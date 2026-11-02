"""Combined-equity phase targets across sprint/core1/core2/compound (Ali, 2 Nov 2026, revised).

Ali's final, explicit structure (2 Nov 2026, superseding the earlier $100->$10k->$116.5k plan):

  Phase A: $100     -> $10,000   within 96 hours of going live.
           On hit: alert, STOP trading. Withdraw $9,000. Leave $1,000 to carry into Phase B.
  Phase B: $1,000   -> $40,000   -- shares a combined 10-day clock with Phase C (5 days
           "pacing" each, but the real deadline is 10 days for B+C together: if B finishes
           early, C gets whatever is left of the 10 days).
           On hit: alert, STOP trading. Withdraw $37,000. Leave $3,000 to carry into Phase C.
  Phase C: $3,000   -> $40,000   -- the second half of that same combined 10-day B+C window.
           On hit: alert, STOP trading. Withdraw $37,000. Leave $3,000 to carry into Phase D.
  Phase D: $3,000   -> $40,000   -- its OWN separate 15-day window, starting fresh once B+C's
           withdrawals are both done (not counted against the B+C 10 days).
           On hit: alert, STOP trading. Withdraw $37,000.

This is a single combined-equity watcher across sprint+core1+core2+compound together, with a
hard stop (no book takes a new trade once the current phase's target is hit) and an explicit,
manual cashout/reseed handoff between phases -- this module tracks and alerts, it never moves
money or rewrites .env itself. Ali withdraws the stated cashout amount, sets each book's
*_SEED_USD in .env to its share of the reseed total, and calls start_phase() for the next phase
to begin tracking it. Same "explicit, manual, never automatic" convention books.force_reseed()
already uses for money-affecting actions.

Works identically in paper mode (now) and live mode (real funds, later) -- it only reads
books.equity()/books.can_open(), which already behave uniformly in both modes. Nothing here is
paper-specific.

B+C combined-window rule (Ali, confirmed): the 10-day clock starts when Phase B starts and does
NOT reset when B hits and C begins -- it keeps counting through both. Phase D's 15-day window is
separate and starts fresh when Phase D starts (after C's cashout is done).

Also includes a max-drawdown circuit breaker Ali did NOT explicitly ask for but that this
session's audit flagged as the missing piece: compound alone has busted to $0 twice in ~36h of
real paper operation. A "trades needed" projection that assumes smooth compounding is not what
actually happens -- real losing streaks happen. Without a breaker, a bad stretch can erase most
of the progress toward a target with nobody watching in real time. The breaker does NOT force-
close positions (a real position may still recover) -- it only PAUSES new entries and raises a
visible, logged alert the moment combined equity falls too far below its own peak-so-far, so a
drawdown is caught and flagged immediately instead of discovered hours later.

State is kept in a single state.py key (see PHASE_KEY below), fully independent of BUST_KEY/
CASHOUT_KEY so it never collides with the existing per-book mechanisms -- those keep working
exactly as before, unaffected by anything in this file.
"""
from __future__ import annotations
import time
from typing import Optional

import state
import executor.books as books

REAL_BOOKS = ("sprint", "core1", "core2", "compound")

PHASE_KEY = "phase_target_v1"

# Each phase: target_usd, cashout_usd, reseed_usd, time allowed, drawdown_pause_pct, next phase.
# drawdown_pause_pct: pause new entries if combined equity falls below this fraction of its own
# peak-so-far THIS phase (e.g. 0.50 = paused if equity drops to <=50% of the highest point reached
# since the phase started) -- a real circuit breaker, not a projection.
#
# window_group: phases sharing a window_group share one clock -- the window starts when the FIRST
# phase in the group starts and does not reset when the next phase in the group begins. A phase
# with no group (or alone in one) gets its own fresh clock starting at its own start_phase() call.
PHASES = {
    "A": {"label": "Phase A: $100 -> $10,000 (96h)", "target_usd": 10_000.0,
          "cashout_usd": 9_000.0, "reseed_usd": 1_000.0, "hours_allowed": 96.0,
          "drawdown_pause_pct": 0.50, "next": "B", "window_group": "A"},
    "B": {"label": "Phase B: $1,000 -> $40,000 (shares 10-day window with C)",
          "target_usd": 40_000.0, "cashout_usd": 37_000.0, "reseed_usd": 3_000.0,
          "hours_allowed": 240.0, "drawdown_pause_pct": 0.50, "next": "C", "window_group": "BC"},
    "C": {"label": "Phase C: $3,000 -> $40,000 (shares 10-day window with B)",
          "target_usd": 40_000.0, "cashout_usd": 37_000.0, "reseed_usd": 3_000.0,
          "hours_allowed": 240.0, "drawdown_pause_pct": 0.50, "next": "D", "window_group": "BC"},
    "D": {"label": "Phase D: $3,000 -> $40,000 (own separate 15-day window)",
          "target_usd": 40_000.0, "cashout_usd": 37_000.0, "reseed_usd": None,
          "hours_allowed": 360.0, "drawdown_pause_pct": 0.50, "next": None, "window_group": "D"},
}


def _state() -> dict:
    v = state.get_value(PHASE_KEY)
    return v if isinstance(v, dict) else {"current": None, "started_ts": None, "peak_equity": 0.0,
                                           "paused": False, "events": [],
                                           "window_started_ts": {}}


def _save(d: dict):
    state.set_value(PHASE_KEY, d)


def combined_equity() -> float:
    """Sum of equity() across the 4 real books -- the number Ali's targets are measured against."""
    return round(sum(books.equity(b) for b in REAL_BOOKS), 2)


def start_phase(phase: str, now: Optional[float] = None) -> dict:
    """Explicit, manual start -- never automatic, same convention as books.force_reseed(). Call
    once when Ali says to begin tracking toward a phase target (or right after the previous
    phase's cashout/reseed handoff is done on his end).

    Handles the B+C shared window_group: if the new phase shares a window_group with the phase
    that was just active, the window's start timestamp carries forward unchanged (the clock keeps
    counting through both phases). Any other case (first start, or a phase in a new group) resets
    the window start to `now`.
    """
    now = now if now is not None else time.time()
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}; must be one of {list(PHASES)}")
    d = _state()
    prev_phase = d.get("current")
    prev_group = PHASES[prev_phase]["window_group"] if prev_phase in PHASES else None
    group = PHASES[phase]["window_group"]

    windows = dict(d.get("window_started_ts") or {})
    if group != prev_group or group not in windows:
        windows[group] = now   # fresh clock for this group

    d["current"] = phase
    d["started_ts"] = now
    d["peak_equity"] = combined_equity()
    d["paused"] = False
    d["window_started_ts"] = windows
    d.setdefault("events", []).append({"ts": now, "type": "phase_started", "phase": phase,
                                        "start_equity": d["peak_equity"]})
    _save(d)
    return d


def _window_elapsed_hours(d: dict, phase: str, now: float) -> float:
    group = PHASES[phase]["window_group"]
    windows = d.get("window_started_ts") or {}
    start = windows.get(group)
    if start is None:
        start = d.get("started_ts")
    if start is None:
        start = now
    return (now - start) / 3600.0


def status(now: Optional[float] = None) -> dict:
    """Read-only snapshot: current phase, combined equity, progress, drawdown state, time left.
    Hours are measured against the PHASE'S WINDOW GROUP clock (B and C share one; A and D each
    have their own), not against when that specific phase individually started."""
    now = now if now is not None else time.time()
    d = _state()
    phase = d.get("current")
    if not phase or phase not in PHASES:
        return {"active": False}
    p = PHASES[phase]
    eq = combined_equity()
    peak = max(d.get("peak_equity", 0.0), eq)
    elapsed_h = _window_elapsed_hours(d, phase, now)
    allowed_h = p["hours_allowed"]
    drawdown_floor = peak * p["drawdown_pause_pct"] if peak > 0 else 0.0
    return {
        "active": True, "phase": phase, "label": p["label"],
        "combined_equity": eq, "target_usd": p["target_usd"],
        "pct_of_target": round(100.0 * eq / p["target_usd"], 2) if p["target_usd"] else None,
        "target_hit": eq >= p["target_usd"],
        "peak_equity": peak, "drawdown_floor": round(drawdown_floor, 2),
        "in_drawdown_pause": d.get("paused", False),
        "elapsed_hours": round(elapsed_h, 1), "allowed_hours": allowed_h,
        "hours_left": round(max(0.0, allowed_h - elapsed_h), 1) if allowed_h else None,
        "window_group": p["window_group"], "next_phase": p["next"],
        "started_ts": d.get("started_ts"),
    }


def check(now: Optional[float] = None) -> dict:
    """The one function to call every cycle (wired into scheduler.run_poll_fast and
    worker_fast_watch.tick() alongside the existing per-book check_cashout()/check_bust() calls).
    Updates peak-equity tracking, fires the drawdown pause/resume, and fires the target-hit
    cashout+alert+next-phase-handoff. Returns the current status dict plus any events that just
    fired this call (empty list if nothing changed)."""
    now = now if now is not None else time.time()
    d = _state()
    phase = d.get("current")
    if not phase or phase not in PHASES:
        return {"active": False, "fired": []}
    p = PHASES[phase]
    eq = combined_equity()
    fired = []

    peak = max(d.get("peak_equity", 0.0), eq)
    d["peak_equity"] = peak

    # --- drawdown circuit breaker: pause/resume, never force-closes anything ---
    floor = peak * p["drawdown_pause_pct"] if peak > 0 else 0.0
    was_paused = d.get("paused", False)
    if not was_paused and peak > 0 and eq <= floor:
        d["paused"] = True
        ev = {"ts": now, "type": "drawdown_pause", "phase": phase, "equity": eq,
              "peak_equity": peak, "floor": round(floor, 2)}
        d.setdefault("events", []).append(ev)
        fired.append(ev)
    elif was_paused and eq > floor:
        d["paused"] = False
        ev = {"ts": now, "type": "drawdown_resume", "phase": phase, "equity": eq, "floor": round(floor, 2)}
        d.setdefault("events", []).append(ev)
        fired.append(ev)

    # --- target hit: alert, stop new entries (via paused=True), cash out, hand off to next phase ---
    if eq >= p["target_usd"]:
        ev = {"ts": now, "type": "target_hit", "phase": phase, "equity": eq, "target": p["target_usd"]}
        d.setdefault("events", []).append(ev)
        fired.append(ev)
        d["paused"] = True   # stop taking new trades immediately -- Ali's explicit requirement
        cashed = min(p["cashout_usd"], eq) if p.get("cashout_usd") is not None else None
        reseed = p.get("reseed_usd")
        next_phase = p.get("next")
        if cashed is not None:
            # This module tracks and alerts; it does NOT silently move money between books' seeds.
            # Ali withdraws the cashout amount himself, sets each book's *_SEED_USD in .env to its
            # share of the reseed total (if any), and calls start_phase(next) to begin tracking the
            # next phase -- same "explicit, manual, never automatic" convention books.force_reseed()
            # already uses.
            note = (f"Target hit on Phase {phase} -- all new entries paused. Ali: withdraw "
                    f"${cashed:,.0f}, "
                    + (f"set each book's *_SEED_USD in .env to its share of the remaining "
                       f"${reseed:,.0f}, then call start_phase({next_phase!r}) to begin tracking it."
                       if reseed is not None and next_phase
                       else "this is the final phase -- no further reseed/next phase."))
            ev2 = {"ts": now, "type": "cashout_recommended", "phase": phase,
                   "next_phase": next_phase, "cashout_usd": cashed, "reseed_usd": reseed,
                   "note": note}
            d.setdefault("events", []).append(ev2)
            fired.append(ev2)

    _save(d)
    return {**status(now), "fired": fired}


def is_paused(now: Optional[float] = None) -> bool:
    """True if new entries should be refused -- either the drawdown breaker tripped or the phase
    target has already been hit. Call this from books.can_open() alongside its other checks."""
    d = _state()
    return bool(d.get("paused", False)) if d.get("current") in PHASES else False

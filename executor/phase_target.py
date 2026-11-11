"""Combined-equity phase targets across sprint/core1/core2/compound.

STALE/SUPERSEDED (11 Nov 2026, Claude): this module's PHASES dict (A/B/C/D, $10k/$40k/$40k/$40k)
is an OLDER, separate 4-phase structure from 2 Nov 2026, never armed (phase_target_v1 state has
never been initialized -- start_phase() is not called anywhere in the live codebase, confirmed by
grep), so is_paused() always returns False and this module currently affects nothing live. The
REAL, actually-tracked 5-phase plan (Ali, "Curve C", 7-8 Nov 2026) lives in executor/books.py as
PHASE_DEFS + the milestone_ladder_v2 state key -- confirmed live and actively tracking real
progress (Phase 1 past the $1,200 rung as of 11 Nov 2026). This module's structure below has been
updated to match that real plan for consistency and to remove a stale reference that caused
confusion earlier today -- if this module is ever armed via start_phase(), it should track the
SAME real targets PHASE_DEFS already uses live, not the old 4-phase numbers.

Ali's confirmed 5-phase structure (7-8 Nov 2026 "Curve C", reseed/cashout amounts per PHASE_DEFS
in executor/books.py; Phase 5 split confirmed 11 Nov 2026):

  Phase 1: $100     -> $10,000    On hit: withdraw $8,500. Leave $1,500 for Phase 2.
  Phase 2: $1,500    -> $12,000    On hit: withdraw $10,000. Leave $2,000 for Phase 3.
  Phase 3: $2,000    -> $12,000    On hit: withdraw $10,000. Leave $2,000 for Phase 4.
  Phase 4: $2,000    -> $120,000   On hit: withdraw $115,000. Leave $5,000 for Phase 5.
  Phase 5: $5,000    -> $1,000,000 On hit: withdraw $975,000. Leave $25,000 running.

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

Also includes a max-drawdown circuit breaker Ali did NOT explicitly ask for but that an earlier
session's audit flagged as a missing piece: compound alone has busted to $0 twice in ~36h of
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
# 11 Nov 2026 (Claude): rewritten to match the REAL confirmed 5-phase Curve C plan (see
# executor/books.py PHASE_DEFS, which is the live, actually-tracked version of these same
# targets). hours_allowed/drawdown_pause_pct/window_group kept as placeholders consistent with
# this module's original design; not yet confirmed against PHASE_DEFS, which has no time-window
# concept at all -- if this module is ever armed, those fields should be reviewed against what
# Ali actually wants for time limits, separately from the target/cashout/reseed amounts below
# which ARE the confirmed real numbers.
PHASES = {
    "1": {"label": "Phase 1: $100 -> $10,000", "target_usd": 10_000.0,
          "cashout_usd": 8_500.0, "reseed_usd": 1_500.0, "hours_allowed": 96.0,
          "drawdown_pause_pct": 0.50, "next": "2", "window_group": "1"},
    "2": {"label": "Phase 2: $1,500 -> $12,000", "target_usd": 12_000.0,
          "cashout_usd": 10_000.0, "reseed_usd": 2_000.0, "hours_allowed": 240.0,
          "drawdown_pause_pct": 0.50, "next": "3", "window_group": "23"},
    "3": {"label": "Phase 3: $2,000 -> $12,000", "target_usd": 12_000.0,
          "cashout_usd": 10_000.0, "reseed_usd": 2_000.0, "hours_allowed": 240.0,
          "drawdown_pause_pct": 0.50, "next": "4", "window_group": "23"},
    "4": {"label": "Phase 4: $2,000 -> $120,000", "target_usd": 120_000.0,
          "cashout_usd": 115_000.0, "reseed_usd": 5_000.0, "hours_allowed": 360.0,
          "drawdown_pause_pct": 0.50, "next": "5", "window_group": "4"},
    "5": {"label": "Phase 5: $5,000 -> $1,000,000", "target_usd": 1_000_000.0,
          "cashout_usd": 975_000.0, "reseed_usd": 25_000.0, "hours_allowed": None,
          "drawdown_pause_pct": 0.50, "next": None, "window_group": "5"},
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
    # TEMP DEBUG (6 Nov 2026, Ali; widened 10 Nov 2026, Claude): instrumented to find the exact
    # book(s) producing wrong reads during the recurring equity-flicker investigation. ORIGINAL
    # trigger was total<300 only -- missed a real incident on 10 Nov (equity misread as $303.27,
    # just above the <300 cutoff, during a drawdown_pause/resume pair 60s apart with ZERO real
    # realized-P&L change in any book across the same window -- confirmed directly, not a real
    # trading event). WIDENED to also log on every actual pause/resume firing itself (regardless
    # of the total<300 heuristic), via the new `force` param the pause/resume call sites below
    # now pass -- so the NEXT occurrence of this bug is captured with full per-book detail
    # instead of being investigated blind again. Still silent on ordinary calls (force=False,
    # old <300 heuristic unchanged) to avoid spamming the log every poll cycle. Remove once the
    # root cause is confirmed and fixed.
    per_book = {b: books.equity(b) for b in REAL_BOOKS}
    total = round(sum(per_book.values()), 2)
    if total < 300 or _EQUITY_DEBUG_FORCE.get("on"):
        try:
            import time as _time, os as _os
            log_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs", "equity_debug.log")
            with open(log_path, "a") as _f:
                _f.write(f"{_time.time()} pid={_os.getpid()} total={total} per_book={per_book} forced={_EQUITY_DEBUG_FORCE.get('on', False)}\n")
        except Exception:
            pass
    return total


# 10 Nov 2026 (Claude): simple in-process flag combined_equity() checks above. Set True by the
# pause/resume mutation below for the single call it makes right before logging that event, so
# the per-book breakdown lands in the log for exactly the call that triggered a real pause/resume
# -- not every call (would spam the log every ~6s poll cycle for no benefit). Reset to False
# immediately after. Module-level dict (not a bare bool) only so a nested/re-entrant call during
# the same brief window still sees the same mutable object cleanly.
_EQUITY_DEBUG_FORCE = {"on": False}


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
    fired this call (empty list if nothing changed).

    6 Nov 2026 (Ali, found live): scheduler.py's run_poll_fast() and worker_fast_watch.py's tick()
    both call this independently -- separate OS processes on Windows, not threads -- and the old
    version here did a plain _state() read followed by a separate _save() write, with no lock held
    across the two. Confirmed live in Ali's own logs/fast_watch.log: 74 "PermissionError: [WinError
    5] Access is denied" crashes on exactly this function's _save() call over several hours, and
    separately reproduced in isolation as a silent lost-update race (two processes reading the same
    old value, both computing a new one, the second write clobbering the first with zero error --
    confirmed via a controlled test: 6 threads x100 concurrent increments landed at 100, not 600).
    Rebuilt on state.atomic_update(), which holds one file lock across the full read+modify+write,
    closing both failure modes -- verified in isolation (thread-level and real separate-OS-process
    level) before being wired in here."""
    now = now if now is not None else time.time()
    fired = []

    def _mutate(d):
        d = d if isinstance(d, dict) else {"current": None, "started_ts": None, "peak_equity": 0.0,
                                            "paused": False, "events": [], "window_started_ts": {}}
        phase = d.get("current")
        if not phase or phase not in PHASES:
            return d
        p = PHASES[phase]
        _EQUITY_DEBUG_FORCE["on"] = True
        try:
            eq = combined_equity()
        finally:
            _EQUITY_DEBUG_FORCE["on"] = False

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
        return d

    d = state.get_value(PHASE_KEY)
    if not (isinstance(d, dict) and d.get("current") in PHASES):
        return {"active": False, "fired": []}

    state.atomic_update(PHASE_KEY, _mutate)
    return {**status(now), "fired": fired}


def is_paused(now: Optional[float] = None) -> bool:
    """True if new entries should be refused -- either the drawdown breaker tripped or the phase
    target has already been hit. Call this from books.can_open() alongside its other checks."""
    d = _state()
    return bool(d.get("paused", False)) if d.get("current") in PHASES else False

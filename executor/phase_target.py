"""Combined-equity phase targets across sprint/core1/core2/compound.

STALE/SUPERSEDED (11 Nov 2026, Claude): this module's PHASES dict is a separate structure,
never armed (phase_target_v1 state has never been initialized -- start_phase() is not called
anywhere in the live codebase, confirmed by grep), so is_paused() always returns False and this
module currently affects nothing live. The REAL, actually-tracked phase plan lives in
executor/books.py as PHASE_DEFS + the milestone_ladder_v2 state key -- confirmed live and
actively tracking real progress. This module's structure below has been updated to match that
real plan for consistency -- if this module is ever armed via start_phase(), it should track the
SAME real targets PHASE_DEFS already uses live.

Ali's confirmed 4-phase structure (16 Nov 2026, supersedes the earlier 5-phase "Curve C" plan
wholesale; reseed/cashout amounts per PHASE_DEFS in executor/books.py):

  Phase 1: $115   -> $6,000      On hit: withdraw $4,500 (fund S1b, all parts). Leave $1,500 for Phase 2.
  Phase 2: $1,500 -> $13,000     On hit: withdraw $11,000 (pay Dec liability). Leave $2,000 for Phase 3.
  Phase 3: $2,000 -> $105,000    On hit: withdraw $100,000 (main debt payment). Leave $5,000 for Phase 4.
  Phase 4: $5,000 -> $1,000,000  On hit: withdraw $985,000 (financial freedom). Leave $15,000 running.

Each phase's own ladder-rung partial banking is a risk-management side effect of normal trading,
never counted as a separate "income" toward that phase's trigger -- the trigger is always
checked against combined equity, which already includes any ladder-banked amount once.

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

REAL_BOOKS = ("sprint", "core1", "core2", "compound")  # kept for backward compat only --
# combined_equity() below no longer sums this whole tuple for any real phase; see its own
# docstring (16 Nov 2026 fix) for the real per-phase book sets it actually uses now.

PHASE_KEY = "phase_target_v1"

# Each phase: target_usd, cashout_usd, reseed_usd, time allowed, drawdown_pause_pct, next phase.
# drawdown_pause_pct: pause new entries if combined equity falls below this fraction of its own
# peak-so-far THIS phase (e.g. 0.50 = paused if equity drops to <=50% of the highest point reached
# since the phase started) -- a real circuit breaker, not a projection.
#
# window_group: phases sharing a window_group share one clock -- the window starts when the FIRST
# phase in the group starts and does not reset when the next phase in the group begins. A phase
# with no group (or alone in one) gets its own fresh clock starting at its own start_phase() call.
# 16 Nov 2026 (Claude): rewritten to match the REAL confirmed 4-phase structure (see
# executor/books.py PHASE_DEFS, which is the live, actually-tracked version of these same
# targets). Each phase here gets its own independent window_group (1/2/3/4 -- no two phases
# share a clock in the new 4-phase structure, unlike the old 5-phase plan where phases 2+3 once
# shared one). hours_allowed/drawdown_pause_pct kept as placeholders consistent with this
# module's original design; not yet confirmed against PHASE_DEFS, which has no time-window
# concept at all -- if this module is ever armed, those fields should be reviewed against what
# Ali actually wants for time limits, separately from the target/cashout/reseed amounts below
# which ARE the confirmed real numbers.
PHASES = {
    "1": {"label": "Phase 1: $115 -> $6,000", "target_usd": 6_000.0,
          "cashout_usd": 4_500.0, "reseed_usd": 1_500.0, "hours_allowed": 96.0,
          "drawdown_pause_pct": 0.50, "next": "2", "window_group": "1"},
    "2": {"label": "Phase 2: $1,500 -> $13,000", "target_usd": 13_000.0,
          "cashout_usd": 11_000.0, "reseed_usd": 2_000.0, "hours_allowed": 240.0,
          "drawdown_pause_pct": 0.50, "next": "3", "window_group": "23"},
    "3": {"label": "Phase 3: $2,000 -> $105,000", "target_usd": 105_000.0,
          "cashout_usd": 100_000.0, "reseed_usd": 5_000.0, "hours_allowed": 360.0,
          "drawdown_pause_pct": 0.50, "next": "4", "window_group": "3"},
    "4": {"label": "Phase 4: $5,000 -> $1,000,000", "target_usd": 1_000_000.0,
          "cashout_usd": 985_000.0, "reseed_usd": 15_000.0, "hours_allowed": None,
          "drawdown_pause_pct": 0.50, "next": None, "window_group": "4"},
}


def _state() -> dict:
    v = state.get_value(PHASE_KEY)
    return v if isinstance(v, dict) else {"current": None, "started_ts": None, "peak_equity": 0.0,
                                           "paused": False, "events": [],
                                           "window_started_ts": {}}


def _save(d: dict):
    state.set_value(PHASE_KEY, d)


def combined_equity(phase: Optional[str] = None) -> float:
    """The number Ali's phase targets are measured against.

    16 Nov 2026 (Ali, arming this module for real, then immediately caught a second real bug
    the same night: "the combined equity... has to be fixed right now"): TWO separate problems
    fixed together, because fixing one without the other would have just traded one wrong
    number for a different wrong number.

    Problem 1 (first fix tonight): compound's slice must use REAL equity (books.equity_real())
    once it's live-armed, not books.equity("compound") (PAPER, zero connection to the real
    wallet) -- same "book_id == 'compound' and SC_CASCADE_FUNDER in _live_books_from_env()"
    condition every other real-money gate in this codebase already uses.

    Problem 2 (THIS fix): this function was unconditionally summing REAL_BOOKS = (sprint,
    core1, core2, compound) for every phase -- but that is not what the live milestone-ladder
    engine in books.py (check_milestone_ladder(), the mechanism that actually fires phase
    cash-outs) has EVER used. books.py's own real behavior, confirmed by reading its code
    directly: during PHASE 1, only compound counts at all (PHASE1_SOLO_BOOK, 100% split --
    sprint/core1/core2 are excluded entirely, not just paper-vs-real, EXCLUDED); from phase 2
    onward, only compound+sprint count (PHASE_ENGINE_BOOKS) -- core1/core2 NEVER count toward
    any phase target, confirmed by books.py's own SC_CASCADE_KEY-area comment: "Core1/core2
    then run fully independently of the phase-target engine... stays scoped to
    PHASE_ENGINE_BOOKS = compound+sprint only."

    Before this fix, arming phase 1 showed a $476.62 "combined equity" that was really $10.05
    real compound + ~$466.57 of PAPER sprint/core1/core2 blended in -- not just the wrong
    real/paper split, but core1/core2 contributing at all when books.py's own real mechanism
    has never counted them during ANY phase. That blended number made the drawdown breaker
    protect a mix of real money and three paper numbers simultaneously, instead of protecting
    compound's actual real equity during phase 1 the way it's supposed to.

    Now routes to the SAME book set books.py's check_milestone_ladder() uses for this exact
    phase: phase "1" -> compound alone (real once live-armed, same as before); any other phase
    (2/3/4, or None when the caller has no phase context) -> sprint+compound only, each counted
    real or paper per its own live-armed status, core1/core2 never included. phase=None keeps
    a book-set choice for callers with no phase context (e.g. a direct, ad-hoc check outside
    the normal check()/status()/start_phase() flow) -- defaults to the phase-2-onward book set
    (sprint+compound) since that's PHASE_ENGINE_BOOKS' own long-standing default elsewhere in
    books.py, not the old, wrong 4-book blend."""
    from executor import books as _books_mod
    if phase == "1":
        book_set = (_books_mod.PHASE1_SOLO_BOOK,)   # compound alone, matching check_milestone_ladder()
    else:
        book_set = _books_mod.PHASE_ENGINE_BOOKS     # (sprint, compound) -- core1/core2 never included

    per_book = {}
    for b in book_set:
        if b == "compound" and _books_mod.SC_CASCADE_FUNDER in _books_mod._live_books_from_env():
            per_book[b] = books.equity_real()
        else:
            per_book[b] = books.equity(b)
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

    Handles a shared window_group generically: if the new phase shares a window_group with the
    phase that was just active, the window's start timestamp carries forward unchanged (the clock
    keeps counting through both phases). Any other case (first start, or a phase in a new group)
    resets the window start to `now`. (None of the current 4 phases share a group with each other,
    but the mechanism stays generic in case that changes again.)
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
    d["peak_equity"] = combined_equity(phase)
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
    Hours are measured against the PHASE'S WINDOW GROUP clock (each of the 4 phases has its own
    independent window_group now), not against when that specific phase individually started."""
    now = now if now is not None else time.time()
    d = _state()
    phase = d.get("current")
    if not phase or phase not in PHASES:
        return {"active": False}
    p = PHASES[phase]
    eq = combined_equity(phase)
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
            eq = combined_equity(phase)
        finally:
            _EQUITY_DEBUG_FORCE["on"] = False

        peak = max(d.get("peak_equity", 0.0), eq)
        d["peak_equity"] = peak

        # --- drawdown circuit breaker: pause/resume, never force-closes anything ---
        #
        # 16 Nov 2026 (Ali, arming this breaker for real): combined_equity()'s compound slice
        # (once live-armed) comes from books.equity_real(), which -- like every other real-money
        # caller in this codebase -- fails SAFE by returning $0 for a chain whose live balance
        # can't be read right now (RPC down, etc.), rather than erroring. That fail-$0 behavior
        # is correct for buy sizing (refuse to size off an unknown balance), but would be WRONG
        # here: a transient RPC failure would look exactly like "real equity just crashed to
        # $0," falsely tripping the breaker and pausing all real trading on a phantom crash. So
        # this pause/resume decision additionally confirms the REAL read actually succeeded this
        # cycle (get_total_real_wallet_usd_or_none() returns None on any chain-read failure)
        # before acting on it -- on a failed read, this cycle skips the pause/resume decision
        # entirely (peak_equity is still updated above from whatever eq combined_equity()
        # returned, which is harmless either way since peak is a max() and a lower/false reading
        # can never corrupt it upward) and simply tries again next cycle. Only checked when
        # compound is actually live-armed -- while compound stays paper, combined_equity() has
        # no real-chain dependency at all and this guard is a complete no-op.
        _real_read_ok = True
        from executor import books as _books_mod_local
        if _books_mod_local.SC_CASCADE_FUNDER in _books_mod_local._live_books_from_env():
            from executor import swap_executor as _se
            _real_read_ok = _se.get_total_real_wallet_usd_or_none() is not None

        floor = peak * p["drawdown_pause_pct"] if peak > 0 else 0.0
        was_paused = d.get("paused", False)
        if _real_read_ok and not was_paused and peak > 0 and eq <= floor:
            d["paused"] = True
            ev = {"ts": now, "type": "drawdown_pause", "phase": phase, "equity": eq,
                  "peak_equity": peak, "floor": round(floor, 2)}
            d.setdefault("events", []).append(ev)
            fired.append(ev)
        elif _real_read_ok and was_paused and eq > floor:
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

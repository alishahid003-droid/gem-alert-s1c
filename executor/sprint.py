"""
5-day sprint (Oct 1 2026).

Ali: "build a five-day sprint module starting with $100, the best possible
attempt logic... whatever has had a near-win ratio every time."

What it is: the compound scalper (executor/compound_scalper.py) switched
into sprint mode with SPRINT_MODE=true. Every exit rule is the one the
backtests scored best (stop -55% / take 60% at 1.5x / 35% trail / 3 h) --
nothing untested is invented for the sprint. The sprint changes WHICH trades
are taken and HOW MUCH compounds:

  entries   only the highest-evidence setups, one at a time:
            - band B+ with >= 60% real data behind the score (no "low data"
              coins), AND the coin is moving the right way right now
              (5-min change >= -5%, 1-h change > 0) -- confluence;
            - or a Layer 15 moonshot qualifier (escape velocity: a real,
              growing crowd), still band B+ for real money;
  size      90% of the pool per trade (max compounding), $100 seed;
  breakers  3 losses in a row -> 90-min cool-off, then resume automatically
            (a 5-day window can't wait for a manual restart) -- unless the
            pool is below 25% of the seed ($25), then it stays stopped;
            75% session loss -> stopped for good;
  window    120 hours from the first entry signal, up to 400 trades;
  start     automatic once SPRINT_MODE=true (that switch is the decision).

Honest expectation, printed in the daily Telegram summary: every trade and
failure is also sent to Telegram as it happens (executor/trade_ops.py).
"""
import os
import time
from typing import Optional, Tuple


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def entry_ok(ctx: Optional[dict], moonshot: bool = False) -> Tuple[bool, str]:
    """Sprint confluence filter on top of the normal band/guard checks.
    Unknown values don't block, except real-data coverage, which must be
    known and >= SPRINT_MIN_COVERAGE for a score-driven entry."""
    ctx = ctx or {}
    if ctx.get("lane") == "momentum":
        return lane_allowed()
    if moonshot or ctx.get("moonshot"):
        return True, "moonshot qualifier (escape velocity)"
    cov = ctx.get("coverage")
    if cov is None or cov < _f("SPRINT_MIN_COVERAGE", 0.6):
        return False, f"sprint needs >= {_f('SPRINT_MIN_COVERAGE', 0.6):.0%} real data behind the score (has {cov if cov is None else f'{cov:.0%}'})"
    m5, h1 = ctx.get("change_m5"), ctx.get("change_h1")
    if m5 is not None and m5 < _f("SPRINT_MIN_M5_PCT", -5):
        return False, f"sprint: falling right now ({m5:+.0f}% in 5 min)"
    if h1 is not None and h1 <= _f("SPRINT_MIN_H1_PCT", 0):
        return False, f"sprint: no upward momentum ({h1:+.0f}% in 1 h)"
    return True, "sprint confluence: real-data band B+ with live momentum"


def lane_allowed() -> Tuple[bool, str]:
    """The momentum lane pauses itself if its real record goes bad: after
    LANE_MIN_TRADES_FOR_VERDICT (6) real lane exits, it needs a win rate of
    at least LANE_MIN_WIN_RATE (50%) to keep trading."""
    if (os.environ.get("SPRINT_MOMENTUM", "true") or "").strip().lower() == "false":
        return False, "momentum lane switched off (SPRINT_MOMENTUM=false)"
    from executor import compound_scalper as cs
    trades = [t for t in cs._get_pool().get("trades", []) if t.get("profile") == "quick"
              and t.get("exit_type") != "take_profit_partial"]
    n = len(trades)
    if n >= _f("LANE_MIN_TRADES_FOR_VERDICT", 6):
        wins = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
        if wins / n < _f("LANE_MIN_WIN_RATE", 0.5):
            return False, f"momentum lane paused: {wins}/{n} real wins (needs {_f('LANE_MIN_WIN_RATE', 0.5):.0%})"
    return True, "momentum lane: live buying burst"


def maybe_resume(pool: dict, now: Optional[float] = None) -> dict:
    """Clears a losing-streak trip after SPRINT_COOLDOWN_MINUTES if the pool
    still holds at least SPRINT_MIN_POOL_FRACTION of its seed."""
    from executor import compound_scalper as cs
    now = now if now is not None else time.time()
    if not pool.get("tripped") or pool.get("tripped_kind") != "losing_streak":
        return pool
    if now - (pool.get("tripped_ts") or now) < _f("SPRINT_COOLDOWN_MINUTES", 90) * 60:
        return pool
    seed = pool.get("seed_usd") or 0
    if (pool.get("balance_usd") or 0) < seed * _f("SPRINT_MIN_POOL_FRACTION", 0.25):
        return pool
    pool["tripped"] = False
    pool["tripped_reason"] = None
    pool["consecutive_losses"] = 0
    pool.setdefault("resumes", []).append(now)
    cs._save_pool(pool)
    return pool


def status_line() -> Optional[str]:
    from executor import compound_scalper as cs
    if not cs.sprint_mode():
        return None
    st = cs.status()
    if not st.get("started"):
        return "Sprint: armed, waiting for the first qualifying setup"
    pool = cs._get_pool()
    day = (time.time() - (pool.get("session_start_ts") or time.time())) / 86400 + 1
    trades = pool.get("trades", [])
    wins = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
    return (f"Sprint day {min(day, 5):.0f}/5: pool ${st.get('balance_usd') or 0:,.2f} "
            f"({(st.get('multiple_of_seed') or 0):.2f}x seed), {len(trades)} exits, {wins} wins"
            + (f", PAUSED: {st.get('tripped_reason')}" if st.get("tripped") else ""))

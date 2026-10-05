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
  window    168 hours (one week) from the first entry signal, up to 400 trades;
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
    from executor import paper_ledger
    ok, why = paper_ledger.signal_allowed("momentum_lane")
    if not ok:
        return False, f"momentum lane paused by its paper record: {why}"
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
    total = (pool.get("banked_usd") or 0) + (st.get("balance_usd") or 0)
    return (f"Sprint day {min(day, 7):.0f}/7: pool ${st.get('balance_usd') or 0:,.2f}, "
            f"banked ${pool.get('banked_usd') or 0:,.2f}, total ${total:,.2f} of ${target_usd():,.0f} target "
            f"({100 * total / target_usd():.1f}%), {len(trades)} exits, {wins} wins"
            + (f", PAUSED: {st.get('tripped_reason')}" if st.get("tripped") else ""))


def target_usd() -> float:
    """Oct 6 2026 (Ali: one-week attempt on $90k). The sprint is finished when
    banked profit + the live pool reach this total; the milestone locks below
    only protect partial wins on the way."""
    return _f("SPRINT_TARGET_USD", 90000.0)


def milestones() -> list:
    """[(target_usd, keep_usd)] from SPRINT_MILESTONES (default Ali's plan,
    Oct 1 2026: "$100 -> $3,500, extract $3,000; $500 -> $20,000, extract
    $18,500; $1,500 -> $75,000"). keep 0 on the last one = target reached,
    sprint stops and everything is banked."""
    raw = os.environ.get("SPRINT_MILESTONES", "3500:1500,20000:5000")
    out = []
    for part in raw.split(","):
        try:
            t, k = part.split(":")
            out.append((float(t), float(k)))
        except ValueError:
            continue
    return sorted(out)


def apply_milestones(pool: dict, now: Optional[float] = None) -> dict:
    """Profit lock: when the (flat) pool reaches a milestone, everything above
    its keep amount is BANKED -- it stays in the wallet but the sprint never
    trades it again (withdraw it to be fully safe). Only while no position is
    open, so nothing is sold to do it."""
    from executor import compound_scalper as cs
    if pool.get("open_position") is not None:
        return pool
    hit = set(pool.get("milestones_hit") or [])
    changed = False
    for target, keep in milestones():
        if str(target) in hit or (pool.get("balance_usd") or 0) < target:
            continue
        bal = pool.get("balance_usd") or 0
        banked = round(bal - keep, 2)
        pool["banked_usd"] = round((pool.get("banked_usd") or 0) + banked, 2)
        pool["balance_usd"] = keep
        hit.add(str(target))
        pool.setdefault("milestone_log", []).append({"target": target, "banked": banked, "kept": keep,
                                                      "ts": now or time.time()})
        if keep <= 0:
            pool["tripped"] = True
            pool["tripped_kind"] = "target_reached"
            pool["tripped_reason"] = f"final target ${target:,.0f} reached -- everything banked"
        changed = True
        try:
            from executor.trade_ops import _send
            _send(f"🏁 SPRINT MILESTONE ${target:,.0f} reached: banked ${banked:,.2f}, "
                  f"trading on with ${keep:,.2f}. Total banked ${pool['banked_usd']:,.2f}.")
        except Exception:  # noqa: BLE001
            pass
    total = (pool.get("banked_usd") or 0) + (pool.get("balance_usd") or 0)
    if total >= target_usd() and not pool.get("tripped"):
        pool["banked_usd"] = round(total, 2)
        pool["balance_usd"] = 0.0
        pool["tripped"] = True
        pool["tripped_kind"] = "target_reached"
        pool["tripped_reason"] = f"sprint target ${target_usd():,.0f} reached (banked + pool ${total:,.2f})"
        changed = True
        try:
            from executor.trade_ops import _send
            _send(f"🏆 SPRINT TARGET ${target_usd():,.0f} REACHED: ${total:,.2f} banked. Sprint stopped.")
        except Exception:  # noqa: BLE001
            pass
    if changed:
        pool["milestones_hit"] = sorted(hit)
        cs._save_pool(pool)
    return pool

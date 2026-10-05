"""
The two campaigns (Oct 6 2026, Ali: "a quick sprint of a week on $90k and a
3-6 month attempt on $1M", $100 to each).

  SPRINT   = the compound scalper in sprint mode (executor/sprint.py).
             $100 seed, 168 h window, finishes when banked + pool >= $90k.
  MARATHON = the moonshot module on the wallet's own equity (stake ladder in
             executor/entrypoint.py), tracked here against a straight
             compounding path from the starting equity to $1M over
             MARATHON_DAYS (default 150 = 5 months; 90-180 is the 3-6 month
             range).

This module only MEASURES the marathon (and states both targets); it never
trades. The pace line says whether equity is ahead of or behind the geometric
path -- a gauge, not a promise: the path is the growth the target needs, not
growth the strategy has shown it can produce.
"""
import math
import os
import time
from typing import Optional

import state

MARATHON_KEY = "campaign:marathon"


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def marathon_target_usd() -> float:
    return _f("MARATHON_TARGET_USD", 1_000_000.0)


def marathon_days() -> float:
    return _f("MARATHON_DAYS", 150.0)


def marathon_status(equity: Optional[float] = None, now: Optional[float] = None) -> dict:
    """Start is recorded the first time this runs with equity > 0."""
    now = now if now is not None else time.time()
    if equity is None:
        from executor.entrypoint import bankroll_equity
        equity = bankroll_equity()
    rec = state.get_value(MARATHON_KEY)
    if not isinstance(rec, dict) or not rec.get("start_equity"):
        if equity and equity > 0:
            rec = {"start_ts": now, "start_equity": float(equity)}
            state.set_value(MARATHON_KEY, rec)
        else:
            return {"started": False, "target": marathon_target_usd(), "days": marathon_days()}
    start_eq, start_ts = rec["start_equity"], rec["start_ts"]
    target, days = marathon_target_usd(), marathon_days()
    elapsed = max(0.0, (now - start_ts) / 86400.0)
    frac = min(1.0, elapsed / days)
    path_now = start_eq * (target / start_eq) ** frac
    need_per_day = (target / max(equity, 1e-9)) ** (1.0 / max(days - elapsed, 1.0)) - 1.0 if equity > 0 else None
    return {
        "started": True, "target": target, "days": days, "elapsed_days": round(elapsed, 1),
        "start_equity": start_eq, "equity": round(float(equity), 2), "path_now": round(path_now, 2),
        "on_pace": equity >= path_now, "pct_of_target": round(100.0 * equity / target, 3),
        "needed_daily_growth_pct": round(100.0 * need_per_day, 2) if need_per_day is not None else None,
        "days_left": round(max(0.0, days - elapsed), 1),
    }


def marathon_line(equity: Optional[float] = None, now: Optional[float] = None) -> str:
    st = marathon_status(equity, now)
    if not st.get("started"):
        return f"Marathon: waiting for wallet equity (target ${st['target']:,.0f} in {st['days']:.0f} days)"
    return (f"Marathon day {st['elapsed_days']:.0f}/{st['days']:.0f}: equity ${st['equity']:,.2f} "
            f"({st['pct_of_target']:.3f}% of ${st['target']:,.0f}), "
            f"{'ahead of' if st['on_pace'] else 'behind'} the target path (${st['path_now']:,.2f}), "
            f"needs +{st['needed_daily_growth_pct']}%/day from here")

"""Router: decides, once, which book a coin belongs to, so Own Analysis and the
Radar never double-highlight (Ali, Oct 12 2026: "the coin should present into
these modules automatically rather than first highlighting in the own analysis
system and then going into that").

Pure function, no network, no state. Lanes:
  core2    earliest / smallest-cap moonshot (100x+ potential)      -> Core 2 book
  core1    mid-cap catalyst or momentum moonshot (10x-50x)          -> Core 1 book
  compound 2x-13x swings, quick momentum, listing-ladder pops       -> Compound book
  watch    fits nowhere: stays on the Own Analysis watch-list

Input dict keys (all optional): mcap, liquidity, age_min, band, score,
moonshot_ok (Layer 15 escape-velocity screen passed), moonshot_score,
kind (radar: ladder|news|launchpad|conviction|buyback), change_m5, change_h1,
momentum (bool), blocked (bool: entry guard / rug filter said no), coverage.
"""
import os
from typing import Dict, Optional

LANES = ("core1", "core2", "compound", "watch")


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def route(c: dict) -> Dict:
    """-> {"lane": ..., "why": str, "potential": "100x+"|"10x-50x"|"2x-13x"|"-"}"""
    mcap = c.get("mcap")
    liq = c.get("liquidity")
    kind = c.get("kind")
    band = c.get("band")
    if c.get("blocked"):
        return {"lane": "watch", "why": "blocked by entry guard / rug filter", "potential": "-"}
    min_liq = _f("ROUTER_MIN_LIQ_USD", 8_000.0)
    if liq is not None and liq < min_liq:
        return {"lane": "watch", "why": f"liquidity ${liq:,.0f} below ${min_liq:,.0f}", "potential": "-"}

    # Exchange-listing ladder pops are +20-70% plays on big caps: daily-profit book.
    if kind == "ladder":
        return {"lane": "compound", "why": "listing-ladder pop (short swing)", "potential": "2x-13x"}

    moonshot_class = bool(c.get("moonshot_ok")) or kind in ("news", "launchpad", "conviction", "buyback")
    if moonshot_class:
        core2_max = _f("ROUTER_CORE2_MAX_MCAP", 300_000.0)
        core1_max = _f("ROUTER_CORE1_MAX_MCAP", 8_000_000.0)
        age = c.get("age_min")
        if mcap is None:
            return {"lane": "watch", "why": "moonshot-class but mcap unknown", "potential": "-"}
        if mcap <= core2_max and (age is None or age <= _f("ROUTER_CORE2_MAX_AGE_MIN", 24 * 60)):
            return {"lane": "core2", "why": f"early moonshot: mcap ${mcap:,.0f} <= ${core2_max:,.0f}", "potential": "100x+"}
        if mcap <= core1_max:
            return {"lane": "core1", "why": f"moonshot with traction: mcap ${mcap:,.0f}", "potential": "10x-50x"}
        return {"lane": "watch", "why": f"mcap ${mcap:,.0f} too large for a 10x+ play", "potential": "-"}

    if band in ("A", "B") or c.get("momentum"):
        if mcap is not None and mcap > _f("ROUTER_COMPOUND_MAX_MCAP", 30_000_000.0):
            return {"lane": "watch", "why": "mcap too large for a swing", "potential": "-"}
        return {"lane": "compound", "why": f"band {band}" if band in ("A", "B") else "momentum swing", "potential": "2x-13x"}
    return {"lane": "watch", "why": "no moonshot or swing signal", "potential": "-"}

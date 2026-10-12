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
    """-> {"lane": ..., "why": str, "potential": "100x+"|"10x-50x"|"2x-13x"|"-"}

    AGE first, never "already moved" (Ali, Oct 12 2026: entering at $100K-$300K
    is useless). Real-time launch entries are decided by layers/layer17_early_entry.py;
    this router is for coins found by the polling layers, where age is the
    earliness proxy and market cap is only a sanity ceiling."""
    mcap = c.get("mcap")
    liq = c.get("liquidity")
    kind = c.get("kind")
    band = c.get("band")
    age = c.get("age_min")
    h1 = c.get("change_h1")
    if c.get("blocked"):
        return {"lane": "watch", "why": "blocked by entry guard / rug filter", "potential": "-"}
    min_liq = _f("ROUTER_MIN_LIQ_USD", 8_000.0)
    if liq is not None and liq < min_liq:
        return {"lane": "watch", "why": f"liquidity ${liq:,.0f} below ${min_liq:,.0f}", "potential": "-"}
    if h1 is not None and h1 > _f("ROUTER_MAX_RAN_H1_PCT", 300.0):
        return {"lane": "watch", "why": f"already ran +{h1:.0f}% in 1h", "potential": "-"}

    # Exchange-listing ladder pops: short swings on big caps -> daily-profit book.
    if kind == "ladder":
        return {"lane": "compound", "why": "listing-ladder pop (short swing)", "potential": "2x-13x"}

    moonshot_class = bool(c.get("moonshot_ok")) or kind in ("news", "launchpad", "conviction", "buyback")
    c2_age, c1_age = _f("ROUTER_CORE2_MAX_AGE_MIN", 15.0), _f("ROUTER_CORE1_MAX_AGE_MIN", 120.0)
    c2_cap, c1_cap = _f("ROUTER_CORE2_MCAP_CEIL", 200_000.0), _f("ROUTER_CORE1_MCAP_CEIL", 1_000_000.0)
    if moonshot_class:
        if age is not None and age <= c2_age and (mcap is None or mcap <= c2_cap):
            return {"lane": "core2", "why": f"pre-early: {age:.0f} min old", "potential": "100x+"}
        if age is not None and age <= c1_age and (mcap is None or mcap <= c1_cap):
            return {"lane": "core1", "why": f"early: {age:.0f} min old", "potential": "10x-50x"}
        # older than the early windows: no moonshot claim, at most a swing
        if band in ("A", "B") or c.get("moonshot_ok"):
            if age is None or age <= _f("ROUTER_COMPOUND_MAX_AGE_MIN", 180.0):
                return {"lane": "compound", "why": "confirmed but past the early window: swing only", "potential": "2x-13x"}
        return {"lane": "watch", "why": "past the early window", "potential": "-"}

    if band in ("A", "B") or c.get("momentum"):
        if age is not None and age > _f("ROUTER_COMPOUND_MAX_AGE_MIN", 180.0):
            return {"lane": "watch", "why": f"{age:.0f} min old: move already happened", "potential": "-"}
        if mcap is not None and mcap > _f("ROUTER_COMPOUND_MAX_MCAP", 5_000_000.0):
            return {"lane": "watch", "why": "mcap too large for a 2x-13x swing", "potential": "-"}
        return {"lane": "compound", "why": f"band {band}" if band in ("A", "B") else "momentum swing", "potential": "2x-13x"}
    return {"lane": "watch", "why": "no moonshot or swing signal", "potential": "-"}

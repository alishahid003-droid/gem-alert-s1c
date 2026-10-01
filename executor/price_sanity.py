"""
Price-jump sanity filter (Oct 1 2026).

The paper scoreboard showed scalper "wins" averaging +1,800% (trail stops at
+2,355%) -- not real: DexScreener sometimes switches which pair, or
marketCap vs FDV, it reports for a token, so the market cap appears to jump
20x between two reads a minute apart. A fake spike sets a fake peak, and the
trailing stop then "sells" at a fake profit (paper) -- or, with real money,
a real position could be sold on a fake pullback.

Rule: a read more than MAX_JUMP x (default 4x) above or below the previous
accepted read is ignored, up to MAX_SKIPS (3) times in a row; if it
persists, it is accepted as real (a genuine rug or a genuine run).
"""
from typing import Optional

import state

MAX_JUMP = 4.0
MAX_SKIPS = 3


def accept_dict(rec: dict, mcap: Optional[float]) -> bool:
    """Same rule on a caller-owned dict (paper positions keep it inline)."""
    if not mcap:
        return False
    last, skips = rec.get("last_mcap"), rec.get("jump_skips", 0)
    if last and (mcap / last > MAX_JUMP or last / mcap > MAX_JUMP) and skips < MAX_SKIPS:
        rec["jump_skips"] = skips + 1
        return False
    rec["last_mcap"], rec["jump_skips"] = mcap, 0
    return True


def accept(chain: str, token: str, mcap: Optional[float]) -> bool:
    key = f"px:{chain}:{token}"
    rec = state.cache_get(key, 6 * 3600) or {}
    ok = accept_dict(rec, mcap)
    state.cache_set(key, rec)
    return ok

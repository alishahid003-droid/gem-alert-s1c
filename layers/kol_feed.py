"""
Shared KOL-feed fetch for Layers 2 (buy-side convergence) and 9 (sell-side
mirror).

Before this module existed, each layer called MadeOnSol's /kol/feed
separately -- Layer 2 with action=buy, Layer 9 with action=sell -- which is
TWO MadeOnSol calls per chain per poll cycle to read what is very possibly
the exact same underlying feed, just filtered twice. That doubling is a real
chunk of the MadeOnSol call budget (see README's call-budget section), so
this module tries to collapse it to one call, adaptively rather than by
assuming MadeOnSol's docs behavior:

  1. Try ONE call with NO `action` param.
  2. If the trades that come back contain BOTH 'buy' and 'sell' actions,
     that single response covers both layers -- 1 MadeOnSol call instead
     of 2 for this chain this cycle.
  3. If they don't (empty feed, an error, or the endpoint silently defaults
     to one direction when `action` is omitted -- unconfirmed either way in
     MadeOnSol's public docs), fall back to the original two filtered
     calls. Same behavior as before this module existed, never worse.

`mode` in the return value tells you which path was taken. The FIRST live
poll cycle Ali runs is the real answer to "does the unfiltered call work" --
until then this is fixture-tested only, like everything else in this repo
that touches MadeOnSol.
"""
from config import CONFIG
from utils.http import get_json, describe_fetch_failure
import state


def fetch_kol_feed_raw(chain: str = "solana", limit: int = 100, action: str = None) -> dict:
    if not CONFIG.madeonsol_api_key:
        return {"ok": False, "reason": "MADEONSOL_API_KEY not configured"}
    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    params = {"limit": limit}
    if action:
        params["action"] = action
    result = get_json(f"{CONFIG.madeonsol_base_url}{prefix}/kol/feed", headers=headers, params=params)
    return {"ok": result["ok"], "raw": result}


def _confirmed_key(chain: str) -> str:
    return f"kol_feed_confirmed_unfiltered:{chain}"


def _filtered_fallback(chain: str, limit: int) -> dict:
    buy_fetch = fetch_kol_feed_raw(chain, limit, action="buy")
    sell_fetch = fetch_kol_feed_raw(chain, limit, action="sell")
    if not buy_fetch["ok"] and not sell_fetch["ok"]:
        reason = describe_fetch_failure(buy_fetch)
        return {"ok": False, "reason": reason, "buy_trades": [], "sell_trades": [],
                "mode": "filtered_fallback", "calls_made": 2}
    buy_trades = (buy_fetch["raw"].get("json") or {}).get("trades", []) if buy_fetch["ok"] else []
    sell_trades = (sell_fetch["raw"].get("json") or {}).get("trades", []) if sell_fetch["ok"] else []
    return {"ok": True, "buy_trades": buy_trades, "sell_trades": sell_trades,
            "mode": "filtered_fallback", "calls_made": 2}


def fetch_kol_feed_both(chain: str = "solana", limit: int = 100) -> dict:
    """Returns {"ok", "reason"?, "buy_trades", "sell_trades", "mode", "calls_made"}.
    mode: "unfiltered" (1 MadeOnSol call covered both directions) or
    "filtered_fallback" (2 calls -- the unfiltered attempt didn't pan out).

    Once unfiltered mode is POSITIVELY confirmed (a response actually
    contained both 'buy' and 'sell' actions), that's remembered in state.py
    and the probe is skipped forever after -- pure upside, 1 call/cycle from
    then on. Until confirmed, every cycle still tries the unfiltered call
    first: worst case (unfiltered never works) this costs 3 calls that
    cycle (1 wasted probe + the 2-call fallback) instead of settling for 2
    calls/cycle forever -- a deliberate bet that discovering the 1-call mode
    is worth an extra call per cycle while unconfirmed. If Ali would rather
    not take that bet, the fix is one line: skip the probe and always call
    _filtered_fallback directly. Left as the adaptive version since the
    downside is bounded and the upside (half the calls, permanently) is
    large relative to the overall MadeOnSol budget -- see README."""
    already_confirmed = bool(state.get_value(_confirmed_key(chain)))

    unfiltered = fetch_kol_feed_raw(chain, limit, action=None)
    if unfiltered.get("reason") == "MADEONSOL_API_KEY not configured":
        return {"ok": False, "reason": unfiltered["reason"], "buy_trades": [], "sell_trades": [],
                "mode": "unconfigured", "calls_made": 0}
    if unfiltered["ok"]:
        body = unfiltered["raw"].get("json")
        trades = (body or {}).get("trades", []) if body else []
        actions_seen = {t.get("action") for t in trades}
        if "buy" in actions_seen and "sell" in actions_seen:
            if not already_confirmed:
                state.set_value(_confirmed_key(chain), True)
            return {
                "ok": True,
                "buy_trades": [t for t in trades if t.get("action") == "buy"],
                "sell_trades": [t for t in trades if t.get("action") == "sell"],
                "mode": "unfiltered",
                "calls_made": 1,
            }

    # Fallback: two filtered calls -- identical to the pre-optimization
    # behavior, plus the 1 wasted probe call above (see docstring).
    result = _filtered_fallback(chain, limit)
    result["calls_made"] += 1
    return result

"""
Layer 14 -- Revival watch + momentum detection (Sept 30 2026).

Ali: "a coin at the start might look like a rug but later the liquidity gets
added, or traction starts coming, or momentum starts building -- how is our
system going to track that and ride it, gain something, move out, like a
scalper and a sniper?"

What existed before: state.py's soft-fail watch (Sept 28) re-checked only
band-D SOLANA coins, only on two holder signals, and re-scored them with
budget-limited MadeOnSol calls. Band C ("almost") coins and every BSC, Base
and Robinhood Chain coin were never looked at again.

This layer extends that same watch list to every chain and to band C, and
re-checks watched coins every poll-fast cycle through ONE free DexScreener
batch call per chain (up to 30 tokens per call, no key, no MadeOnSol).
A watched coin counts as REVIVED when real trading has come in since it
failed -- any of:
  - liquidity added: now >= REVIVAL_MIN_LIQ_USD and >= 1.5x what it had
    when it failed;
  - momentum: 1h price +30% or more, buyers outnumbering sellers 1.2:1 in
    the last hour, 1h volume >= $5k, and the last 5 minutes not collapsing.
A revived coin is re-scored immediately with the free scorer (GoPlus,
liquidity, activity, capped Birdeye crash check -- the same one Layer 0b
uses) and handed to scheduler._handle_scored like any fresh coin: alert,
Stage 1 check, compound-scalper momentum entry, paper trade.

Everything below is pure (no network) except fetch_dexscreener_batch.
"""
from typing import Dict, List, Optional

from utils.http import get_json
from links import DEXSCREENER_CHAIN_SLUG

BATCH_SIZE = 30
# 28 Oct 2026 (Ali): robinhood_chain paper trades were dying at a 59% dead-on-arrival rate (see
# GO_LIVE_CHECKLIST SS0K/0L) -- not because the coins were bad, but because DexScreener simply has
# no indexed pool yet for many very-new robinhood_chain launches (confirmed: a direct DexScreener
# query for one of these exact dead tokens returned [] -- empty, not an error -- while the SAME
# token queried against GeckoTerminal's /networks/robinhood/tokens/{address} endpoint came back
# with real, current price/fdv/liquidity data). Of the 66/161 robinhood_chain trades that DID get
# priced by DexScreener, 57.6% won and the chain was net profitable (+$898.24) -- the strategy is
# fine, the single price source just can't see pools this fresh fast enough. GeckoTerminal already
# successfully resolves the robinhood_chain network slug at entry time (multichain_sniper.resolve_rhc),
# so it is used here as a second, per-token fallback price source -- ONLY for tokens DexScreener's
# batch call did not return anything for, and ONLY on robinhood_chain (every other chain's pricing
# is untouched).
GT_FALLBACK_CHAINS = {"robinhood_chain"}
GT_NETWORK_SLUG = {"robinhood_chain": "robinhood"}
REVIVAL_MIN_LIQ_USD = 10_000.0
REVIVAL_LIQ_GROWTH = 1.5
MOMENTUM_H1_CHANGE_PCT = 30.0
MOMENTUM_BUY_SELL_RATIO = 1.2
MOMENTUM_MIN_H1_VOLUME_USD = 5_000.0
MOMENTUM_M5_FLOOR_PCT = -10.0


def _gt_token_fallback(chain: str, address: str) -> Optional[dict]:
    """GeckoTerminal /networks/{slug}/tokens/{address} -- a single-token lookup used only as a
    fallback when DexScreener's batch call returned nothing for this token (see GT_FALLBACK_CHAINS
    comment above). Normalizes into the same shape callers already unpack from a DexScreener pair
    (marketCap/fdv, liquidity.usd) so no downstream code needs to know which source it came from.
    Never a hard dependency: any failure (unsupported chain, no data, network error) just returns
    None, same degrade-gracefully convention as the rest of this module."""
    gt_slug = GT_NETWORK_SLUG.get(chain)
    if not gt_slug or not address:
        return None
    try:
        from config import CONFIG
        base = CONFIG.geckoterminal_base_url
    except Exception:  # noqa: BLE001
        base = "https://api.geckoterminal.com/api/v2"
    r = get_json(f"{base}/networks/{gt_slug}/tokens/{address}")
    if not r.get("ok"):
        return None
    attrs = ((r.get("json") or {}).get("data") or {}).get("attributes") or {}
    fdv = attrs.get("fdv_usd")
    mcap = attrs.get("market_cap_usd")
    liq = attrs.get("total_reserve_in_usd")
    if fdv is None and mcap is None:
        return None            # nothing usable -- let the caller treat this token as still unpriced
    return {"marketCap": mcap, "fdv": fdv, "liquidity": {"usd": liq}}


def fetch_dexscreener_batch(chain: str, addresses: List[str]) -> Dict[str, dict]:
    """{address: deepest-liquidity pair} for up to BATCH_SIZE tokens in ONE
    call (DexScreener /tokens/v1/{chain}/{a,b,...}). Missing tokens are
    simply absent from the result, EXCEPT on GT_FALLBACK_CHAINS, where each
    still-missing token gets one extra GeckoTerminal lookup before giving up
    (see GT_FALLBACK_CHAINS comment above)."""
    slug = DEXSCREENER_CHAIN_SLUG.get(chain)
    if not slug or not addresses:
        return {}
    out: Dict[str, dict] = {}
    for i in range(0, len(addresses), BATCH_SIZE):
        chunk = addresses[i:i + BATCH_SIZE]
        r = get_json(f"https://api.dexscreener.com/tokens/v1/{slug}/{','.join(chunk)}")
        pairs = r.get("json") if r.get("ok") else None
        if not isinstance(pairs, list):
            continue
        lowered = {a.lower(): a for a in chunk}
        for p in pairs:
            base = ((p.get("baseToken") or {}).get("address") or "")
            addr = lowered.get(base.lower())
            if not addr:
                continue
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if addr not in out or liq > ((out[addr].get("liquidity") or {}).get("usd") or 0):
                out[addr] = p
    if chain in GT_FALLBACK_CHAINS:
        for addr in addresses:
            if addr in out:
                continue
            gt = _gt_token_fallback(chain, addr)
            if gt is not None:
                out[addr] = gt
    return out


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def momentum_metrics(pair: dict) -> dict:
    txh1 = (pair.get("txns") or {}).get("h1") or {}
    txh24 = (pair.get("txns") or {}).get("h24") or {}
    pc = pair.get("priceChange") or {}
    return {
        "liquidity_usd": _f((pair.get("liquidity") or {}).get("usd")),
        "price_usd": _f(pair.get("priceUsd")),
        "mcap_usd": _f(pair.get("marketCap")) or _f(pair.get("fdv")),
        "change_m5": _f(pc.get("m5")), "change_h1": _f(pc.get("h1")),
        "buys_h1": int(txh1.get("buys") or 0), "sells_h1": int(txh1.get("sells") or 0),
        "txns_h1": int((txh1.get("buys") or 0) + (txh1.get("sells") or 0)),
        "txns_h24": int((txh24.get("buys") or 0) + (txh24.get("sells") or 0)),
        "volume_h1": _f((pair.get("volume") or {}).get("h1")),
        "volume_h24": _f((pair.get("volume") or {}).get("h24")),
        "pair_created_at": pair.get("pairCreatedAt"),
    }


def is_revived(baseline_liq: Optional[float], m: dict) -> tuple:
    """(revived: bool, reasons: list, momentum: bool). momentum=True means
    the price/flow momentum rule fired (what the scalper trades on)."""
    reasons = []
    liq = m.get("liquidity_usd") or 0
    if liq >= REVIVAL_MIN_LIQ_USD and (not baseline_liq or liq >= baseline_liq * REVIVAL_LIQ_GROWTH):
        reasons.append(f"liquidity ${liq:,.0f} (was ${baseline_liq or 0:,.0f})")
    buys, sells = m.get("buys_h1", 0), m.get("sells_h1", 0)
    momentum = (
        (m.get("change_h1") or 0) >= MOMENTUM_H1_CHANGE_PCT
        and buys >= max(1, sells) * MOMENTUM_BUY_SELL_RATIO
        and (m.get("volume_h1") or 0) >= MOMENTUM_MIN_H1_VOLUME_USD
        and (m.get("change_m5") if m.get("change_m5") is not None else 0) > MOMENTUM_M5_FLOOR_PCT
        and liq >= REVIVAL_MIN_LIQ_USD / 2
    )
    if momentum:
        reasons.append(f"momentum +{m.get('change_h1'):.0f}% 1h, {buys} buys vs {sells} sells, "
                       f"${m.get('volume_h1') or 0:,.0f} 1h volume")
    return bool(reasons), reasons, momentum


def pair_to_gt_item(address: str, pair: dict) -> dict:
    """Maps a DexScreener pair to the flat item shape
    layer0_scoring.score_geckoterminal_pools expects, so a revived coin is
    re-scored by exactly the same free scorer Layer 0b uses."""
    import datetime
    m = momentum_metrics(pair)
    created = m.get("pair_created_at")
    created_iso = (datetime.datetime.fromtimestamp(created / 1000, tz=datetime.timezone.utc).isoformat()
                   if isinstance(created, (int, float)) else None)
    return {
        "address": address, "name": (pair.get("baseToken") or {}).get("symbol"),
        "price_usd": m["price_usd"], "fdv_usd": _f(pair.get("fdv")), "market_cap_usd": m["mcap_usd"],
        "volume_24h_usd": m["volume_h24"], "liquidity_usd": m["liquidity_usd"],
        "pool_created_at": created_iso, "txns_h1_total": m["txns_h1"], "txns_h24_total": m["txns_h24"],
        "dex_id": pair.get("dexId"),
        "change_m5": m["change_m5"], "change_h1": m["change_h1"],
    }

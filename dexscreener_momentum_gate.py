"""
Pre-entry momentum gate for Robinhood Chain + Base, using DexScreener's
own priceChange fields (m5/h1/h6/h24) -- zero extra API calls, since this
rides on the SAME token-pairs response fetch_dexscreener_vol_liq() already
makes for volume/liquidity on every scored token.

WHY THIS EXISTS (9 Nov 2026, Ali's direct question): layer0_scoring.py's
existing price_drawdown_from_peak_pct override (score_token, ~line 329)
needs Birdeye OHLCV to compute launch-window drawdown. Confirmed in code:
Birdeye has NO chain mapping for robinhood_chain at all (fetch_birdeye_ohlcv
in layer0d_point_in_time.py returns birdeye_chain=None for it) -- not a
config gap, a real network-support limit. So every robinhood_chain token
enters purely on structural safety signals (holder concentration, LP lock,
authority revocation) with ZERO check on whether the coin already stalled
or dumped before entry. Base DOES get the Birdeye check, but it shares a
40-calls/day budget with solana+bsc (confirmed exhausted at 40/40 today),
so it's frequently starved even though the mechanism exists.

THIS MODULE gives BOTH chains a real pre-entry "has this already stalled
or dumped" signal using data that is already being fetched for free, with
no dependency on Birdeye's chain coverage or daily budget at all.

REAL EVIDENCE for the threshold below: investigated the 9 "no pop"/"no
move" stall losses since Option1/2 deployed (13:28 PKT) -- 7 of 9 were
robinhood_chain (the chain with literally no momentum check today). The
mechanism's own existing post-entry rule (paper_ledger.py EARLY_NO_POP_MULT)
cuts a position that never reaches 1.05x within EARLY_NO_POP_MIN (1.5min).
This module aims to catch the SAME "never had real momentum" pattern
BEFORE paying the entry cost at all, using priceChange.m5/h1 as the
pre-entry analog of that post-entry check.

WIRING: call dexscreener_momentum_verdict(chain, address) -- or, cheaper,
momentum_verdict_from_pair(pair_dict) if the caller already has the
DexScreener pair object from fetch_dexscreener_vol_liq's underlying call
(avoids a second network round-trip). Returns a dict the caller folds into
RawSignals.price_drawdown_from_peak_pct -- same field, same override logic
in score_token, so NO changes needed to the override mechanism itself,
just a second way to populate the same signal on chains Birdeye can't
reach (or that Birdeye's shared budget is currently starving).

HONESTY FLAG, same posture as every other model in this engagement: this
is NOT identical to Birdeye's launch-window peak-to-close drawdown (that
needs true OHLCV history). priceChange.h1 is DexScreener's own computed
"price now vs price 1h ago" -- a real, live, first-party figure, not an
estimate, but it only sees the live window DexScreener tracks (m5/h1/h6/h24),
not necessarily the token's true all-time peak if that peak was further
back than h6. For tokens this system would ever consider buying (recent,
low-liquidity launches), h1/h6 covers the relevant window fine; it would
be a weaker signal on an old, slow-moving token, which is not this
system's use case.
"""
from __future__ import annotations
from typing import Optional

# Scope: the two chains that structurally lack (robinhood_chain) or
# frequently lose (base, Birdeye-budget-starved) a real pre-entry
# momentum/drawdown check. Not applied to solana/bsc -- they already have
# a working, unstarved mechanism via Birdeye (solana) or this doesn't
# change their Birdeye-budget competition at all once base gets its own
# dedicated cap (see BIRDEYE_BASE_DAILY_CAP in config/.env).
MOMENTUM_GATE_CHAINS = {"robinhood_chain", "base"}

# Threshold for treating the live DexScreener price-change window as a
# "this has already collapsed" signal. Same -60% cutoff score_token's
# existing override uses for Birdeye drawdown (OVERRIDE_DRAWDOWN_PCT in
# layer0_scoring.py), kept IDENTICAL on purpose -- this module feeds the
# same field/override, not a new separate threshold, so tuning stays in
# one place (layer0_scoring.py) once this is wired in.
DRAWDOWN_OVERRIDE_PCT = -60.0

# Which DexScreener window to read. h1 (not m5) chosen as the primary
# signal: m5 is noisy on a token seconds old (one or two trades can swing
# it wildly); h1 needs the pair to be at least ~1h old to be meaningful,
# which fits this system's management cadence (positions already live for
# minutes before any exit decision fires) better than an instant snapshot.
# Falls back to h6 when h1 is unavailable (pair too new for DexScreener to
# have computed an h1 change yet) -- h6 is a weaker but still real signal,
# never fabricated.
PRIMARY_WINDOW = "h1"
FALLBACK_WINDOW = "h6"


def momentum_verdict_from_pair(pair: dict) -> dict:
    """pair is a raw DexScreener pair object (same shape
    fetch_dexscreener_vol_liq already receives -- has 'priceChange',
    'liquidity', 'volume', 'txns' keys). Returns:
      {"drawdown_pct": float|None, "window_used": "h1"|"h6"|None,
       "reason": str}
    drawdown_pct maps directly onto RawSignals.price_drawdown_from_peak_pct
    -- same sign convention (negative = down from a recent high), same
    override threshold in score_token. None means no real signal available
    (never fabricated as 0 -- that would silently claim "flat," which is
    not the same as "unknown")."""
    pc = pair.get("priceChange") or {}

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    h1 = _f(pc.get(PRIMARY_WINDOW))
    if h1 is not None:
        return {"drawdown_pct": h1, "window_used": PRIMARY_WINDOW,
                "reason": f"DexScreener {PRIMARY_WINDOW} price change: {h1:+.1f}%"}

    h6 = _f(pc.get(FALLBACK_WINDOW))
    if h6 is not None:
        return {"drawdown_pct": h6, "window_used": FALLBACK_WINDOW,
                "reason": f"DexScreener {PRIMARY_WINDOW} unavailable, "
                          f"using {FALLBACK_WINDOW}: {h6:+.1f}%"}

    return {"drawdown_pct": None, "window_used": None,
            "reason": "no priceChange data available (pair too new or missing field)"}


def dexscreener_momentum_verdict(chain: str, address: str) -> dict:
    """Convenience wrapper that fetches the pair itself, for callers that
    don't already have it in hand. PREFER momentum_verdict_from_pair when
    the caller already made the fetch_dexscreener_vol_liq-style call --
    this makes a second network round-trip, which the main scoring path
    (score_geckoterminal_pools) should avoid by reusing its existing
    fetch instead of calling this wrapper."""
    if chain not in MOMENTUM_GATE_CHAINS:
        return {"drawdown_pct": None, "window_used": None,
                "reason": f"chain {chain!r} not in scope for this gate"}
    from links import DEXSCREENER_CHAIN_SLUG
    from utils.http import get_json
    slug = DEXSCREENER_CHAIN_SLUG.get(chain)
    if not slug or not address:
        return {"drawdown_pct": None, "window_used": None,
                "reason": f"no DexScreener chain slug for chain={chain!r}"}
    result = get_json(f"https://api.dexscreener.com/token-pairs/v1/{slug}/{address}")
    if not result.get("ok"):
        return {"drawdown_pct": None, "window_used": None, "reason": "DexScreener fetch failed"}
    pairs = result.get("json")
    if not isinstance(pairs, list) or not pairs:
        return {"drawdown_pct": None, "window_used": None, "reason": "no pairs returned"}
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    return momentum_verdict_from_pair(best)

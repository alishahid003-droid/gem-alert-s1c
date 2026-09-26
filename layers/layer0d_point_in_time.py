"""Point-in-time backtest data layer -- Ali's ask Sept 26 2026: "we need to
run it on back data...that if our system was live when these coins launched
what would our system would have recommended and done...only then we come
to know the credibility and effectiveness of our system."

WHY THIS EXISTS: backtest_categorized.py scores every labeled coin against
CURRENT live data (today's holders/bundle/risk/liquidity). That's honest for
judging "does the engine separate healthy vs dead tokens today," but for
moonshots especially it's not a fair test -- scoring TRUMP or LAPTOP *today*
(months of history, tens of millions of real mcap) proves nothing about
whether the engine would have caught them the day they launched, which is
the actual question for sniper credibility.

REAL, CONFIRMED LIMITATION (checked before building this, not guessed):
none of MadeOnSol's per-token endpoints (/tokens/{mint}/risk, /holders,
/bundle), GoPlus's security API, or DexScreener's public pairs endpoint
expose historical/point-in-time data for an arbitrary past date -- all
three are current-snapshot-only. Confirmed against each provider's own
docs Sept 26 2026:
  - MadeOnSol's SDK: https://github.com/madeonsol/madeonsol-sdk -- the
    ONLY historical/point-in-time endpoint in their whole API is deployer
    WALLET reputation (GET /deployer-hunter/{wallet}/as-of?date=YYYY-MM-DD
    and .../history), not anything per-token.
  - GoPlus: https://docs.gopluslabs.io/reference/api-overview -- real-time
    only, no historical param.
  - DexScreener: the free public pairs endpoint this codebase already
    calls has no OHLCV/historical-candle capability at all.

So a FULL point-in-time replay (holder concentration, bundle/sniper wallet
share, and risk score exactly as MadeOnSol would have computed them at
launch) is NOT buildable from any source wired into this codebase -- there
is no historical data to pull for that. This module builds the two pieces
that genuinely ARE available for free, real, point-in-time data, and is
explicit about what's still a proxy:

  1. REAL launch timestamp -- DexScreener's pairCreatedAt field (confirmed
     real per docs.dexscreener.com/api/reference), already threaded through
     fetch_dexscreener_vol_liq (layer0_scoring.py) as launch_ts_ms.

  2. REAL point-in-time deployer-tier check -- MadeOnSol's
     deployer-hunter/{wallet}/as-of endpoint answers "what was this
     deployer's reputation tier on the coin's actual launch date" -- and
     deployer tier is exactly what Layer 1 filters on FIRST, before a token
     is ever scored (see layer1_deployer.py). This needs the deployer's
     WALLET address, which MadeOnSol's per-mint endpoints don't return --
     see fetch_deployer_wallet_onchain below for how that's sourced.

  3. BEST-EFFORT, not a hard guarantee -- on-chain deployer wallet lookup.
     Solana RPC's getSignaturesForAddress only pages BACKWARDS (newest
     first via a `before` cursor) -- there is no "give me the oldest
     signature" call. Finding a mint's very first (creation) transaction
     means paging all the way back, which is cheap for a quiet/rugged
     token (hundreds-thousands of txns) but impractical for a token with
     months of real trading history (TRUMP, LAPTOP -- could be millions of
     txns on a free, rate-limited public RPC). MAX_SIGNATURE_PAGES below
     caps this so it fails honestly ("couldn't reach genesis within
     budget") instead of hanging -- this will work cleanly for the young
     rugs/pump_dumps (exactly where deployer-tier-at-launch matters most)
     and likely time out for old established moonshots, which is fine
     since those don't need this signal to prove they were once real.

  4. BEST-EFFORT, access unconfirmed -- Birdeye historical OHLCV. Birdeye's
     own docs disagree with each other: their package-comparison page
     (docs.birdeye.so/docs/data-accessibility-by-packages) lists "Price &
     OHLCV: Historical prices, OHLCV data" under the free $0 Standard tier,
     but the OHLCV reference page itself
     (docs.birdeye.so/reference/get-defi-ohlcv) lists "Supported Plans:
     Starter, Premium, Business, Enterprise" with no Standard tier
     mentioned. NOT resolved by reading more docs -- the real answer is
     whatever Ali's actual free key gets back the first time this runs
     (200 with real candle data, or 401/403 meaning Standard doesn't cover
     it after all). fetch_birdeye_ohlcv below surfaces that real response
     rather than assuming either way.
"""
from typing import Optional
import time

from config import CONFIG
from utils.http import get_json, describe_fetch_failure
from executor.rpc_pool import rpc_call
import state

MAX_SIGNATURE_PAGES = 60          # 60 * 1000 = up to 60k signatures paged
SIGNATURES_PER_PAGE = 1000        # Solana RPC's own max per call


def fetch_deployer_wallet_onchain(mint: str, max_pages: int = MAX_SIGNATURE_PAGES) -> dict:
    """Best-effort: pages Solana's getSignaturesForAddress backwards from
    "now" until it reaches the mint's very first (oldest) transaction --
    the fee payer of that transaction is the deployer wallet for the
    overwhelming majority of pump.fun-style launches (the create-mint
    instruction is normally the account's first-ever activity).

    Returns {"ok": True, "deployer_wallet": <pubkey>, "launch_signature": ...,
    "pages_used": N} on success, or {"ok": False, "reason": ...,
    "truncated": True} if max_pages is hit before reaching genesis (a real,
    honest partial result, not a guess) -- see module docstring for why
    this is expected for old/high-volume tokens."""
    before = None
    last_page = None
    pages_used = 0
    for _ in range(max_pages):
        params_obj = {"limit": SIGNATURES_PER_PAGE}
        if before:
            params_obj["before"] = before
        result = rpc_call("solana", "getSignaturesForAddress", [mint, params_obj])
        pages_used += 1
        if not result.get("ok"):
            return {"ok": False, "reason": f"RPC failed on page {pages_used}: {result.get('reason')}",
                    "pages_used": pages_used}
        page = result.get("result") or []
        if not page:
            # empty page with a `before` cursor set means we've paged past
            # genesis already -- last_page from the prior loop was it
            break
        last_page = page
        if len(page) < SIGNATURES_PER_PAGE:
            # short page = this IS the oldest page, no need to page further
            break
        before = page[-1]["signature"]
    if not last_page:
        return {"ok": False, "reason": "no signatures found for this mint", "pages_used": pages_used}
    if pages_used >= max_pages and len(last_page) == SIGNATURES_PER_PAGE:
        return {"ok": False, "reason": f"hit {max_pages}-page cap before reaching genesis -- "
                                        "token has too much history for free-RPC pagination",
                "truncated": True, "pages_used": pages_used}
    oldest_sig = last_page[-1]["signature"]
    tx_result = rpc_call("solana", "getTransaction",
                          [oldest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    if not tx_result.get("ok"):
        return {"ok": False, "reason": f"got oldest signature but fetching its tx failed: "
                                        f"{tx_result.get('reason')}", "pages_used": pages_used}
    tx = tx_result.get("result") or {}
    account_keys = (tx.get("transaction", {}).get("message", {}) or {}).get("accountKeys", [])
    if not account_keys:
        return {"ok": False, "reason": "oldest tx has no accountKeys to read a fee payer from",
                "pages_used": pages_used}
    # Fee payer is always accountKeys[0] per Solana's transaction format.
    first = account_keys[0]
    deployer_wallet = first.get("pubkey") if isinstance(first, dict) else first
    if not deployer_wallet:
        return {"ok": False, "reason": "couldn't read fee payer pubkey from oldest tx",
                "pages_used": pages_used}
    return {"ok": True, "deployer_wallet": deployer_wallet, "launch_signature": oldest_sig,
            "pages_used": pages_used}


def fetch_deployer_asof(wallet: str, chain: str, date_str: str) -> dict:
    """Real point-in-time deployer-reputation snapshot -- MadeOnSol's own
    documented as-of endpoint (see module docstring for source). `date_str`
    must be YYYY-MM-DD and >= 2026-04-07 per MadeOnSol's own stated floor;
    a date before that just gets whatever their earliest snapshot is, per
    the endpoint's own "latest write-on-change snapshot at or before it"
    semantics -- not this code's problem to enforce, MadeOnSol's API
    handles that itself."""
    if not CONFIG.madeonsol_api_key:
        return {"ok": False, "reason": "MADEONSOL_API_KEY not configured"}
    if state.madeonsol_budget_remaining() < 1:
        return {"ok": False, "reason": "MadeOnSol daily call budget exhausted "
                                        f"({state.madeonsol_calls_today()}/{state.MADEONSOL_DAILY_BUDGET})"}
    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    result = get_json(f"{CONFIG.madeonsol_base_url}{prefix}/deployer-hunter/{wallet}/as-of",
                       headers=headers, params={"date": date_str})
    state.record_madeonsol_calls(1)
    if not result.get("ok"):
        return {"ok": False, "reason": describe_fetch_failure({"raw": result})}
    body = result.get("json") or {}
    return {"ok": True, "as_of": body.get("as_of"), "snapshot": body.get("snapshot")}


def fetch_birdeye_ohlcv(chain: str, address: str, time_from: int, time_to: int,
                         interval: str = "1H") -> dict:
    """Historical OHLCV candles around a token's real launch window -- see
    module docstring point 4 for the honest "access tier unconfirmed until
    tested live" caveat. time_from/time_to are Unix seconds. Never a hard
    dependency for backtest_point_in_time.py -- if this comes back
    ok=False (missing key, or the free tier really doesn't cover it),
    the caller just reports the gap and moves on, same degrade-gracefully
    convention as every other optional source in this codebase."""
    if not CONFIG.birdeye_api_key:
        return {"ok": False, "reason": "BIRDEYE_API_KEY not configured"}
    birdeye_chain = {"solana": "solana", "base": "base", "bsc": "bsc",
                      "robinhood_chain": None, "ethereum": "ethereum"}.get(chain)
    if not birdeye_chain:
        return {"ok": False, "reason": f"no Birdeye chain mapping for chain={chain!r} "
                                        "(Robinhood Chain is not a Birdeye-supported network)"}
    headers = {"X-API-KEY": CONFIG.birdeye_api_key, "x-chain": birdeye_chain, "accept": "application/json"}
    params = {"address": address, "type": interval, "time_from": time_from, "time_to": time_to}
    result = get_json(f"{CONFIG.birdeye_base_url}/defi/ohlcv", headers=headers, params=params)
    if not result.get("ok"):
        return {"ok": False, "reason": describe_fetch_failure({"raw": result}),
                "status_code": result.get("status_code")}
    body = result.get("json") or {}
    items = ((body.get("data") or {}).get("items")) or []
    return {"ok": True, "candles": items}


def summarize_launch_window(candles: list) -> dict:
    """Reduces a list of Birdeye OHLCV candles down to the numbers that
    actually matter for judging a launch: first-candle price, peak price
    in the window, peak-to-window-close drawdown (the real "did it pump
    and dump WITHIN the window we can see" signal), and total volume."""
    if not candles:
        return {"ok": False, "reason": "no candles to summarize"}
    opens = [c.get("o") for c in candles if c.get("o") is not None]
    highs = [c.get("h") for c in candles if c.get("h") is not None]
    closes = [c.get("c") for c in candles if c.get("c") is not None]
    volumes = [c.get("v") for c in candles if c.get("v") is not None]
    if not opens or not highs or not closes:
        return {"ok": False, "reason": "candles missing OHLC fields"}
    first_price = opens[0]
    peak_price = max(highs)
    last_price = closes[-1]
    drawdown_from_peak_pct = None
    if peak_price:
        drawdown_from_peak_pct = round((last_price - peak_price) / peak_price * 100, 2)
    return {
        "ok": True,
        "first_price": first_price,
        "peak_price": peak_price,
        "last_price_in_window": last_price,
        "drawdown_from_peak_pct": drawdown_from_peak_pct,
        "total_volume": sum(volumes) if volumes else None,
        "num_candles": len(candles),
    }

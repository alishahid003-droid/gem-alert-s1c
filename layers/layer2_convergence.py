"""
Layer 2 -- Multi-wallet convergence.

Fires when 2+ Tier 1/2 wallets (roster.CONVERGENCE_ROSTER) buy the same new
token within ~1 hour of each other, via MadeOnSol's KOL feed
(/kol/feed and /rhc/kol/feed).

ASSUMPTION FLAGGED FOR LIVE VERIFICATION: MadeOnSol's own published example
response for /kol/feed (see README) did not show a token-identifying field
in the truncated sample -- only wallet_address, kol_name, action, sol_amount,
market_cap_usd_at_trade, traded_at. This module reads token_mint / mint /
token_address, in that order, and will need a one-line fix if the live field
name differs. Flagging this now rather than silently assuming it's right.
"""
from datetime import datetime, timedelta
from collections import defaultdict

from config import CONFIG
from utils.http import get_json
from layers.roster import CONVERGENCE_ROSTER, tier_of

CONVERGENCE_WINDOW = timedelta(hours=1)
MIN_WALLETS_FOR_CONVERGENCE = 2


def fetch_kol_feed(chain: str = "solana", limit: int = 100) -> dict:
    if not CONFIG.madeonsol_api_key:
        return {"ok": False, "reason": "MADEONSOL_API_KEY not configured"}
    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    result = get_json(f"{CONFIG.madeonsol_base_url}{prefix}/kol/feed",
                       headers=headers, params={"limit": limit, "action": "buy"})
    return {"ok": result["ok"], "raw": result}


def _token_id(trade: dict):
    return trade.get("token_mint") or trade.get("mint") or trade.get("token_address")


def _parse_time(trade: dict):
    ts = trade.get("traded_at")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_convergence(trades: list, roster: set = CONVERGENCE_ROSTER,
                        window: timedelta = CONVERGENCE_WINDOW,
                        min_wallets: int = MIN_WALLETS_FOR_CONVERGENCE) -> list:
    """trades: list of raw trade dicts from /kol/feed's "trades" array.
    Returns a list of convergence events: {token, wallets: [(name, time)], count}."""
    by_token = defaultdict(list)
    for t in trades:
        if t.get("action") != "buy":
            continue
        name = t.get("kol_name")
        if name not in roster:
            continue
        token = _token_id(t)
        ts = _parse_time(t)
        if not token or not ts:
            continue
        by_token[token].append((name, ts, t))

    events = []
    for token, entries in by_token.items():
        entries.sort(key=lambda e: e[1])
        # sliding window: for each entry, count distinct roster names within
        # `window` after it
        for i, (name_i, ts_i, _) in enumerate(entries):
            window_names = {name_i}
            for name_j, ts_j, _ in entries[i + 1:]:
                if ts_j - ts_i <= window:
                    window_names.add(name_j)
                else:
                    break
            if len(window_names) >= min_wallets:
                events.append({
                    "token": token,
                    "wallets": sorted(window_names),
                    "tiers": {n: tier_of(n) for n in window_names},
                    "count": len(window_names),
                    "window_start": ts_i.isoformat(),
                })
    # de-duplicate: keep the highest-count event per token
    best_per_token = {}
    for e in events:
        cur = best_per_token.get(e["token"])
        if not cur or e["count"] > cur["count"]:
            best_per_token[e["token"]] = e
    return list(best_per_token.values())


def extract_mc_points(trades: list) -> list:
    """Pulls (token, market_cap_usd, unix_ts) out of raw /kol/feed trades --
    used by the scheduler to feed Layer 8's rolling MC history from data
    it's already fetching for this layer, no extra API call."""
    points = []
    for t in trades:
        token = _token_id(t)
        mc = t.get("market_cap_usd_at_trade")
        ts = _parse_time(t)
        if token and mc is not None and ts is not None:
            points.append((token, mc, ts.timestamp()))
    return points


def poll_layer2(chain: str = "solana", trades: list = None) -> dict:
    """If `trades` is given (already-fetched buy trades, e.g. from
    layers.kol_feed.fetch_kol_feed_both shared across Layers 2+9), uses them
    directly and makes no network call. Otherwise fetches its own -- kept for
    backward compatibility / standalone testing."""
    if trades is None:
        fetched = fetch_kol_feed(chain)
        if not fetched["ok"]:
            return {"ok": False, "reason": fetched.get("reason", "fetch failed"), "events": []}
        body = fetched["raw"].get("json")
        if body is None:
            return {"ok": False, "reason": f"non-JSON response, status {fetched['raw']['status_code']}", "events": []}
        trades = body.get("trades", [])
    return {"ok": True, "events": detect_convergence(trades), "mc_points": extract_mc_points(trades)}

"""Free leaderboard of top Solana traders (Solana Tracker data API, PnL v2 leaderboard).
Needs a FREE key in .env: SOLANATRACKER_API_KEY (free plan = 2.5K requests/month, 3 req/s). We use ~2 calls/day.
Wallets go to state `st_leaderboard_v1`; wallet_watch.py watches them on-chain (a buy by them = copy signal) and
worker smart_wallets() unions them. Filters ask the API for consistent traders, not one-off winners."""
import os
import time
from typing import List, Optional

import requests

KEY_WALLETS = "st_leaderboard_v1"
KEY_TS = "st_leaderboard_ts_v1"
KEY_CALLS = "st_calls_month"
BASE = os.environ.get("SOLANATRACKER_BASE", "https://data.solanatracker.io")


def api_key() -> Optional[str]:
    return (os.environ.get("SOLANATRACKER_API_KEY") or "").strip() or None


def parse(body) -> List[dict]:
    out = []
    for t in (body or {}).get("traders", []) or []:
        w = t.get("wallet")
        if not w:
            continue
        out.append({"wallet": w, "realized": ((t.get("ending") or {}).get("pnl") or {}).get("realized"),
                    "win": t.get("winRate"), "trades": (t.get("counts") or {}).get("trades")})
    return out


def fetch_page(cursor=None, get=None) -> dict:
    get = get or (lambda url, params, headers: requests.get(url, params=params, headers=headers, timeout=25))
    params = {"sort": "realized", "direction": "desc", "limit": 100, "days": 30, "minTrades": 30, "minDays": 5,
              "minClosedTokens": 10, "maxSingleTokenPct": 50, "excludeArbitrage": "true", "pnlMode": "strict"}
    if cursor:
        params["cursor"] = cursor
    r = get(f"{BASE}/v2/pnl/leaderboard/top", params, {"x-api-key": api_key() or ""})
    if getattr(r, "status_code", 200) != 200:
        return {"error": f"HTTP {getattr(r, 'status_code', '?')}"}
    return r.json()


def refresh(state, now: float = None, get=None, pages: int = 2, every_s: float = 86400, month_cap: int = 100) -> str:
    """Once per day. Returns a short status line for the log."""
    now = now or time.time()
    if not api_key():
        return "no SOLANATRACKER_API_KEY in .env (free key from solanatracker.io) - leaderboard skipped"
    if now - float(state.get_value(KEY_TS) or 0) < every_s:
        return "fresh"
    mkey = f"{KEY_CALLS}:{time.strftime('%Y-%m', time.gmtime(now))}"
    used = int(state.get_value(mkey) or 0)
    wallets: List[str] = []
    cursor = None
    for _ in range(pages):
        if used >= month_cap:
            return f"monthly call cap {month_cap} reached"
        body = fetch_page(cursor, get)
        used += 1
        if body.get("error"):
            state.set_value(mkey, used)
            return "leaderboard error: " + str(body["error"])[:60]
        tr = parse(body)
        diag = {"keys": sorted(body.keys())[:8] if isinstance(body, dict) else str(type(body)), "n_traders": len(tr),
                "first": (body.get("traders") or [{}])[0].keys().__repr__()[:300] if isinstance(body, dict) and body.get("traders") else None,
                "sample": (tr[0] if tr else None), "msg": str(body.get("message") or body.get("error") or "")[:120] if isinstance(body, dict) else ""}
        state.set_value("st_diag_v1", diag)
        for t in tr:
            wallets.append(t["wallet"])          # the API already sorts by realized PnL and filters for consistent traders
        cursor = (body.get("pagination") or {}).get("nextCursor")
        if not cursor:
            break
    state.set_value(mkey, used)
    if wallets:
        state.set_value(KEY_WALLETS, list(dict.fromkeys(wallets))[:300])
        state.set_value(KEY_TS, now)
    else:
        state.set_value(KEY_TS, now - every_s + 6 * 3600)          # empty answer: retry in 6 h, not every hour
    return f"leaderboard: {len(wallets)} wallets stored (calls this month {used}); diag {state.get_value('st_diag_v1')}"

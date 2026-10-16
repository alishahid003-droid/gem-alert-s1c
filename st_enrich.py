"""Use the leftover Solana Tracker free credits (2.5K/month ~ 80/day; the leaderboard uses ~2/day) on the best question we can ask:
WHO BOUGHT OUR WINNERS FIRST? For coins in our logs that ran >=10x we call GET /first-buyers/{mint} (first 100 buyers with PnL).
Wallets that were early AND in profit on >= MIN_WINNERS different winners are stored as `st_early_wallets_v1`; wallet_watch follows
them on-chain and worker smart_wallets() unions them (-> smart_wallet confirmation, copy signal). Snipers/bots can look like this
too, so the bar is >=3 winners and a profit each time; the 24 h data judges whether it adds anything. Budget-capped per day and month."""
import os
import time
from typing import Callable, Dict, List, Optional

import requests

import solana_tracker as stk

KEY_DONE = "st_fb_done_v1"            # {mint: [wallets that were profitable early buyers]}
KEY_OUT = "st_early_wallets_v1"
KEY_DAY = "st_fb_calls_day"           # + ":YYYY-MM-DD"
KEY_DIAG = "st_fb_diag_v1"
MIN_WINNERS = int(os.environ.get("ST_MIN_WINNERS", "3"))
MIN_PNL_USD = float(os.environ.get("ST_MIN_PNL_USD", "100"))   # 16 Oct: $1-profit wallets in 15 "winners" were a bot cluster


def parse_first_buyers(body) -> List[dict]:
    """Tolerant: list under buyers/wallets/data or a bare list; wallet under wallet/owner/address; pnl under pnl/realized/total/profit."""
    rows = body if isinstance(body, list) else None
    if rows is None and isinstance(body, dict):
        for k in ("buyers", "wallets", "data", "traders", "results"):
            if isinstance(body.get(k), list):
                rows = body[k]
                break
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        w = r.get("wallet") or r.get("owner") or r.get("address")
        pnl = None
        for k in ("pnl", "realized", "total", "profit"):
            v = r.get(k)
            if isinstance(v, dict):
                v = v.get("realized", v.get("total"))
            if isinstance(v, (int, float)):
                pnl = float(v)
                break
        if w:
            out.append({"wallet": w, "pnl": pnl})
    return out


def fetch_first_buyers(mint: str, get: Optional[Callable] = None) -> dict:
    get = get or (lambda url, headers: requests.get(url, headers=headers, timeout=25))
    r = get(f"{stk.BASE}/first-buyers/{mint}", {"x-api-key": stk.api_key() or ""})
    if getattr(r, "status_code", 200) != 200:
        return {"error": f"HTTP {getattr(r, 'status_code', '?')}"}
    return {"body": r.json()}


def rebuild(state, min_winners: int = MIN_WINNERS) -> List[str]:
    cnt: Dict[str, int] = {}
    for _m, ws in (state.get_value(KEY_DONE) or {}).items():
        for w in set(ws):
            cnt[w] = cnt.get(w, 0) + 1
    import bot_wallets
    out = sorted(w for w, c in cnt.items() if c >= min_winners and w not in bot_wallets.load())
    state.set_value(KEY_OUT, out)
    return out


def run(state, winner_mints: List[str], now: float = None, get: Optional[Callable] = None, max_calls: int = 5,
        day_cap: int = 45, month_cap: int = 1800) -> str:
    """Process up to max_calls unprocessed winner mints (newest first), inside the day and month credit budgets."""
    now = now or time.time()
    if not stk.api_key():
        return "no key"
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    mkey = f"{stk.KEY_CALLS}:{time.strftime('%Y-%m', time.gmtime(now))}"
    dkey = f"{KEY_DAY}:{day}"
    done = dict(state.get_value(KEY_DONE) or {})
    calls = 0
    for m in winner_mints:
        if m in done:
            continue
        if calls >= max_calls or int(state.get_value(dkey) or 0) >= day_cap or int(state.get_value(mkey) or 0) >= month_cap:
            break
        res = fetch_first_buyers(m, get)
        calls += 1
        state.set_value(dkey, int(state.get_value(dkey) or 0) + 1)
        state.set_value(mkey, int(state.get_value(mkey) or 0) + 1)
        if res.get("error"):
            state.set_value(KEY_DIAG, {"error": res["error"], "mint": m[:8]})
            return "first-buyers error: " + res["error"]
        rows = parse_first_buyers(res["body"])
        state.set_value(KEY_DIAG, {"n_rows": len(rows), "sample": rows[:2],
                                   "keys": sorted(res["body"].keys())[:8] if isinstance(res["body"], dict) else "list"})
        done[m] = [r["wallet"] for r in rows if (r["pnl"] or 0) > MIN_PNL_USD]
    state.set_value(KEY_DONE, done)
    out = rebuild(state)
    return f"{calls} calls, {len(done)} winners processed, {len(out)} early wallets (>= {MIN_WINNERS} winners)"

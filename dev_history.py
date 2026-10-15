"""Dev history backfill (free alternative to MadeOnSol Deployer Hunter / GMGN dev history).
For a dev wallet: list the pump.fun coins it created (Helius parsed history, fallback = plain RPC), then judge each
coin with free DexScreener data: WIN = graduated off the pump.fun curve, or market cap >= WIN_MCAP now.
Result per dev in state `dev_history_v1`: {dev: {"ts","launches","wins","best_mcap","src"}}; dev_rank.refresh merges it
into the good-dev list (>=3 launches, >=30% wins). Alert/trade logic is unchanged - this only widens the history."""
import json
import os
import time
from typing import Callable, Dict, List, Optional

import requests

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
KEY = "dev_history_v1"
WIN_MCAP = float(os.environ.get("DEV_HIST_WIN_MCAP", "60000"))
RECHECK_S = 3 * 86400
MAX_COINS = 60


def helius_key() -> Optional[str]:
    k = os.environ.get("HELIUS_API_KEY")
    if k:
        return k
    u = os.environ.get("SOLANA_RPC_URL", "")
    if "api-key=" in u:
        return u.split("api-key=", 1)[1].split("&", 1)[0]
    return None


def find_creates(obj, out: Optional[list] = None) -> List[str]:
    """Tolerant walk of a Helius parsed-events page: any instruction named create/create_v2 -> its mint account."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        name = str(obj.get("instructionName") or obj.get("name") or "").lower()
        if name in ("create", "create_v2"):
            dec = obj.get("decoded") or obj
            accs = dec.get("accounts") if isinstance(dec, dict) else None
            mint = None
            if isinstance(accs, dict):
                mint = accs.get("mint")
            elif isinstance(accs, list):
                for a in accs:
                    if isinstance(a, dict) and str(a.get("name") or a.get("role") or "").lower() == "mint":
                        mint = a.get("pubkey") or a.get("address")
            if mint and mint not in out:
                out.append(mint)
        for v in obj.values():
            find_creates(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_creates(v, out)
    return out


def creates_from_helius(dev: str, post=None, pages: int = 3) -> Optional[List[str]]:
    key = helius_key()
    if not key:
        return None
    post = post or (lambda url, body: requests.post(url, json=body, timeout=25))
    url = f"https://mainnet.helius-rpc.com/v1/parsed-events/transaction-history?api-key={key}"
    mints: List[str] = []
    token = None
    for _ in range(pages):
        body = {"address": dev, "limit": 100, "sortOrder": "desc", "commitment": "confirmed"}
        if token:
            body["paginationToken"] = token
        r = post(url, body)
        if getattr(r, "status_code", 200) != 200:
            return None
        j = r.json()
        find_creates(j, mints)
        token = (j.get("paginationToken") if isinstance(j, dict) else None)
        if not token or len(mints) >= MAX_COINS:
            break
    return mints[:MAX_COINS]


def creates_from_rpc(dev: str, rpc: Callable, max_tx: int = 40) -> Optional[List[str]]:
    """Fallback with the plain RPC: pump 'Create' transactions signed by the dev. ~1+max_tx calls."""
    r = rpc("getSignaturesForAddress", [dev, {"limit": max_tx}])
    if not r.get("ok"):
        return None
    mints: List[str] = []
    for s in r.get("result") or []:
        if s.get("err"):
            continue
        t = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        res = t.get("result") if t.get("ok") else None
        if not res:
            continue
        logs = (res.get("meta") or {}).get("logMessages") or []
        if not any("Instruction: Create" in l for l in logs):
            continue
        for ix in (res.get("transaction") or {}).get("message", {}).get("instructions", []):
            if ix.get("programId") == PUMP_PROGRAM and ix.get("accounts"):
                m = ix["accounts"][0]
                if m not in mints:
                    mints.append(m)
    return mints


def judge(pairs: list, mints: List[str]) -> Dict[str, dict]:
    """DexScreener pairs -> {mint: {"win": bool, "mcap": x}}. A mint with no pair at all counts as dead."""
    best: Dict[str, dict] = {}
    for p in pairs or []:
        m = (p.get("baseToken") or {}).get("address")
        if m not in mints:
            continue
        mc = float(p.get("marketCap") or p.get("fdv") or 0)
        grad = str(p.get("dexId") or "").lower() not in ("pumpfun", "")
        e = best.setdefault(m, {"win": False, "mcap": 0.0})
        e["mcap"] = max(e["mcap"], mc)
        e["win"] = e["win"] or grad or mc >= WIN_MCAP
    return {m: best.get(m, {"win": False, "mcap": 0.0}) for m in mints}


def dexscreener_pairs(mints: List[str], get=None) -> list:
    get = get or (lambda u: requests.get(u, timeout=20))
    out = []
    for i in range(0, len(mints), 30):
        r = get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints[i:i + 30]))
        if getattr(r, "status_code", 200) == 200:
            j = r.json()
            out += j if isinstance(j, list) else []
    return out


def backfill_one(state, dev: str, now: float = None, mints_fn=None, pairs_fn=None, rpc=None) -> Optional[dict]:
    now = now or time.time()
    src = "helius"
    mints = mints_fn(dev) if mints_fn else None
    if mints is None:
        try:
            mints = creates_from_helius(dev)
        except Exception:  # noqa: BLE001
            mints = None
    if mints is None and rpc is not None:
        src = "rpc"
        mints = creates_from_rpc(dev, rpc)
    if mints is None:
        return None
    verdict = judge((pairs_fn or dexscreener_pairs)(mints) if mints else [], mints)
    rec = {"ts": now, "launches": len(mints), "wins": sum(1 for v in verdict.values() if v["win"]),
           "best_mcap": max([v["mcap"] for v in verdict.values()] or [0]), "src": src}
    book = state.get_value(KEY) or {}
    book[dev] = rec
    state.set_value(KEY, book)
    return rec


def queue(state, devs_seen: Dict[str, list], extra: List[str], now: float = None, n: int = 2) -> List[str]:
    """Devs to backfill next: ones launching repeatedly in our logs + extra (screenshot list), oldest/unchecked first."""
    now = now or time.time()
    book = state.get_value(KEY) or {}
    cand = [d for d, (l, w) in devs_seen.items() if l >= 2 or w >= 1] + list(extra)    # repeat launchers + creators of any winner
    cand = [d for d in dict.fromkeys(cand) if now - (book.get(d) or {}).get("ts", 0) > RECHECK_S]
    cand.sort(key=lambda d: ((book.get(d) or {}).get("ts", 0), -(devs_seen.get(d) or [0, 0])[1]))   # unchecked first, creators of winners first
    return cand[:n]


def merged_devs(devs_log: Dict[str, list], book: Dict[str, dict]) -> Dict[str, list]:
    """Combine [launches,wins] from our own logs with the backfilled history (take the larger sample per dev)."""
    out = {k: list(v) for k, v in devs_log.items()}
    for d, r in (book or {}).items():
        cur = out.get(d)
        if not cur or r["launches"] > cur[0]:
            out[d] = [r["launches"], r["wins"]]
    return out

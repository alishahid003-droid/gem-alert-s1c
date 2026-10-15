"""Copy-trading alternative for Base / BSC / Robinhood chain (no KOL feed covers them).
Free data only: GeckoTerminal pool trades (tx_from_address, buy/sell). Two jobs, both budgeted:
 LEARN : for trending pools (coins that already ran) record which wallets bought among the first EARLY_N buys.
         A wallet seen early in >= SMART_MIN_POOLS distinct winning pools becomes 'smart' (per chain).
         Wallets that trade like bots (many trades in one pool) are ignored. Survivor-biased; paper evidence only.
 WATCH : for fresh candidate pools, fetch recent trades (a few per cycle) and flag pools a smart wallet is buying.
Result: p['smart_n'] on the candidate -> 'smart_wallet' confirmation in multichain_sniper.Scanner."""
import os
import time

KEY = "evm_wallets_v1"          # {chain: {wallet: [pool, ...]}}
KEY_LEARNED = "evm_learned_pools_v1"
KEY_HITS = "evm_smart_hits_v1"  # {chain|pool: {n, ts}}
EARLY_N = 25
BOT_TRADES = 6                  # >6 trades in one pool = bot, skip
SMART_MIN_POOLS = int(os.environ.get("EVM_SMART_MIN_POOLS", "3"))


def parse_trades(body) -> list:
    out = []
    for t in (body or {}).get("data", []) or []:
        a = t.get("attributes") or {}
        w = a.get("tx_from_address")
        if w:
            out.append({"wallet": w.lower(), "kind": a.get("kind"), "ts": a.get("block_timestamp") or "", "usd": a.get("volume_in_usd")})
    return sorted(out, key=lambda x: x["ts"])          # oldest first (ISO strings sort correctly)


def early_buyers(trades: list, n: int = EARLY_N) -> set:
    counts = {}
    for t in trades:
        counts[t["wallet"]] = counts.get(t["wallet"], 0) + 1
    buys = [t["wallet"] for t in trades if t["kind"] == "buy"][:n]
    return {w for w in buys if counts[w] <= BOT_TRADES}


def learn_pool(state, chain: str, pool: str, trades: list) -> int:
    done = state.get_value(KEY_LEARNED) or []
    tag = f"{chain}|{pool}"
    if tag in done:
        return 0
    book = state.get_value(KEY) or {}
    cb = book.setdefault(chain, {})
    ws = early_buyers(trades)
    for w in ws:
        lst = cb.setdefault(w, [])
        if pool not in lst:
            lst.append(pool)
    state.set_value(KEY, book)
    state.set_value(KEY_LEARNED, (done + [tag])[-3000:])
    return len(ws)


def smart_set(state, chain: str) -> set:
    return {w for w, pools in ((state.get_value(KEY) or {}).get(chain) or {}).items() if len(pools) >= SMART_MIN_POOLS}


def check_pool(state, chain: str, pool: str, trades: list, now: float = None) -> int:
    """How many distinct smart wallets bought this pool; stored for the scanner."""
    smart = smart_set(state, chain)
    n = len({t["wallet"] for t in trades if t["kind"] == "buy" and t["wallet"] in smart})
    if n:
        hits = state.get_value(KEY_HITS) or {}
        hits[f"{chain}|{pool}"] = {"n": n, "ts": now or time.time()}
        state.set_value(KEY_HITS, hits)
    return n


def hit_n(state, chain: str, pool: str, now: float = None, max_age: float = 3600) -> int:
    e = (state.get_value(KEY_HITS) or {}).get(f"{chain}|{pool}")
    return int(e["n"]) if e and (now or time.time()) - e["ts"] < max_age else 0


def cycle(state, slugs: dict, candidates: list, fetch, trending_fetch, now: float = None,
          learn_per_cycle: int = 1, watch_per_cycle: int = 3) -> dict:
    """fetch(slug, pool) -> trades json ; trending_fetch(slug) -> pools json. Few calls per cycle (GeckoTerminal 30/min is shared)."""
    now = now or time.time()
    st = {"learned": 0, "watched": 0}
    # WATCH: freshest candidates that have a pool address
    fresh = sorted((c for c in candidates if c.get("pool") and c["chain"] in slugs), key=lambda c: -(c.get("created") or 0))
    for c in fresh[:watch_per_cycle]:
        if not smart_set(state, c["chain"]):
            continue
        body = fetch(slugs[c["chain"]], c["pool"])
        if body:
            check_pool(state, c["chain"], c["pool"], parse_trades(body), now)
            st["watched"] += 1
    # LEARN: one trending pool per cycle, rotating chains
    chains = sorted(slugs)
    if chains:
        chain = chains[int(now // 45) % len(chains)]
        tp = trending_fetch(slugs[chain])
        done = set(state.get_value(KEY_LEARNED) or [])
        for pool in (tp or {}).get("data", []) or []:
            addr = (pool.get("attributes") or {}).get("address")
            if not addr or f"{chain}|{addr}" in done:
                continue
            body = fetch(slugs[chain], addr)
            if body:
                st["learned"] += learn_pool(state, chain, addr, parse_trades(body))
            learn_per_cycle -= 1
            if learn_per_cycle <= 0:
                break
    return st

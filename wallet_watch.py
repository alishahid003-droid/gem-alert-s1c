"""Direct on-chain copy trading (15 Oct 2026). Watches the wallets of followed traders / KOLs straight from the Solana RPC,
so copy signals do NOT depend on the metered MadeOnSol feed (~190 calls/day) or on Fomo credits (0 right now).
Wallet addresses are harvested for free from every KOL-feed result (state kol_wallets_v1: {name: wallet}).
Each cycle polls a round-robin slice of wallets (getSignaturesForAddress), fetches only NEW transactions (getTransaction),
and flags a BUY = the wallet's own token balance of a non-stable mint went UP while its SOL went DOWN.
Writes state copy_buys_rpc_v1 {mint: {names, n, best_tier, ts}}; copy_signals.refresh merges it into copy_buys_v1.
Budget: WALLET_WATCH_MAX_CALLS_DAY (default 15000) counted in state, so the RPC key is never drained."""
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

KEY_WALLETS = "kol_wallets_v1"
KEY_SIGS = "wallet_watch_last_sig_v1"
KEY_BOOK = "copy_buys_rpc_v1"
KEY_CURSOR = "wallet_watch_cursor_v1"
IGNORE_MINTS = {"So11111111111111111111111111111111111111112",      # wSOL
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",    # USDC
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}    # USDT
KEEP_S = 7200


def _env_i(name: str, d: int) -> int:
    try:
        return int(os.environ.get(name, "") or d)
    except ValueError:
        return d


def detect_buys(tx: Optional[dict], wallet: str) -> List[str]:
    """Mints this wallet BOUGHT in a jsonParsed getTransaction result ([] for sells / transfers-out / failed tx)."""
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return []
    meta = tx.get("meta") or {}

    def bal(rows):
        out: Dict[str, float] = {}
        for r in rows or []:
            if r.get("owner") != wallet:
                continue
            ui = (r.get("uiTokenAmount") or {})
            v = ui.get("uiAmount")
            if v is None:
                try:
                    v = float(ui.get("uiAmountString") or 0)
                except ValueError:
                    v = 0.0
            out[r.get("mint")] = out.get(r.get("mint"), 0.0) + float(v or 0)
        return out
    pre, post = bal(meta.get("preTokenBalances")), bal(meta.get("postTokenBalances"))
    keys = (((tx.get("transaction") or {}).get("message") or {}).get("accountKeys")) or []
    names = [k.get("pubkey") if isinstance(k, dict) else k for k in keys]
    sol_down = False
    if wallet in names:
        i = names.index(wallet)
        try:
            sol_down = meta["postBalances"][i] < meta["preBalances"][i]
        except (KeyError, IndexError, TypeError):
            sol_down = False
    if not sol_down:
        return []
    return [m for m, v in post.items() if m and m not in IGNORE_MINTS and v > pre.get(m, 0.0)]


def harvest_wallets(trades: List[dict], roster: set, state) -> int:
    """Remember {kol_name: wallet_address} from KOL-feed trades (free, passive)."""
    book = dict(state.get_value(KEY_WALLETS) or {})
    n0 = len(book)
    for t in trades or []:
        name, w = t.get("kol_name"), t.get("wallet_address")
        if name and w and name in roster:
            book[name] = w
    if len(book) != n0:
        state.set_value(KEY_WALLETS, book)
    return len(book) - n0


def watch_set(state) -> dict:
    """{label: wallet}: followed KOL wallets + our own pump.fun smart-money roster (layer2b) + wallets that
    our logs show winning early (dev_rank smart_wallets_v1). All of them are watched on-chain the same way."""
    out = dict(state.get_value(KEY_WALLETS) or {})
    for key, pre in (("pumpfun_smart_money_roster", "pf"), ("smart_wallets_v1", "sw"), ("st_leaderboard_v1", "st")):
        try:
            for w in state.get_value(key) or []:
                if isinstance(w, str) and len(w) > 30:
                    out.setdefault(f"{pre}:{w[:6]}", w)
        except Exception:  # noqa: BLE001
            pass
    return out


def poll_cycle(state, rpc: Optional[Callable] = None, now: Optional[float] = None, roster=None, tier_of=None,
               max_wallets: int = 12) -> Tuple[int, List[str]]:
    """One round-robin slice. Returns (new buys recorded, notes)."""
    now = now or time.time()
    notes: List[str] = []
    if rpc is None:
        from executor.rpc_pool import rpc_call
        rpc = lambda method, params: rpc_call("solana", method, params, timeout=10)
    if tier_of is None:
        from layers import roster as _r
        tier_of = _r.tier_of
    wallets = watch_set(state)
    if not wallets:
        return 0, ["no wallets harvested yet"]
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    ck = f"wallet_watch_calls:{day}"
    used = int(state.get_value(ck) or 0)
    cap = _env_i("WALLET_WATCH_MAX_CALLS_DAY", 15000)
    names = sorted(wallets)
    cur = int(state.get_value(KEY_CURSOR) or 0) % len(names)
    slice_ = [names[(cur + i) % len(names)] for i in range(min(max_wallets, len(names)))]
    state.set_value(KEY_CURSOR, (cur + len(slice_)) % len(names))
    last = dict(state.get_value(KEY_SIGS) or {})
    book = {m: e for m, e in (state.get_value(KEY_BOOK) or {}).items() if now - float(e.get("ts") or 0) < KEEP_S}
    found = 0
    tw = {"Tier 1": 4, "Tier 2": 3}
    for name in slice_:
        if used >= cap:
            notes.append("daily RPC budget reached")
            break
        w = wallets[name]
        params = [w, {"limit": 8}]
        if last.get(w):
            params[1]["until"] = last[w]
        r = rpc("getSignaturesForAddress", params)
        used += 1
        if not r.get("ok"):
            notes.append(f"sigs {name}: {str(r.get('reason'))[:40]}")
            continue
        sigs = [s for s in (r.get("result") or []) if not s.get("err")]
        if not sigs:
            continue
        first_run = not last.get(w)
        last[w] = sigs[0]["signature"]
        if first_run:
            continue                                  # first look at a wallet only sets the cursor (no stale history)
        for s in sigs[:5]:
            if used >= cap:
                break
            tr = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            used += 1
            if not tr.get("ok"):
                continue
            ts = float(s.get("blockTime") or now)
            if now - ts > 3600:
                continue
            for mint in detect_buys(tr.get("result"), w):
                e = book.setdefault(mint, {"names": [], "n": 0, "best_tier": 0, "ts": 0.0, "src": ["rpc"]})
                if name not in e["names"]:
                    e["names"].append(name)
                e["n"] = len(e["names"])
                e["best_tier"] = max(e["best_tier"], tw.get(tier_of(name), 2))
                e["ts"] = max(e["ts"], ts)
                found += 1
    state.set_value(ck, used)
    state.set_value(KEY_SIGS, last)
    state.set_value(KEY_BOOK, book)
    return found, notes

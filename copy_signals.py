"""Copy-trading bridge (15 Oct 2026): turns KOL-wallet buys (MadeOnSol feed) and Fomo followed / new-wallet buys
into entry confirmations for the early-watch sniper.
  kol_buy          a followed trader / KOL wallet bought this coin (any tier)
  kol_converge     2+ DIFFERENT followed traders bought it
  fomo_new_wallet  a qualified NEW wallet (>= $5k, positive 7d+30d PnL, not insider) bought it (needs Fomo credits)
State key copy_buys_v1: {mint: {names:[...], n, best_tier, new_wallet, kol, ts, src:[...]}} pruned after 2 h.
Pure functions + one refresh(); every fetch is injectable so it can be tested without network."""
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

KEY = "copy_buys_v1"
WINDOW_S = 3600
KEEP_S = 7200
_TIER_W = {"Tier 1": 4, "Tier 2": 3, "Tier 3": 2, "Tier 4": 2, "watchlist": 2}


def _mint(t: dict):
    return t.get("token_mint") or t.get("mint") or t.get("token_address")


def _ts(t: dict, now: float) -> float:
    v = t.get("traded_at")
    if v:
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return now


def _add(book: dict, mint: str, name: str, ts: float, tier_w: int, src: str, new_wallet: bool = False):
    e = book.setdefault(mint, {"names": [], "n": 0, "best_tier": 0, "new_wallet": False, "kol": False, "ts": 0.0, "src": []})
    if name not in e["names"]:
        e["names"].append(name)
    e["n"] = len(e["names"])
    e["best_tier"] = max(e["best_tier"], tier_w)
    e["new_wallet"] = e["new_wallet"] or new_wallet
    e["kol"] = e["kol"] or (not new_wallet)
    e["ts"] = max(e["ts"], ts)
    if src not in e["src"]:
        e["src"].append(src)


def from_kol_trades(trades: List[dict], roster: set, tier_of: Callable[[str], str], now: float, book: Optional[dict] = None) -> dict:
    book = book if book is not None else {}
    for t in trades or []:
        if (t.get("action") or "").lower() != "buy":
            continue
        name, m = t.get("kol_name"), _mint(t)
        if not name or not m or name not in roster:
            continue
        ts = _ts(t, now)
        if now - ts > WINDOW_S:
            continue
        _add(book, m, name, ts, _TIER_W.get(tier_of(name), 2), "kol")
    return book


def from_fomo_signals(signals: List[dict], now: float, book: Optional[dict] = None) -> dict:
    book = book if book is not None else {}
    for s in signals or []:
        kind, m = s.get("kind"), s.get("token_address")
        if kind not in ("buy", "new_wallet_buy") or not m:
            continue
        ts = float(s.get("ts") or now)
        if now - ts > WINDOW_S:
            continue
        nw = kind == "new_wallet_buy"
        _add(book, m, str(s.get("trader") or "?"), ts, 3 if nw else _TIER_W.get(s.get("tier"), 3) if isinstance(s.get("tier"), str) else 3,
             "fomo_new" if nw else "fomo", new_wallet=nw)
    return book


def refresh(state, now: Optional[float] = None, kol_fetch: Optional[Callable] = None) -> Tuple[int, List[str]]:
    """Rebuild copy_buys_v1 from the freshest sources. Returns (mints, notes)."""
    now = now or time.time()
    notes: List[str] = []
    old = state.get_value(KEY) or {}
    book = {m: e for m, e in old.items() if now - float(e.get("ts") or 0) < KEEP_S}
    try:
        from layers import roster
        use_default = kol_fetch is None
        if kol_fetch is None:
            from layers import kol_feed
            kol_fetch = lambda: kol_feed.fetch_kol_feed_both("solana", 100)
        res = kol_fetch() or {}
        if not res.get("ok") and use_default:
            cached = kol_feed.cached_feed("solana")          # scheduler's last successful poll (shared budget)
            if cached.get("ok"):
                notes.append(f"kol: using cached feed ({res.get('reason', '')[:30]})")
                res = cached
        if res.get("ok"):
            from_kol_trades(res.get("buy_trades") or [], roster.CONVERGENCE_ROSTER, roster.tier_of, now, book)
            try:
                import wallet_watch
                wallet_watch.harvest_wallets((res.get("buy_trades") or []) + (res.get("sell_trades") or []), roster.CONVERGENCE_ROSTER, state)
            except Exception:  # noqa: BLE001
                pass
        elif res.get("reason"):
            notes.append("kol: " + str(res.get("reason"))[:60])
    except Exception as e:  # noqa: BLE001
        notes.append(f"kol error {type(e).__name__}")
    try:
        for m, e in (state.get_value("copy_buys_rpc_v1") or {}).items():       # direct on-chain wallet watch (wallet_watch.py)
            if now - float(e.get("ts") or 0) < WINDOW_S:
                for nm in e.get("names") or []:
                    _add(book, m, nm, float(e.get("ts") or now), int(e.get("best_tier") or 2), "rpc")
    except Exception as e:  # noqa: BLE001
        notes.append(f"rpc merge error {type(e).__name__}")
    try:
        from_fomo_signals(state.get_value("fomo_signals") or [], now, book)
    except Exception as e:  # noqa: BLE001
        notes.append(f"fomo error {type(e).__name__}")
    state.set_value(KEY, book)
    return len(book), notes


def info(state, mint: str) -> Optional[dict]:
    return (state.get_value(KEY) or {}).get(mint)

"""Layer 20 - notable-wallet impersonation watch (Ali's request, 21 Oct 2026, after the Kimchi incident):
someone posts their own wallet publicly, then an attacker mints a brand-new coin and sends a big chunk of its
supply to THAT REAL wallet -- not because the person launched anything, but because the market sees "known
wallet holds a chunk of supply" and assumes an endorsement. The $Kimchi memecoin (riding trader @kimchi1x's
fame after his reported $40M TRUMP trade) reached ~$38.7-40M mcap within about an hour on that kind of
attention alone.

This can't be auto-discovered: there is no feed of "which real person just posted which wallet", so
notable_wallets.json (same folder) is a small registry Ali grows himself the moment he sees it happen, exactly
the way bot_wallets.txt grows. What THIS script automates is the expensive, continuous part: watching every
new coin's largest holders for a match against that registry, in near-real-time, so Ali never has to catch it
by eye again once a wallet is in the list.

Data/cost: Solana's own getTokenLargestAccounts + getTokenSupply + getAccountInfo (to resolve a token
ACCOUNT to its owner WALLET) via the free RPC pool already used by layer0_scoring.py -- zero MadeOnSol/Solana
Tracker budget, same pool Ali's Helius key already funds. Checks coins up to NOTABLE_WATCH_MAX_AGE_MIN old
(default 240 -- longer than layer19's window, because the attacker can send supply to the wallet any time
after launch, not just at mint) so a late impersonation send still gets caught on a later pass.

Alert wording is deliberately careful: this NEVER claims the named person launched or endorsed anything --
only that the market is likely to read it that way, which is itself the tradable (and reportable-as-fraud)
signal. Shadow-logs every hit's outcome (+15m/1h/6h/24h) to notable_wallet_log/*.jsonl, same convention as
layer18/19, so the pattern's real hit rate can be proven before this is ever used for anything but an alert."""
import glob
import json
import os
import time
from typing import Callable, Dict, List, Optional

import state
from executor.rpc_pool import rpc_call

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(HERE, "notable_wallet_log")
EARLY_DIR = os.path.join(HERE, "early_log")
REGISTRY_PATH = os.path.join(HERE, "notable_wallets.json")
SEEN_KEY = "notable_wallet_seen_mints"
PENDING_KEY = "notable_wallet_pending"
OUTCOME_HORIZONS_MIN = (15, 60, 360, 1440)


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def load_registry(path: str = REGISTRY_PATH) -> Dict[str, dict]:
    """{wallet: {name, chain, source, added_ts, confirmed}}. {} if the file doesn't exist or has no
    entries -- this layer does nothing (never invents a name) until Ali adds at least one real entry.
    An entry with confirmed=False still fires (so it can be watched) but format_alert() flags it as
    unverified rather than stating the match as settled fact."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    w = d.get("wallets")
    return w if isinstance(w, dict) else {}


def recent_creates(max_age_min: float = 240.0, min_age_min: float = 0.0, now: Optional[float] = None,
                    directory: Optional[str] = None) -> List[dict]:
    """Today's/yesterday's 'create' rows aged between min/max. Wider window than layer19 on purpose: the
    impersonation transfer can land hours after launch, not only at mint."""
    now = now if now is not None else time.time()
    out = []
    files = sorted(glob.glob(os.path.join(directory or EARLY_DIR, "*.jsonl")))[-2:]
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as fh:
                for line in fh:
                    if '"create"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("t") != "create" or not row.get("mint"):
                        continue
                    age_min = (now - row["ts"]) / 60.0
                    if min_age_min <= age_min <= max_age_min:
                        out.append(row)
        except OSError:
            continue
    return out


def fetch_largest_holder_owners(mint: str, top_n: int = 10, rpc: Callable = rpc_call) -> List[dict]:
    """[{owner, token_account, pct}] for the mint's largest token accounts, owner-resolved. Pure free RPC
    (getTokenLargestAccounts + getTokenSupply + getAccountInfo per account); [] on any failure, never guesses."""
    largest = rpc("solana", "getTokenLargestAccounts", [mint])
    if not largest.get("ok"):
        return []
    accounts = ((largest.get("result") or {}).get("value") or [])[:top_n]
    if not accounts:
        return []
    supply_resp = rpc("solana", "getTokenSupply", [mint])
    if not supply_resp.get("ok"):
        return []
    total = ((supply_resp.get("result") or {}).get("value") or {}).get("uiAmount")
    if not total:
        return []
    out = []
    for a in accounts:
        acct_addr = a.get("address")
        amt = float(a.get("uiAmount") or 0)
        if not acct_addr or amt <= 0:
            continue
        info = rpc("solana", "getAccountInfo", [acct_addr, {"encoding": "jsonParsed"}])
        if not info.get("ok"):
            continue
        parsed = (((info.get("result") or {}).get("value") or {}).get("data") or {}).get("parsed") or {}
        owner = (parsed.get("info") or {}).get("owner")
        if owner:
            out.append({"owner": owner, "token_account": acct_addr, "pct": min(1.0, amt / total)})
    return out


def format_alert(mint: str, sym: str, hits: List[dict], registry: Dict[str, dict]) -> str:
    best = max(hits, key=lambda h: h["pct"])
    rec = registry.get(best["owner"], {})
    name = rec.get("name", "unknown")
    tag = "" if rec.get("confirmed") else " (UNVERIFIED wallet match -- source not independently confirmed)"
    lines = [
        "\U0001F6A8 NOTABLE-WALLET FUNDED (possible fake-endorsement pump)" + tag,
        f"{sym}  `{mint}`",
        f"{best['pct']*100:.0f}% of supply sent to {name}'s wallet ({best['owner'][:8]}...).",
        "This does NOT mean they launched or endorsed it -- someone may have sent it there on purpose to",
        "create that impression. The impression itself is the tradable (and reportable) signal.",
        "[alert + shadow log only -- no trade]",
    ]
    try:
        from links import render_links_line
        lk = render_links_line("solana", mint)
        if lk:
            lines.append(lk)
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _log(row: dict, now: float, directory: Optional[str] = None) -> None:
    d = directory or LOG_DIR
    try:
        os.makedirs(d, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with open(os.path.join(d, f"{day}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def update_outcomes(price_of: Callable[[List[str]], Dict[str, float]], now: float, directory: Optional[str] = None) -> int:
    pend = state.get_value(PENDING_KEY)
    pend = pend if isinstance(pend, list) else []
    due = [p for p in pend if any((now - p["ts"]) / 60 >= h and str(h) not in p["done"] for h in OUTCOME_HORIZONS_MIN)]
    if not due:
        return 0
    prices = price_of(sorted({p["mint"] for p in due}))
    n = 0
    for p in due:
        px = prices.get(p["mint"])
        if not px or not p.get("price"):
            continue
        for h in OUTCOME_HORIZONS_MIN:
            if (now - p["ts"]) / 60 >= h and str(h) not in p["done"]:
                _log({"t": "outcome", "ts": now, "mint": p["mint"], "horizon_min": h,
                      "mult": round(px / p["price"], 4)}, now, directory)
                p["done"].append(str(h))
                n += 1
    keep = [p for p in pend if len(p["done"]) < len(OUTCOME_HORIZONS_MIN) and (now - p["ts"]) < 2 * 86400]
    state.set_value(PENDING_KEY, keep[-400:])
    return n


def run_cycle(send: Optional[Callable[[str], dict]] = None, *, rpc: Callable = rpc_call,
              dex_batch: Optional[Callable] = None, registry: Optional[Dict[str, dict]] = None,
              creates: Optional[List[dict]] = None, now: Optional[float] = None,
              log_dir: Optional[str] = None) -> dict:
    """One pass. Never raises. Returns counts + errors."""
    now = now if now is not None else time.time()
    if send is None:
        from telegram_alert import send_telegram_message as send
    if dex_batch is None:
        from layers.layer14_revival import fetch_dexscreener_batch
        dex_batch = lambda addrs: fetch_dexscreener_batch("solana", addrs)  # noqa: E731
    errors: List[str] = []
    res = {"registry_size": 0, "candidates": 0, "checked": 0, "hits": 0, "alerts": 0, "outcomes": 0, "errors": errors}

    registry = registry if registry is not None else load_registry()
    res["registry_size"] = len(registry)
    if not registry:
        errors.append("notable_wallets.json is empty -- add at least one real wallet entry to enable this layer")
        return res

    seen = state.get_value(SEEN_KEY)
    seen = seen if isinstance(seen, list) else []
    seen_set = set(seen)
    candidates = creates if creates is not None else recent_creates(now=now, max_age_min=_f("NOTABLE_WATCH_MAX_AGE_MIN", 240.0))
    candidates = [c for c in candidates if c["mint"] not in seen_set]
    res["candidates"] = len(candidates)

    pend = state.get_value(PENDING_KEY)
    pend = pend if isinstance(pend, list) else []

    def guard(name, fn, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return default

    min_pct = _f("NOTABLE_WATCH_MIN_PCT", 0.10)
    # Fixed 25 Oct 2026: layer20 now runs every poll-radar cycle (moved ahead of layer18/19
    # so it's no longer starved by the 540s cycle timeout -- see scheduler.py). But with no
    # internal time budget, a cold seen_set (first run, or a long NOTABLE_WATCH_MAX_AGE_MIN
    # backlog) could itself eat the whole 540s doing RPC-heavy holder lookups one mint at a
    # time, hanging the ENTIRE poll-radar cycle (layer18/19 never even starting) instead of
    # just this layer's own slice. Bail out early and leave the rest for the next cycle --
    # they stay candidates (not marked seen) so nothing is skipped, just spread over time.
    budget_s = _f("NOTABLE_WATCH_TIME_BUDGET_S", 45.0)
    deadline = time.time() + budget_s
    for c in candidates:
        if time.time() >= deadline:
            errors.append(f"time budget ({budget_s:.0f}s) hit -- {res['candidates'] - res['checked']} candidate(s) deferred to next cycle")
            break
        mint = c["mint"]
        res["checked"] += 1
        holders = guard("fetch_largest_holder_owners", lambda c=c: fetch_largest_holder_owners(mint, rpc=rpc), [])
        seen_set.add(mint)
        if not holders:
            continue
        hits = [h for h in holders if h["owner"] in registry and h["pct"] >= min_pct]
        if not hits:
            continue
        res["hits"] += 1
        px = guard("price", lambda: dex_batch([mint]).get(mint))
        from layers.layer16_catalyst_radar import _num
        price = _num((px or {}).get("priceUsd")) if px else None
        sym = c.get("sym") or ""
        _log({"t": "alert", "ts": now, "mint": mint, "sym": sym,
              "hits": [{"owner": h["owner"], "pct": h["pct"]} for h in hits],
              "names": [registry[h["owner"]]["name"] for h in hits], "price": price}, now, log_dir)
        if price:
            pend.append({"mint": mint, "price": price, "ts": now, "done": []})
        if guard("send", lambda: send(format_alert(mint, sym, hits, registry))) is not None:
            res["alerts"] += 1
    state.set_value(SEEN_KEY, sorted(seen_set)[-3000:])
    state.set_value(PENDING_KEY, pend[-400:])

    def _price_lookup(mints: List[str]) -> Dict[str, float]:
        from layers.layer16_catalyst_radar import _num
        out = {}
        for mint, pair in dex_batch(mints).items():
            px = _num(pair.get("priceUsd")) if pair else None
            if px:
                out[mint] = px
        return out
    res["outcomes"] = guard("outcomes", lambda: update_outcomes(_price_lookup, now, log_dir), 0) or 0
    return res

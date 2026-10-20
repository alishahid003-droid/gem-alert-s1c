"""Layer 19 - "insider/sniper" watch (Ali's idea): alert when a wallet from the validated smart-wallet list
(built offline by wallet_edge_builder.py from hundreds of real graduated coins, not a guess) is among the
FIRST BUYERS of a brand-new pump.fun coin -- i.e. someone with a real track record of getting in before a
coin runs is already in this one too.

Needs grad_runners/smart_wallets.csv to exist (wallet_edge_builder.py writes it). Until that file has been
validated across enough coins, this layer does nothing but say so -- it never invents a wallet list.

Credit-aware on purpose: this checks Solana Tracker's first-buyers endpoint, which is also used by the
monthly-budgeted leaderboard refresh (solana_tracker.py: free plan = 2.5K requests/month). A hard daily cap
(SMART_WALLET_DAILY_CAP, default 20) keeps this layer from eating that budget; it resets at UTC midnight and
fails closed (does nothing, no error) once hit for the day.

Alerts + shadow log only -- never a trade. Logs every hit to smart_wallet_log/*.jsonl with the price at alert
time and again at +15m/1h/6h/24h, exactly like layer18, so the edge can be proven or killed with real data
before any money risks it."""
import glob
import json
import os
import time
from typing import Callable, Dict, List, Optional

import state
from utils.http import get_json

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "smart_wallet_log")
EARLY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "early_log")
WALLET_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "grad_runners", "smart_wallets.csv")
STU = "https://data.solanatracker.io"
SEEN_KEY = "smart_wallet_seen_mints"
PENDING_KEY = "smart_wallet_pending"
CREDIT_DAY_KEY = "smart_wallet_credit_day"
OUTCOME_HORIZONS_MIN = (15, 60, 360, 1440)


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def load_smart_wallets(path: str = WALLET_CSV) -> Dict[str, dict]:
    """{wallet: {runner_hits, dud_hits}} from wallet_edge_builder.py's output. {} if it doesn't exist yet."""
    out = {}
    try:
        import csv
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                w = row.get("wallet")
                if w:
                    out[w] = {"runner_hits": int(row.get("runner_hits") or 0), "dud_hits": int(row.get("dud_hits") or 0)}
    except (OSError, ValueError):
        return {}
    return out


def recent_creates(max_age_min: float = 12.0, min_age_min: float = 2.0, now: Optional[float] = None,
                    directory: Optional[str] = None) -> List[dict]:
    """Today's and yesterday's 'create' rows aged between min/max -- old enough that first buyers exist,
    young enough that a smart-wallet hit is still actionable (not chasing a coin that's already run)."""
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


def _credits_available(now: float) -> int:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    rec = state.get_value(CREDIT_DAY_KEY)
    rec = rec if isinstance(rec, dict) and rec.get("day") == day else {"day": day, "used": 0}
    cap = int(_f("SMART_WALLET_DAILY_CAP", 20))
    return max(0, cap - rec["used"]), rec, day


def _spend_credit(rec: dict, day: str) -> None:
    rec["used"] = rec.get("used", 0) + 1
    state.set_value(CREDIT_DAY_KEY, {"day": day, "used": rec["used"]})


def fetch_first_buyers(mint: str, fetch_json: Callable = get_json) -> List[str]:
    key = (os.environ.get("SOLANATRACKER_API_KEY") or "").strip()
    if not key:
        return []
    r = fetch_json(f"{STU}/first-buyers/{mint}", headers={"x-api-key": key}, timeout=15)
    d = r.get("json") if isinstance(r, dict) and r.get("ok") else None
    L = d if isinstance(d, list) else (d.get("buyers") or d.get("data") or []) if isinstance(d, dict) else []
    return [x["wallet"] for x in L if isinstance(x, dict) and x.get("wallet")]


def format_alert(mint: str, sym: str, hits: List[str], wallets: Dict[str, dict]) -> str:
    best = max(hits, key=lambda w: wallets.get(w, {}).get("runner_hits", 0))
    rec = wallets.get(best, {})
    lines = [f"🕵️ SMART-WALLET EARLY BUY", f"{sym}  `{mint}`",
             f"{len(hits)} tracked wallet(s) already bought in -- best one: {best[:8]}... "
             f"(seen early in {rec.get('runner_hits', 0)} past runners, {rec.get('dud_hits', 0)} duds)",
             "[alert + shadow log only -- no trade]"]
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
                _log({"t": "outcome", "ts": now, "mint": p["mint"], "horizon_min": h, "mult": round(px / p["price"], 4)}, now, directory)
                p["done"].append(str(h))
                n += 1
    keep = [p for p in pend if len(p["done"]) < len(OUTCOME_HORIZONS_MIN) and (now - p["ts"]) < 2 * 86400]
    state.set_value(PENDING_KEY, keep[-400:])
    return n


def run_cycle(send: Optional[Callable[[str], dict]] = None, *, fetch_json: Callable = get_json,
              dex_batch: Optional[Callable] = None, wallets: Optional[Dict[str, dict]] = None,
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
    res = {"wallets_loaded": 0, "candidates": 0, "checked": 0, "hits": 0, "alerts": 0, "outcomes": 0, "errors": errors}

    wallets = wallets if wallets is not None else load_smart_wallets()
    res["wallets_loaded"] = len(wallets)
    if not wallets:
        errors.append("no grad_runners/smart_wallets.csv yet -- run wallet_edge_builder.py first")
        return res

    import bot_wallets
    bots = bot_wallets.load()
    wallets = {w: v for w, v in wallets.items() if w not in bots}

    seen = state.get_value(SEEN_KEY)
    seen = seen if isinstance(seen, list) else []
    seen_set = set(seen)
    candidates = creates if creates is not None else recent_creates(now=now)
    candidates = [c for c in candidates if c["mint"] not in seen_set]
    res["candidates"] = len(candidates)

    avail, credit_rec, day = _credits_available(now)
    pend = state.get_value(PENDING_KEY)
    pend = pend if isinstance(pend, list) else []

    def guard(name, fn, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return default

    for c in candidates:
        if avail <= 0:
            errors.append("daily Solana Tracker credit cap reached for smart-wallet checks")
            break
        mint = c["mint"]
        avail -= 1
        _spend_credit(credit_rec, day)
        res["checked"] += 1
        buyers = guard("fetch_first_buyers", lambda c=c: fetch_first_buyers(mint, fetch_json), [])
        seen_set.add(mint)
        if not buyers:
            continue
        hits = [w for w in set(buyers) if w in wallets]
        if not hits:
            continue
        res["hits"] += 1
        px = guard("price", lambda: dex_batch([mint]).get(mint))
        from layers.layer16_catalyst_radar import _num
        price = _num((px or {}).get("priceUsd")) if px else None
        sym = c.get("sym") or ""
        _log({"t": "alert", "ts": now, "mint": mint, "sym": sym, "hit_wallets": hits,
              "best_runner_hits": max(wallets[w]["runner_hits"] for w in hits), "price": price}, now, log_dir)
        if price:
            pend.append({"mint": mint, "price": price, "ts": now, "done": []})
        if guard("send", lambda: send(format_alert(mint, sym, hits, wallets))) is not None:
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

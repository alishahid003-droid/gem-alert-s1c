"""Layer 18 - catalyst revival detector (influencer / news / "someone posted a picture" -> dumped coin wakes up).

Why: a coin can sit dead for days (dev sold, chart flat) and then explode the moment a known person, a
headline or a viral picture points at it (case study: FTR / "Roadster"). Launch-time features never predict
that, so the only reliable edge is to SEE THE CATALYST EARLY and map it to the coin by name/ticker.

How (all free, no API keys, every step fails closed):
  1. Collect "what the world is talking about right now": Google Trends, Google News, Hacker News, CoinDesk
     (the same feeds layer16 uses) + Reddit hot RSS + any feeds Ali adds in CATALYST_FEEDS +
     TRUSTED sources (CATALYST_TRUSTED_FEEDS: RSS of an influencer, a public Telegram channel page
     https://t.me/s/<channel>, an RSS-bridge URL for an X account). One mention in a trusted source is enough.
  2. Turn headlines into terms (layer16.extract_terms) and flag terms that are NEW since last cycle (a burst).
  3. Find coins that carry that name: (a) DexScreener search for the hottest terms (any age, not just new
     launches) and (b) our own early_log create-registry (every pump.fun mint we saw, so dead coins DexScreener
     search would rank low are still found).
  4. Judge the coin: liquidity floor, age >= CATALYST_MIN_AGE_H (younger coins are layer16's job), clone
     filter (deepest pool per term is the leader), and market reaction (volume surge vs its own 24h pace,
     1h price change). HEADS-UP = named but not moved yet (the early one); CONFIRMED = named AND waking up;
     LATE = already > +400% in 1h (logged only, never alerted).
  5. Alert on Telegram and write a SHADOW LOG (catalyst_log/YYYY-MM-DD.jsonl) with the price at alert and
     +15m/1h/6h/24h later, so catalyst_report.py can prove or kill the edge before any money is risked.
Alerts and logs only. Never a real order."""
import glob
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional

import state
from layers import layer16_catalyst_radar as r16

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "catalyst_log")
EARLY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "early_log")
SEEN_KEY = "catalyst_seen"
PREV_TERMS_KEY = "catalyst_prev_terms"
PENDING_KEY = "catalyst_pending"
OUTCOME_HORIZONS_MIN = (15, 60, 360, 1440)
EXTRA_FEEDS = (
    "https://www.reddit.com/r/CryptoCurrency/hot/.rss",
    "https://www.reddit.com/r/solana/hot/.rss",
    "https://www.reddit.com/r/memecoins/hot/.rss",
)
_TME = re.compile(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,})")
_TME_MSG = re.compile(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _f(name: str, default: float) -> float:
    return r16._f(name, default)


def _csv(name: str, default: Iterable[str] = ()) -> List[str]:
    v = os.environ.get(name, "")
    return [x.strip() for x in v.split(",") if x.strip()] if v else list(default)


# ------------------------------------------------------------------ feeds
def parse_tme_messages(html: Optional[str], limit: int = 25) -> List[str]:
    """Public Telegram channel preview page (t.me/s/name) -> last message texts (tags stripped)."""
    if not html:
        return []
    out = []
    for m in _TME_MSG.findall(html)[-limit:]:
        t = re.sub(r"\s+", " ", _TAG.sub(" ", m.replace("<br/>", ". ").replace("<br>", ". "))).strip()
        if t:
            out.append(t[:300])
    return out


def _titles_for(url: str, text: Optional[str]) -> List[str]:
    if "t.me/" in url:
        return parse_tme_messages(text)
    return r16.parse_feed_titles(text)


def normalise_feed_url(u: str) -> str:
    """'@somechannel' or 'https://t.me/somechannel' -> the t.me/s/ preview URL; other URLs unchanged."""
    u = u.strip()
    if u.startswith("@"):
        return f"https://t.me/s/{u[1:]}"
    m = _TME.search(u)
    return f"https://t.me/s/{m.group(1)}" if m else u


def build_hot(feeds: Dict[str, List[str]], trusted: Iterable[str] = (), prev: Optional[Iterable[str]] = None) -> Dict[str, dict]:
    """Hot terms with score + 'fresh' (not hot last cycle) + 'trusted' (named by a source Ali chose).
    Trusted sources: ONE mention is enough -- that is exactly the 'influencer posted it' case."""
    hot = dict(r16.hot_terms(feeds))
    trusted = set(trusted)
    for url, titles in feeds.items():
        if url not in trusted:
            continue
        for t in titles:
            for term in r16.extract_terms(t):
                cur = hot.get(term)
                if cur is None:
                    hot[term] = {"score": 6, "feeds": 1, "trend": False, "trusted": True}
                else:
                    cur["score"] += 5
                    cur["trusted"] = True
    prev = set(prev or [])
    for term, info in hot.items():
        info.setdefault("trusted", False)
        info["fresh"] = term not in prev
    return hot


# ------------------------------------------------------------------ matching
def name_matches(term: str, sym: str, name: str) -> bool:
    """Same rule as layer16 (single word must equal a word/the whole name; multi-word needs all parts or the
    joined form equal to symbol/name) but on a plain symbol + name pair."""
    words = {r16._norm(w) for w in (name or "").replace("/", " ").split() if r16._norm(w)}
    sym_n, joined = r16._norm(sym or ""), r16._norm(name or "")
    words.add(sym_n)
    parts = term.split()
    if len(parts) == 1:
        return len(term) >= 4 and (term in words or term == joined)
    return all(p in words for p in parts) or "".join(parts) in (sym_n, joined)


def pair_to_coin(p: dict, now: Optional[float] = None) -> Optional[dict]:
    """DexScreener pair -> flat coin dict (None if unusable)."""
    now = now if now is not None else time.time()
    base = p.get("baseToken") or {}
    mint = base.get("address")
    if not mint:
        return None
    n = r16._num
    created = n(p.get("pairCreatedAt"))
    vol, pc, tx = p.get("volume") or {}, p.get("priceChange") or {}, p.get("txns") or {}
    h1 = tx.get("h1") or {}
    return {"chain": p.get("chainId"), "mint": mint, "sym": base.get("symbol") or "", "name": base.get("name") or "",
            "liq": n((p.get("liquidity") or {}).get("usd")) or 0.0, "mcap": n(p.get("marketCap")) or n(p.get("fdv")),
            "price": n(p.get("priceUsd")), "age_min": (now - created / 1000.0) / 60.0 if created else None,
            "vol_h1": n(vol.get("h1")) or 0.0, "vol_h24": n(vol.get("h24")) or 0.0,
            "chg_m5": n(pc.get("m5")), "chg_h1": n(pc.get("h1")), "chg_h6": n(pc.get("h6")), "chg_h24": n(pc.get("h24")),
            "buys_h1": h1.get("buys") or 0, "sells_h1": h1.get("sells") or 0}


def surge_ratio(c: dict) -> float:
    """Volume in the last hour vs this coin's own average hour over the previous 23h. Dormant coin with $50 of
    hourly volume that suddenly does $5,000 -> 100x. Floor on the base keeps dead-flat coins from dividing by 0."""
    base = max(((c.get("vol_h24") or 0) - (c.get("vol_h1") or 0)) / 23.0, 25.0)
    return (c.get("vol_h1") or 0) / base


def classify(c: dict) -> str:
    """HEADS-UP (named, not moved), CONFIRMED (named + waking), LATE (>+400% in 1h, too late to chase)."""
    h1 = c.get("chg_h1") or 0
    if h1 > _f("CATALYST_LATE_PCT", 400):
        return "LATE"
    if surge_ratio(c) >= _f("CATALYST_SURGE_X", 4) and (c.get("vol_h1") or 0) >= _f("CATALYST_MIN_VOL_H1", 500) \
            or h1 >= _f("CATALYST_CONFIRM_PCT", 25):
        return "CONFIRMED"
    return "HEADS-UP"


def pick_matches(hot: Dict[str, dict], coins: List[dict]) -> List[dict]:
    """Best hot term per coin, then the deepest-liquidity coin per term is the leader (others = clones)."""
    min_liq, min_age = _f("CATALYST_MIN_LIQ_USD", 8_000), _f("CATALYST_MIN_AGE_H", 12) * 60
    found: Dict[str, dict] = {}
    for c in coins:
        if c["liq"] < min_liq or c["age_min"] is None or c["age_min"] < min_age:
            continue
        best = None
        for term, info in hot.items():
            if name_matches(term, c["sym"], c["name"]) and (best is None or info["score"] > hot[best]["score"]):
                best = term
        if best:
            found[c["mint"]] = {"coin": c, "term": best, "info": hot[best]}
    groups: Dict[str, List[dict]] = {}
    for m in found.values():
        groups.setdefault(m["term"], []).append(m)
    out = []
    for term, ms in groups.items():
        ms.sort(key=lambda m: -m["coin"]["liq"])
        for i, m in enumerate(ms):
            out.append({**m, "leader": i == 0, "cluster": len(ms)})
    return out


# ------------------------------------------------------------------ registry (our own early_log)
def load_registry(days: int = 14, directory: Optional[str] = None) -> Dict[str, dict]:
    """{mint: {sym, name}} for every create row in the last `days` early_log files."""
    reg: Dict[str, dict] = {}
    files = sorted(glob.glob(os.path.join(directory or EARLY_DIR, "*.jsonl")))[-days:]
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
                    if row.get("t") == "create" and row.get("mint"):
                        reg[row["mint"]] = {"sym": row.get("sym") or "", "name": row.get("name") or ""}
        except OSError:
            continue
    return reg


def registry_hits(hot: Dict[str, dict], registry: Dict[str, dict], cap: int = 60) -> List[str]:
    """Mints in our registry whose name/ticker matches a hot term, hottest terms first (capped)."""
    terms = sorted(hot, key=lambda t: -hot[t]["score"])[:80]
    hits: List[str] = []
    for mint, v in registry.items():
        for t in terms:
            if name_matches(t, v["sym"], v["name"]):
                hits.append(mint)
                break
        if len(hits) >= cap:
            break
    return hits


# ------------------------------------------------------------------ output
def format_alert(m: dict, verdict: str) -> str:
    c, info = m["coin"], m["info"]
    who = "trusted source" if info.get("trusted") else ("trending" if info.get("trend") else f"{info.get('feeds')} feeds")
    head = {"HEADS-UP": "HEADS-UP (named, not moved yet)", "CONFIRMED": "CONFIRMED (named + waking up)"}.get(verdict, verdict)
    lines = [f"🔔 CATALYST REVIVAL - {head}",
             f"{r16._clean(c['sym'])}  `{c['mint']}`",
             f"'{m['term']}' is in the news ({who}{', NEW this cycle' if info.get('fresh') else ''}); coin is "
             f"{(c['age_min'] or 0) / 60:.0f}h old" + ("" if m["leader"] else f" -- CLONE (1 of {m['cluster']}, not the deepest pool)"),
             f"[MCap: ${(c['mcap'] or 0):,.0f}] [Liq: ${c['liq']:,.0f}] [1h: {c['chg_h1'] if c['chg_h1'] is not None else '?'}%] "
             f"[Vol x{surge_ratio(c):.1f}] [24h: {c['chg_h24'] if c['chg_h24'] is not None else '?'}%]",
             "[alert + shadow log only - no trade]"]
    try:
        from links import render_links_line
        lk = render_links_line("solana", c["mint"])
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
    """For logged alerts still waiting on a horizon, fetch the current price and write an 'outcome' row."""
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
                _log({"t": "outcome", "ts": now, "mint": p["mint"], "term": p["term"], "verdict": p["verdict"],
                      "horizon_min": h, "mult": round(px / p["price"], 4)}, now, directory)
                p["done"].append(str(h))
                n += 1
    keep = [p for p in pend if len(p["done"]) < len(OUTCOME_HORIZONS_MIN) and (now - p["ts"]) < 2 * 86400]
    state.set_value(PENDING_KEY, keep[-400:])
    return n


# ------------------------------------------------------------------ orchestration
def _fetch_feeds(urls: List[str], fetch_text: Callable) -> Dict[str, List[str]]:
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {u: ex.submit(lambda u=u: _titles_for(u, fetch_text(u))) for u in urls}
        out = {}
        for u, f in futs.items():
            try:
                out[u] = f.result() or []
            except Exception:  # noqa: BLE001
                out[u] = []
    return out


def _search(term: str, fetch_json: Callable) -> List[dict]:
    data = fetch_json(r16.DEXSCREENER_SEARCH, {"q": term})
    pairs = (data or {}).get("pairs") if isinstance(data, dict) else None
    return [p for p in (pairs or []) if p.get("chainId") == "solana"]


def _price_batch(mints: List[str]) -> Dict[str, float]:
    from layers.layer14_revival import fetch_dexscreener_batch
    out = {}
    for mint, p in fetch_dexscreener_batch("solana", mints).items():
        px = r16._num(p.get("priceUsd"))
        if px:
            out[mint] = px
    return out


def run_cycle(send: Optional[Callable[[str], dict]] = None, *, fetch_json: Callable = r16._http_json,
              fetch_text: Callable = r16._http_text, dex_batch: Optional[Callable] = None,
              registry: Optional[Dict[str, dict]] = None, now: Optional[float] = None,
              log_dir: Optional[str] = None) -> dict:
    """One pass. Never raises. Returns counts + errors."""
    now = now if now is not None else time.time()
    if send is None:
        from telegram_alert import send_telegram_message as send
    if dex_batch is None:
        from layers.layer14_revival import fetch_dexscreener_batch
        dex_batch = lambda addrs: fetch_dexscreener_batch("solana", addrs)  # noqa: E731
    errors: List[str] = []
    res = {"hot": 0, "matches": 0, "alerts": 0, "logged": 0, "outcomes": 0, "errors": errors}

    def guard(name, fn, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return default

    trusted = [normalise_feed_url(u) for u in _csv("CATALYST_TRUSTED_FEEDS")]
    urls = list(dict.fromkeys(list(r16.DEFAULT_NEWS_FEEDS) + list(EXTRA_FEEDS)
                              + [normalise_feed_url(u) for u in _csv("CATALYST_FEEDS")] + trusted))
    feeds = _fetch_feeds(urls, fetch_text)
    if not any(feeds.values()):
        errors.append("no feed returned titles")
        return res
    prev = state.get_value(PREV_TERMS_KEY)
    hot = build_hot(feeds, trusted, (prev or {}).get("terms") if isinstance(prev, dict) else None)
    state.set_value(PREV_TERMS_KEY, {"ts": now, "terms": sorted(hot)[:600]})
    res["hot"] = len(hot)

    # coin discovery: (a) DexScreener search on the hottest specific terms, (b) our own create-registry
    ranked = [t for t in sorted(hot, key=lambda t: (-hot[t].get("trusted", False), -hot[t]["score"])) if len(t) >= 4]
    coins: Dict[str, dict] = {}
    def _discover():
        top = ranked[: int(_f("CATALYST_SEARCH_TERMS", 12))]
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(_search, t, fetch_json) for t in top]
            for f in futs:
                try:
                    for p in f.result():
                        c = pair_to_coin(p, now)
                        if c and (c["mint"] not in coins or c["liq"] > coins[c["mint"]]["liq"]):
                            coins[c["mint"]] = c
                except Exception as e:  # noqa: BLE001
                    errors.append(f"search: {type(e).__name__}")
    guard("search", _discover)
    def _registry():
        reg = registry if registry is not None else load_registry()
        mints = [m for m in registry_hits(hot, reg) if m not in coins]
        for mint, p in dex_batch(mints).items():
            c = pair_to_coin(p, now)
            if c:
                coins[c["mint"]] = c
    guard("registry", _registry)

    matches = pick_matches(hot, list(coins.values()))
    res["matches"] = len(matches)
    seen = state.get_value(SEEN_KEY)
    seen = seen if isinstance(seen, dict) else {}
    cooldown = _f("CATALYST_COOLDOWN_H", 6) * 3600
    max_alerts = int(_f("CATALYST_MAX_ALERTS", 5))
    pend = state.get_value(PENDING_KEY)
    pend = pend if isinstance(pend, list) else []
    for m in sorted(matches, key=lambda m: (not m["leader"], -m["info"]["score"])):
        c = m["coin"]
        verdict = classify(c)
        prev_seen = seen.get(c["mint"])
        # re-alert only when a HEADS-UP has since become CONFIRMED, or the cooldown passed
        if prev_seen and now - prev_seen["ts"] < cooldown and not (prev_seen["v"] == "HEADS-UP" and verdict == "CONFIRMED"):
            continue
        _log({"t": "alert", "ts": now, "mint": c["mint"], "sym": c["sym"], "term": m["term"], "verdict": verdict,
              "leader": m["leader"], "trusted": m["info"].get("trusted"), "fresh": m["info"].get("fresh"),
              "price": c["price"], "mcap": c["mcap"], "liq": c["liq"], "age_h": round((c["age_min"] or 0) / 60, 1),
              "chg_h1": c["chg_h1"], "chg_h24": c["chg_h24"], "surge_x": round(surge_ratio(c), 2)}, now, log_dir)
        res["logged"] += 1
        seen[c["mint"]] = {"ts": now, "v": verdict}
        if c["price"]:
            pend.append({"mint": c["mint"], "term": m["term"], "verdict": verdict, "price": c["price"], "ts": now, "done": []})
        if verdict != "LATE" and m["leader"] and res["alerts"] < max_alerts:
            if guard("send", lambda: send(format_alert(m, verdict))) is not None:
                res["alerts"] += 1
    state.set_value(PENDING_KEY, pend[-400:])
    state.set_value(SEEN_KEY, {k: v for k, v in seen.items() if now - v["ts"] < 3 * 86400})
    res["outcomes"] = guard("outcomes", lambda: update_outcomes(lambda ms: {k: r16._num(p.get("priceUsd")) for k, p in dex_batch(ms).items()
                                                                           if r16._num(p.get("priceUsd"))}, now, log_dir), 0) or 0
    return res

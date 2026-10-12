"""
Layer 16 -- Catalyst radar (Oct 12 2026).  GO_LIVE_CHECKLIST Phase 12.

Why: the research in MOONSHOT_RESEARCH.md found that the coins that gave
outsized returns in 2026 (PONS, STONK, AMC, MARS, USELESS) each had a PUBLIC,
dated catalyst that was visible before the crowd arrived:

  listing ladder   Binance Alpha -> futures -> spot (MARS)
  news-jack        a viral story, then a coin named after it within hours (AMC)
  launchpad surge  a new launchpad's fees explode (PONS, STONK)
  conviction money tracked smart wallets make a LARGE early buy (USELESS)
  buyback set      coins about to enter a protocol's buyback set (STONK)

This module watches those public precursors every few minutes and turns each
hit into (a) a Telegram alert and (b) a PAPER trade under its own signal name
(radar_ladder, radar_news, radar_launchpad, radar_conviction). It never places
a real order: real money needs the signal to earn its record in the paper
ledger first (executor.paper_ledger.signal_allowed) -- exactly the gate the
rest of the system uses.

Also here: the market-temperature gauge (regime), the narrative-leader /
clone filter, and cross-chain copy detection.

HONESTY: every threshold is a first-pass heuristic from articles, NOT measured
on our data. They are env-tunable. The paper record is the judge.
All network access goes through injectable fetchers, so everything is testable
offline, and every radar fails soft: one broken feed never stops the others.
"""
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional

import state

SEEN_KEY = "radar_seen"
LADDER_KEY = "radar_ladder"
REGIME_KEY = "regime_samples"
MAX_SEEN = 2500
MAX_LADDER = 400
MAX_REGIME_SAMPLES = 400
STAGE_RANK = {"alpha": 1, "futures": 2, "spot": 3}
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
DEFILLAMA_FEES = "https://api.llama.fi/overview/fees"
BINANCE_PAGE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
SUPPORTED_CHAINS = ("solana", "bsc", "base")

DEFAULT_NEWS_FEEDS = (
    "https://trends.google.com/trending/rss?geo=US",
    "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "https://hnrss.org/frontpage",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
)
TRENDS_HOSTS = ("trends.google.com",)


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ plumbing
def _http_json(url: str, params: Optional[dict] = None):
    from utils.http import get_json
    try:
        r = get_json(url, params=params, timeout=15)
    except Exception:  # noqa: BLE001  (network-level failure: radar degrades, never raises)
        return None
    return r.get("json") if r.get("ok") else None


def _http_text(url: str) -> Optional[str]:
    from utils.http import get
    try:
        r = get(url, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    return r.text if r.ok else None


def seen_ids() -> dict:
    v = state.get_value(SEEN_KEY)
    return v if isinstance(v, dict) else {}


def mark_seen(ids: Iterable[str], now: Optional[float] = None) -> None:
    ids = [i for i in ids if i]
    if not ids:
        return
    now = now if now is not None else time.time()
    seen = seen_ids()
    for i in ids:
        seen[i] = now
    if len(seen) > MAX_SEEN:
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-MAX_SEEN:])
    state.set_value(SEEN_KEY, seen)


# ------------------------------------------------------------ 12.1 listing ladder
_PAREN_TICKER = re.compile(r"\(([A-Za-z0-9]{2,15})\)")
_FUTURES_TICKER = re.compile(r"(?:USD\S{0,2}-M|COIN-M)\s+([A-Za-z0-9]{2,15})\s+Perpetual", re.I)


def classify_listing_title(title: str) -> Optional[dict]:
    """Binance announcement title -> {"stage": alpha|futures|spot, "tickers": [...]} or None.
    Ignores delistings, tag changes, earn/margin additions."""
    if not title:
        return None
    low = title.lower()
    if any(w in low for w in ("delist", "remove", "monitoring tag", "will add", "launchpool", "convert")):
        return None
    stage = None
    if "binance alpha" in low and any(w in low for w in ("feature", "list", "add", "first platform")):
        stage = "alpha"
    elif "futures will launch" in low or "perpetual contract" in low:
        stage = "futures"
    elif "binance will list" in low or "will list" in low:
        stage = "spot"
    if not stage:
        return None
    tickers = []
    for m in _FUTURES_TICKER.finditer(title):
        tickers.append(m.group(1).upper())
    for m in _PAREN_TICKER.finditer(title):
        tickers.append(m.group(1).upper())
    seen, uniq = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return {"stage": stage, "tickers": uniq} if uniq else None


def articles_from_binance(payload) -> List[dict]:
    """Tolerant parse of Binance's public CMS response (shape has varied)."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    arts = data.get("articles")
    if arts is None:
        cats = data.get("catalogs") or []
        arts = (cats[0] or {}).get("articles") if cats else []
    out = []
    for a in arts or []:
        if isinstance(a, dict) and a.get("title"):
            out.append({"title": a.get("title"), "code": a.get("code") or a.get("title"),
                        "release_ms": _num(a.get("releaseDate"))})
    return out


def ladder_update(articles: List[dict], ladder: dict, now: Optional[float] = None,
                  max_age_s: Optional[float] = None) -> List[dict]:
    """Walks announcements oldest-first, advances each ticker's ladder stage in `ladder`
    (mutated) and returns an event for every STEP UP that is still fresh. Old
    announcements update the state silently, so a first run never floods alerts."""
    now = now if now is not None else time.time()
    max_age_s = max_age_s if max_age_s is not None else _f("RADAR_LADDER_MAX_AGE_HOURS", 6) * 3600
    events = []
    for a in sorted(articles, key=lambda x: x.get("release_ms") or 0):
        c = classify_listing_title(a.get("title"))
        if not c:
            continue
        rel = (a.get("release_ms") or 0) / 1000.0
        fresh = rel == 0 or (now - rel) <= max_age_s
        for t in c["tickers"]:
            cur = ladder.get(t) or {}
            prev = cur.get("stage")
            if STAGE_RANK[c["stage"]] <= STAGE_RANK.get(prev, 0):
                continue
            ladder[t] = {"stage": c["stage"], "ts": rel or now, "prev": prev,
                         "stages": {**(cur.get("stages") or {}), c["stage"]: rel or now}}
            if fresh:
                events.append({"id": f"ladder:{a.get('code')}:{t}", "ticker": t, "stage": c["stage"],
                               "prev_stage": prev, "title": a.get("title")})
    if len(ladder) > MAX_LADDER:
        for k in sorted(ladder, key=lambda k: ladder[k].get("ts") or 0)[:len(ladder) - MAX_LADDER]:
            ladder.pop(k, None)
    return events


def resolve_ticker(ticker: str, fetch_json: Callable = _http_json) -> Optional[dict]:
    """ticker -> the one liquid on-chain token that carries it, or None.
    Refuses ambiguity: if a second token with the same symbol has >= half the
    liquidity of the best one, we cannot tell which is real (copycats) -> None."""
    data = fetch_json(DEXSCREENER_SEARCH, {"q": ticker})
    pairs = (data or {}).get("pairs") if isinstance(data, dict) else None
    if not pairs:
        return None
    min_liq = _f("RADAR_MIN_LIQ_USD", 100_000)
    tokens: Dict[tuple, dict] = {}
    for p in pairs:
        chain = p.get("chainId")
        base = p.get("baseToken") or {}
        if chain not in SUPPORTED_CHAINS or (base.get("symbol") or "").upper() != ticker.upper():
            continue
        addr = base.get("address")
        liq = _num((p.get("liquidity") or {}).get("usd")) or 0.0
        if not addr:
            continue
        k = (chain, addr)
        t = tokens.setdefault(k, {"chain": chain, "token": addr, "symbol": base.get("symbol"), "liquidity": 0.0,
                                  "mcap": None, "created_ms": None})
        t["liquidity"] += liq
        mc = _num(p.get("marketCap")) or _num(p.get("fdv"))
        if mc and (t["mcap"] is None or liq >= 0):
            t["mcap"] = max(t["mcap"] or 0, mc)
        t["created_ms"] = min(filter(None, [t["created_ms"], _num(p.get("pairCreatedAt"))]), default=t["created_ms"])
    ranked = sorted(tokens.values(), key=lambda t: -t["liquidity"])
    if not ranked or ranked[0]["liquidity"] < min_liq:
        return None
    if len(ranked) > 1 and ranked[1]["liquidity"] >= 0.5 * ranked[0]["liquidity"]:
        return None
    return ranked[0]


def ladder_candidates(events: List[dict], fetch_json: Callable = _http_json) -> List[dict]:
    """Alpha step: alert only (the +350% already happened on the announcement).
    Futures / spot step: alert + paper trade, if the ticker resolves unambiguously."""
    max_mcap = _f("RADAR_LADDER_MAX_MCAP_USD", 1_000_000_000)
    out = []
    for e in events:
        c = {"id": e["id"], "kind": "ladder", "symbol": e["ticker"], "chain": None, "token": None,
             "reason": f"Binance {e['stage']} step for {e['ticker']}"
                       + (f" (was {e['prev_stage']})" if e.get("prev_stage") else " (first sighting)"),
             "score": 40 + 20 * STAGE_RANK[e["stage"]], "mcap": None, "liquidity": None,
             "paper": False, "profile": "quick", "title": e.get("title")}
        if e["stage"] in ("futures", "spot"):
            r = resolve_ticker(e["ticker"], fetch_json)
            if r:
                c.update(chain=r["chain"], token=r["token"], mcap=r["mcap"], liquidity=r["liquidity"])
                c["paper"] = bool(r["mcap"]) and r["mcap"] <= max_mcap
            else:
                c["reason"] += " -- no single unambiguous on-chain token found, alert only"
        out.append(c)
    return out


# ------------------------------------------------------------ 12.2 news-jack
_STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been it its this that these those
how why what when where who which will would can could should may might has have had not no yes new says say said after
before over under about into up down out off more most less than then so very just now today tonight week year years day days
first last next one two three four five six seven eight nine ten monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december you your we our they their he she his her
us uk eu usa news live update updates report reports amid vs via per new best top big get gets got set sets make makes made
take takes took show shows watch read here there still back first second people man woman world city state states""".split())
_COIN_STOP = set("""coin token meme sol solana pump fun cat dog ai bitcoin ethereum crypto usd usdt usdc finance protocol swap inu
bnb eth btc base chain wallet dao defi nft""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’&\-]{1,}")
_CAPSEQ = re.compile(r"\b[A-Z][A-Za-z0-9&'’.\-]+(?:\s+[A-Z][A-Za-z0-9&'’.\-]+){0,2}")


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def parse_feed_titles(xml_text: Optional[str]) -> List[str]:
    """RSS <item><title> or Atom <entry><title>, namespace-agnostic. Bad XML -> []."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    titles = []
    for el in root.iter():
        if el.tag.split("}")[-1] in ("item", "entry"):
            for ch in el:
                if ch.tag.split("}")[-1] == "title" and (ch.text or "").strip():
                    titles.append(ch.text.strip())
                    break
    return titles


def extract_terms(title: str) -> set:
    """Candidate 'entities' in a headline: capitalised phrases (<= 3 words) and their
    individual words, lower-cased, stop-words removed."""
    terms = set()
    title = re.sub(r"\s[-–—|]\s[^-–—|]{2,40}$", "", title or "")  # drop Google-News " - Source"
    for m in _CAPSEQ.finditer(title):
        words = [_norm(w) for w in m.group(0).split()]
        words = [w for w in words if w]
        if not words:
            continue
        if len(words) > 1:
            terms.add(" ".join(words))
        for w in words:
            if len(w) >= 3 and w not in _STOP and w not in _COIN_STOP:
                terms.add(w)
    return terms


def hot_terms(feeds: Dict[str, List[str]], min_feeds: Optional[int] = None) -> Dict[str, dict]:
    """feeds: {feed_url: [titles]}. A term is HOT when >= `min_feeds` distinct feeds mention it,
    or it appears in a Google-Trends feed (each trend title is already a viral term)."""
    min_feeds = int(min_feeds if min_feeds is not None else _f("RADAR_NEWS_MIN_FEEDS", 2))
    seen: Dict[str, dict] = {}
    for url, titles in feeds.items():
        is_trend = any(h in url for h in TRENDS_HOSTS)
        per_feed = set()
        for t in titles:
            for term in extract_terms(t):
                per_feed.add(term)
            if is_trend:
                for w in _WORD.findall(t):
                    n = _norm(w)
                    if len(n) >= 3 and n not in _STOP and n not in _COIN_STOP:
                        per_feed.add(n)
                full = " ".join(_norm(w) for w in t.split() if _norm(w))
                if full and len(full.split()) <= 3:
                    per_feed.add(full)
        for term in per_feed:
            d = seen.setdefault(term, {"feeds": set(), "trend": False})
            d["feeds"].add(url)
            d["trend"] = d["trend"] or is_trend
    out = {}
    for term, d in seen.items():
        n = len(d["feeds"])
        if d["trend"] or n >= min_feeds:
            out[term] = {"score": (3 if d["trend"] else 0) + n, "feeds": n, "trend": d["trend"]}
    return out


def coin_symbol_name(item: dict) -> tuple:
    """GeckoTerminal flattened item -> (symbol, full name) from 'SYMBOL / SOL'."""
    nm = (item.get("name") or "").strip()
    sym = nm.split("/")[0].strip() if "/" in nm else nm
    return sym, nm


def coin_age_minutes(item: dict, now: Optional[float] = None) -> Optional[float]:
    import datetime
    now = now if now is not None else time.time()
    s = item.get("pool_created_at")
    if not s:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return max(0.0, (now - ts) / 60.0)


def match_new_coins(hot: Dict[str, dict], coins: List[dict], now: Optional[float] = None) -> List[dict]:
    """coins: flattened GeckoTerminal items with a 'chain' key. Returns matches
    (best hot term per coin) for coins newer than RADAR_NEWS_MAX_AGE_HOURS with real liquidity."""
    max_age = _f("RADAR_NEWS_MAX_AGE_HOURS", 12) * 60
    min_liq = _f("RADAR_NEWS_MIN_LIQ_USD", 5_000)
    out = []
    for c in coins:
        age = coin_age_minutes(c, now)
        if age is None or age > max_age or (c.get("liquidity_usd") or 0) < min_liq:
            continue
        sym, nm = coin_symbol_name(c)
        words = {_norm(w) for w in nm.replace("/", " ").split() if _norm(w)}
        sym_n = _norm(sym)
        words.add(sym_n)
        joined = _norm(nm.split("/")[0])
        best = None
        for term, info in hot.items():
            parts = term.split()
            ok = False
            if len(parts) == 1:
                ok = len(term) >= 3 and (term in words or term == joined)
            else:
                ok = all(p in words for p in parts) or "".join(parts) in (sym_n, joined)
            if ok and (best is None or info["score"] > best[1]["score"]):
                best = (term, info)
        if best:
            out.append({"coin": c, "term": best[0], "score": best[1]["score"], "trend": best[1]["trend"],
                        "age_min": age})
    return out


# ------------------------------------------- 12.4 narrative leader / clone filter
def choose_leaders(matches: List[dict]) -> List[dict]:
    """Per matched term only the deepest-liquidity coin is the leader; the rest are clones
    (flagged, not traded). Leaders get the cluster size attached."""
    groups: Dict[str, List[dict]] = {}
    for m in matches:
        groups.setdefault(m["term"], []).append(m)
    out = []
    for term, ms in groups.items():
        ms = sorted(ms, key=lambda m: -(m["coin"].get("liquidity_usd") or 0))
        for i, m in enumerate(ms):
            m = dict(m)
            m["leader"] = i == 0
            m["cluster_size"] = len(ms)
            out.append(m)
    return out


def cross_chain_copies(coins: List[dict], max_gap_h: float = 24.0, now: Optional[float] = None) -> Dict[str, List[dict]]:
    """12.11: same symbol on >= 2 chains created within `max_gap_h` of each other = a story
    jumping chains. {symbol: [{chain, age_min}, ...]} sorted oldest first."""
    by: Dict[str, List[dict]] = {}
    for c in coins:
        sym, _ = coin_symbol_name(c)
        age = coin_age_minutes(c, now)
        if sym and age is not None and c.get("chain"):
            by.setdefault(sym.upper(), []).append({"chain": c["chain"], "age_min": age})
    out = {}
    for sym, lst in by.items():
        chains = {x["chain"] for x in lst}
        if len(chains) >= 2:
            ages = sorted(x["age_min"] for x in lst)
            if ages[-1] - ages[0] <= max_gap_h * 60:
                out[sym] = sorted(lst, key=lambda x: -x["age_min"])
    return out


def news_candidates(matches: List[dict]) -> List[dict]:
    out = []
    for m in choose_leaders(matches):
        c = m["coin"]
        sym, nm = coin_symbol_name(c)
        reason = (f"news-jack: '{m['term']}' is trending ({m['score']} pts), coin {m['age_min']:.0f} min old"
                  + ("" if m["leader"] else f" -- CLONE (1 of {m['cluster_size']}, not the deepest pool)"))
        out.append({"id": f"news:{c.get('chain')}:{c.get('address')}", "kind": "news", "symbol": sym,
                    "chain": c.get("chain"), "token": c.get("address"), "reason": reason,
                    "score": m["score"] + (10 if m["leader"] else -20), "mcap": c.get("market_cap_usd") or c.get("fdv_usd"),
                    "liquidity": c.get("liquidity_usd"), "paper": bool(m["leader"]), "profile": "moonshot"})
    return out


# ------------------------------------------------------------ 12.3 launchpad radar
_LAUNCHPAD_NAME = re.compile(r"(pump|launch|\.fun|fun\b|bonk|meme|four\.meme|flap)", re.I)


def launchpad_surges(protocols: List[dict]) -> List[dict]:
    """DefiLlama fee overview rows -> launchpad-like protocols whose fees are exploding."""
    min_fees = _f("RADAR_LAUNCHPAD_MIN_FEES_USD", 25_000)
    min_d1 = _f("RADAR_LAUNCHPAD_MIN_D1_PCT", 100)
    min_d7 = _f("RADAR_LAUNCHPAD_MIN_D7_PCT", 150)
    out = []
    for p in protocols or []:
        name = p.get("displayName") or p.get("name") or ""
        cat = (p.get("category") or "")
        if not (re.search(r"launchpad", cat, re.I) or _LAUNCHPAD_NAME.search(name)):
            continue
        fees = _num(p.get("total24h")) or 0
        d1, d7 = _num(p.get("change_1d")), _num(p.get("change_7d"))
        if fees < min_fees:
            continue
        if (d1 is not None and d1 >= min_d1) or (d7 is not None and d7 >= min_d7):
            out.append({"name": name, "fees_24h": fees, "change_1d": d1, "change_7d": d7,
                        "symbol": (p.get("symbol") or "").upper() if p.get("symbol") not in (None, "-") else None,
                        "chains": p.get("chains")})
    return sorted(out, key=lambda r: -r["fees_24h"])


def launchpad_candidates(surges: List[dict], fetch_json: Callable = _http_json) -> List[dict]:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    max_mcap = _f("RADAR_LAUNCHPAD_MAX_MCAP_USD", 300_000_000)
    out = []
    for s in surges:
        c = {"id": f"launchpad:{s['name']}:{day}", "kind": "launchpad", "symbol": s.get("symbol") or s["name"],
             "chain": None, "token": None, "mcap": None, "liquidity": None, "paper": False, "profile": "moonshot",
             "reason": (f"launchpad surge: {s['name']} fees ${s['fees_24h']:,.0f}/24h "
                        f"(1d {s['change_1d']:+.0f}%, 7d {s['change_7d']:+.0f}%)" if s.get("change_1d") is not None and s.get("change_7d") is not None
                        else f"launchpad surge: {s['name']} fees ${s['fees_24h']:,.0f}/24h"),
             "score": 60}
        if s.get("symbol"):
            r = resolve_ticker(s["symbol"], fetch_json)
            if r:
                c.update(chain=r["chain"], token=r["token"], mcap=r["mcap"], liquidity=r["liquidity"])
                c["paper"] = bool(r["mcap"]) and r["mcap"] <= max_mcap
        out.append(c)
    return out


# ------------------------------------------------------------ 12.9 buyback-set proximity
def buyback_proximity(coins: List[dict], k: int = 15, near_below: int = 3, near_above: int = 10) -> List[dict]:
    """coins: [{symbol, address, chain, mcap_usd, volume_h1, volume_h6?}] for one buyback ecosystem.
    Ranks by mcap; coins ranked (k - near_below) .. (k + near_above) whose hourly volume is above their
    hourly average (volume_h24 / 24 x 1.5) are about to enter / have just entered the set."""
    ranked = sorted([c for c in coins if (c.get("mcap_usd") or 0) > 0], key=lambda c: -c["mcap_usd"])
    out = []
    for i, c in enumerate(ranked, start=1):
        if not (k - near_below <= i <= k + near_above):
            continue
        h1, h24 = c.get("volume_h1") or 0, c.get("volume_h24") or 0
        if h24 > 0 and h1 >= 1.5 * (h24 / 24.0):
            out.append({**c, "rank": i, "set_size": k})
    return out


# ------------------------------------------------------------ 12.8 regime gauge
def regime_sample(coins: List[dict], now: Optional[float] = None) -> dict:
    """One market-temperature reading from freshly created pools (all chains passed in)."""
    now = now if now is not None else time.time()
    liquid = [c for c in coins if (c.get("liquidity_usd") or 0) >= 5_000]
    h1 = [c.get("change_h1") for c in liquid if c.get("change_h1") is not None]
    green = (sum(1 for v in h1 if v > 0) / len(h1)) if h1 else None
    return {"ts": now, "n_liquid": len(liquid), "green": green}


def _pctile(sorted_vals: List[float], v: float) -> float:
    if not sorted_vals:
        return 0.5
    below = sum(1 for x in sorted_vals if x < v) + 0.5 * sum(1 for x in sorted_vals if x == v)
    return below / len(sorted_vals)


def regime_state(samples: List[dict], min_samples: int = 24) -> dict:
    """hot / neutral / cold relative to our OWN recent history (no outside baseline).
    Needs >= min_samples readings; until then 'unknown' (which never changes sizing)."""
    good = [s for s in samples if s.get("green") is not None]
    if len(good) < min_samples:
        return {"state": "unknown", "n": len(good), "score": None}
    cur = good[-1]
    hist_n = sorted(s["n_liquid"] for s in good)
    hist_g = sorted(s["green"] for s in good)
    score = 0.5 * _pctile(hist_n, cur["n_liquid"]) + 0.5 * _pctile(hist_g, cur["green"])
    st = "hot" if score >= 0.7 else ("cold" if score <= 0.3 else "neutral")
    return {"state": st, "n": len(good), "score": round(score, 2)}


def record_regime(sample: dict, min_gap_s: float = 240) -> None:
    samples = state.get_value(REGIME_KEY)
    samples = samples if isinstance(samples, list) else []
    if samples and sample["ts"] - samples[-1].get("ts", 0) < min_gap_s:
        return
    samples.append(sample)
    state.set_value(REGIME_KEY, samples[-MAX_REGIME_SAMPLES:])


def current_regime() -> dict:
    s = state.get_value(REGIME_KEY)
    return regime_state(s if isinstance(s, list) else [])


def regime_stake_multiplier() -> float:
    """1.0 unless REGIME_GATE_ENABLED=true AND the market reads 'cold' (then REGIME_COLD_STAKE_MULT, default 0.5)."""
    if os.environ.get("REGIME_GATE_ENABLED", "").strip().lower() != "true":
        return 1.0
    try:
        if current_regime()["state"] == "cold":
            return _f("REGIME_COLD_STAKE_MULT", 0.5)
    except Exception:  # noqa: BLE001
        pass
    return 1.0


# ------------------------------------------------------------ 12.10 conviction money
def conviction(buys: List[dict], roster: Iterable[str], min_sol: Optional[float] = None) -> dict:
    """Tracked (roster) wallets making LARGE buys: >= min_sol SOL in one coin (default 5)."""
    min_sol = min_sol if min_sol is not None else _f("RADAR_CONVICTION_MIN_SOL", 5.0)
    roster = set(roster or [])
    per: Dict[str, float] = {}
    for b in buys:
        w = b.get("wallet")
        sol = abs(_num(b.get("sol_delta")) or 0.0)
        if w in roster:
            per[w] = per.get(w, 0.0) + sol
    big = {w: s for w, s in per.items() if s >= min_sol}
    return {"fires": bool(big), "wallets": sorted(big), "total_sol": round(sum(big.values()), 2),
            "roster_buyers": len(per)}


def conviction_scan(archive: dict, roster: Iterable[str]) -> List[dict]:
    """archive: state['buyer_archive'] ({mint: {first_ts, buys:[{w,t,sol}]}})."""
    out = []
    for mint, rec in (archive or {}).items():
        buys = [{"wallet": b.get("w"), "sol_delta": b.get("sol"), "block_time": b.get("t")} for b in rec.get("buys", [])]
        r = conviction(buys, roster)
        if r["fires"]:
            out.append({"mint": mint, **r})
    return out


def conviction_candidates(fires: List[dict], fetch_batch: Callable[[List[str]], Dict[str, dict]]) -> List[dict]:
    """Prices the firing mints (one DexScreener batch) and keeps sub-$5M coins with real liquidity."""
    if not fires:
        return []
    max_mcap = _f("RADAR_CONVICTION_MAX_MCAP_USD", 5_000_000)
    min_liq = _f("RADAR_CONVICTION_MIN_LIQ_USD", 10_000)
    pairs = fetch_batch([f["mint"] for f in fires]) or {}
    out = []
    for f in fires:
        p = pairs.get(f["mint"])
        if not p:
            continue
        mc = _num(p.get("marketCap")) or _num(p.get("fdv"))
        liq = _num((p.get("liquidity") or {}).get("usd")) or 0
        if not mc or mc > max_mcap or liq < min_liq:
            continue
        sym = (p.get("baseToken") or {}).get("symbol") or f["mint"][:6]
        out.append({"id": f"conviction:{f['mint']}", "kind": "conviction", "symbol": sym, "chain": "solana",
                    "token": f["mint"], "mcap": mc, "liquidity": liq, "paper": True, "profile": "moonshot",
                    "score": 50 + min(30, int(f["total_sol"])),
                    "reason": f"conviction money: {len(f['wallets'])} tracked wallet(s) put {f['total_sol']} SOL into it"})
    return out


# ------------------------------------------------------------ output
def _clean(s) -> str:
    return re.sub(r"[`*_\[\]]", " ", str(s or ""))[:140]


def format_alert(c: dict, regime: Optional[dict] = None) -> str:
    from links import render_links_line
    head = {"ladder": "LISTING LADDER", "news": "NEWS-JACK", "launchpad": "LAUNCHPAD SURGE",
            "conviction": "CONVICTION MONEY", "buyback": "BUYBACK SET"}.get(c["kind"], c["kind"].upper())
    lines = [f"[📡 RADAR] 📡 CATALYST RADAR - {head}", f"{_clean(c.get('symbol'))}  {('`' + c['token'] + '`') if c.get('token') else ''}".strip(),
             _clean(c.get("reason"))]
    tags = [f"[Chain: {c['chain']}]"] if c.get("chain") else []
    if c.get("mcap"):
        tags.append(f"[MCap: ${c['mcap']:,.0f}]")
    if c.get("liquidity"):
        tags.append(f"[Liq: ${c['liquidity']:,.0f}]")
    if regime and regime.get("state") not in (None, "unknown"):
        tags.append(f"[Market: {regime['state']}]")
    tags.append("[PAPER trade opened]" if c.get("paper") else "[alert only]")
    lines.append(" ".join(tags))
    if c.get("chain") and c.get("token"):
        try:
            links = render_links_line(c["chain"], c["token"])
            if links:
                lines.append(links)
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(lines)


def open_paper_for(c: dict) -> Optional[dict]:
    """Opens the paper position for a candidate (never a real order)."""
    if not (c.get("paper") and c.get("chain") and c.get("token") and c.get("mcap")):
        return None
    from executor import paper_ledger
    usd = _f("RADAR_PAPER_USD", 30.0)
    signal = f"radar_{c['kind']}"
    quick = c.get("profile") == "quick"
    return paper_ledger.open_paper(c["chain"], c["token"], signal, signal, usd, c["mcap"],
                                   liquidity_usd=c.get("liquidity"), tags={"radar": c["kind"], "score": c.get("score")},
                                   strategy="quick" if quick else "stage", exit_profile=c.get("profile"))


# ------------------------------------------------------------ orchestration
def _dex_batch(addresses: List[str]) -> Dict[str, dict]:
    from layers.layer14_revival import fetch_dexscreener_batch
    return fetch_dexscreener_batch("solana", addresses)


def _gt_coins(chain: str) -> List[dict]:
    from layers.layer0_scoring import fetch_geckoterminal_new_pools, flatten_geckoterminal_pools
    try:
        r = fetch_geckoterminal_new_pools(chain)
    except Exception:  # noqa: BLE001
        return []
    gt = r.get("json") if isinstance(r, dict) and "json" in r else r
    items = flatten_geckoterminal_pools(gt)
    for it in items:
        it["chain"] = chain
    return items


def run_cycle(send: Optional[Callable[[str], dict]] = None, *, fetch_json: Callable = _http_json,
              fetch_text: Callable = _http_text, coins_fn: Callable[[str], List[dict]] = _gt_coins,
              dex_batch: Callable = _dex_batch, now: Optional[float] = None) -> dict:
    """One radar pass. Every section is wrapped: a failure is recorded in 'errors', never raised.
    Returns {"candidates": n, "alerts": n, "paper": n, "regime": {...}, "errors": [...], "notes": [...]}."""
    now = now if now is not None else time.time()
    if send is None:
        from telegram_alert import send_telegram_message as send
    errors, notes, cands = [], [], []

    def guard(name, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return None

    # --- fetch everything independent in parallel (speed) ---
    feeds_urls = [u for u in (os.environ.get("RADAR_NEWS_FEEDS", "").split(",") if os.environ.get("RADAR_NEWS_FEEDS") else DEFAULT_NEWS_FEEDS) if u.strip()]
    chains = [c.strip() for c in os.environ.get("RADAR_CHAINS", ",".join(SUPPORTED_CHAINS)).split(",") if c.strip()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        try:
            from config import CONFIG
            bin_url = CONFIG.binance_announcements_url or BINANCE_PAGE
        except Exception:  # noqa: BLE001
            bin_url = BINANCE_PAGE
        f_bin = ex.submit(lambda: fetch_json(bin_url, {"type": 1, "pageNo": 1, "pageSize": 30, "catalogId": 48}))
        f_fees = ex.submit(lambda: fetch_json(DEFILLAMA_FEES, {"excludeTotalDataChart": "true",
                                                                "excludeTotalDataChartBreakdown": "true"}))
        f_feeds = {u: ex.submit(lambda u=u: parse_feed_titles(fetch_text(u))) for u in feeds_urls}
        f_coins = {c: ex.submit(lambda c=c: coins_fn(c)) for c in chains}
        bin_payload = guard("binance", f_bin.result)
        fees_payload = guard("defillama", f_fees.result)
        feeds = {u: (guard(f"feed {u[:40]}", f.result) or []) for u, f in f_feeds.items()}
        coins = []
        for c, f in f_coins.items():
            coins.extend(guard(f"coins {c}", f.result) or [])

    # --- regime (12.8) ---
    regime = {"state": "unknown"}
    def _regime():
        nonlocal regime
        if coins:
            record_regime(regime_sample(coins, now))
        regime = current_regime()
    guard("regime", _regime)

    # --- listing ladder (12.1) ---
    def _ladder():
        arts = articles_from_binance(bin_payload)
        if not arts:
            notes.append("ladder: no Binance articles (feed down or empty)")
            return
        ladder = state.get_value(LADDER_KEY)
        ladder = ladder if isinstance(ladder, dict) else {}
        events = ladder_update(arts, ladder, now)
        state.set_value(LADDER_KEY, ladder)
        cands.extend(ladder_candidates(events, fetch_json))
    guard("ladder", _ladder)

    # --- news-jack + narrative leader + cross-chain (12.2 / 12.4 / 12.11) ---
    def _news():
        if not any(feeds.values()):
            notes.append("news: no feed returned titles")
            return
        hot = hot_terms(feeds)
        matches = match_new_coins(hot, coins, now)
        copies = cross_chain_copies(coins, now=now)
        for cd in news_candidates(matches):
            sym = (cd["symbol"] or "").upper()
            if sym in copies:
                others = [x["chain"] for x in copies[sym] if x["chain"] != cd["chain"]]
                if others:
                    cd["reason"] += f"; same ticker also on {', '.join(sorted(set(others)))}"
            cands.append(cd)
        notes.append(f"news: {len(hot)} hot terms, {len(matches)} matching new coins")
    guard("news", _news)

    # --- launchpad / chain radar (12.3) ---
    def _launchpad():
        rows = (fees_payload or {}).get("protocols") if isinstance(fees_payload, dict) else None
        if not rows:
            notes.append("launchpad: no DefiLlama fee data")
            return
        cands.extend(launchpad_candidates(launchpad_surges(rows), fetch_json))
    guard("launchpad", _launchpad)

    # --- conviction money (12.10) ---
    def _conviction():
        arch = state.get_value("buyer_archive") or {}
        if not arch:
            return
        from layers.layer2b_pumpfun_smart_money import get_smart_money_roster
        fires = conviction_scan(arch, get_smart_money_roster())
        fires = [f for f in fires if f"conviction:{f['mint']}" not in seen_ids()]
        cands.extend(conviction_candidates(fires[:30], dex_batch))
    guard("conviction", _conviction)

    # --- buyback sets (12.9): user-supplied file, off unless present ---
    def _buyback():
        import json
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "buyback_sets.json")
        if not os.path.exists(p):
            return
        with open(p, encoding="utf-8") as fh:
            sets = json.load(fh)
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        for s in sets if isinstance(sets, list) else []:
            addrs = s.get("token_addresses") or []
            if s.get("chain") != "solana" or not addrs:
                continue
            pairs = dex_batch(addrs)
            rows = []
            for a, pr in pairs.items():
                vol = pr.get("volume") or {}
                rows.append({"symbol": (pr.get("baseToken") or {}).get("symbol"), "address": a, "chain": "solana",
                             "mcap_usd": _num(pr.get("marketCap")) or _num(pr.get("fdv")),
                             "volume_h1": _num(vol.get("h1")), "volume_h24": _num(vol.get("h24")),
                             "liquidity_usd": _num((pr.get("liquidity") or {}).get("usd"))})
            for r in buyback_proximity(rows, int(s.get("k", 15))):
                cands.append({"id": f"buyback:{r['address']}:{day}", "kind": "buyback", "symbol": r["symbol"], "chain": "solana",
                              "token": r["address"], "mcap": r["mcap_usd"], "liquidity": r["liquidity_usd"], "paper": False,
                              "profile": "moonshot", "score": 45,
                              "reason": f"buyback set '{s.get('name', '?')}': rank {r['rank']} of top {r['set_size']}, volume running hot"})
    guard("buyback", _buyback)

    # --- dedupe, cap, alert, paper trade ---
    seen = seen_ids()
    fresh = [c for c in cands if c["id"] not in seen]
    fresh.sort(key=lambda c: -c.get("score", 0))
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    dstate = state.get_value("radar_day")
    if not isinstance(dstate, dict) or dstate.get("day") != day:
        dstate = {"day": day, "n": 0}
    cap = int(min(_f("RADAR_MAX_ALERTS_PER_CYCLE", 6), max(0, _f("RADAR_MAX_ALERTS_PER_DAY", 40) - dstate["n"])))
    if fresh and cap == 0:
        notes.append("daily alert cap reached -- candidates still recorded as paper-only next day")
    sent = paper_n = 0
    done_ids = []
    for c in fresh:
        if sent >= cap:
            break
        pos = guard("paper", lambda c=c: open_paper_for(c))
        if pos:
            paper_n += 1
        else:
            c["paper"] = False
        guard("alert", lambda c=c: send(format_alert(c, regime)))
        guard("feed", lambda c=c: state.log_full_alert(
            "radar_" + str(c.get("kind") or "x"), c.get("chain") or "n/a", _clean(c.get("symbol")) or "?",
            c.get("token") or "n/a", _clean(c.get("reason"))[:200],
            {"Chain": c.get("chain") or "", "Radar": str(c.get("kind")),
             "MCap": f"${c['mcap']:,.0f}" if c.get("mcap") else "",
             "Paper": "opened" if c.get("paper") else "alert only"}))
        sent += 1
        done_ids.append(c["id"])
    mark_seen(done_ids, now)
    if sent:
        dstate["n"] += sent
        state.set_value("radar_day", dstate)
    return {"candidates": len(cands), "alerts": sent, "paper": paper_n, "regime": regime,
            "errors": errors, "notes": notes}

"""
Layer 4 -- News/exchange layer.

Two independent sub-sources, tagged separately in the alert output:

  1. CryptoPanic (news + Reddit-tagged posts, filter=rising/hot). Needs its
     own free CRYPTOPANIC_AUTH_TOKEN -- note this account was NOT in Ali's
     Part A list, so it's flagged as an extra step in the README.

  2. Free public exchange listing-announcement feeds (Binance, Coinbase),
     tagged as an explicit [News: exchange-listing] sub-tag. No key needed
     for either. Robinhood has no confirmed public listing feed as of this
     build (see README) -- left as a documented gap, not faked.
"""
import re
import xml.etree.ElementTree as ET

from config import CONFIG
from utils.http import get, get_json


def fetch_cryptopanic_posts(filter_: str = "rising") -> dict:
    """CryptoPanic discontinued their free "Developer" API plan (confirmed
    Sept 28 2026, live -- a real call now 404s with "Unknown API endpoint.
    Valid paths are /api/{growth|growth_weekly|enterprise}/v2/{posts|
    portfolio|status}/"). CONFIG.cryptopanic_base_url still points at the
    old /api/developer/v2 path, which no longer exists at any price --
    "developer" isn't even in CryptoPanic's own list of valid tiers anymore.
    Failing fast here (rather than letting every cycle retry a dead
    endpoint 3x via utils.http's retry decorator) until/unless Ali upgrades
    to a paid growth/growth_weekly/enterprise CryptoPanic plan and
    cryptopanic_base_url is updated to match."""
    if not CONFIG.cryptopanic_auth_token:
        return {"ok": False, "reason": "CRYPTOPANIC_AUTH_TOKEN not configured (see README -- separate free signup)"}
    return {"ok": False, "reason": (
        "CryptoPanic's free Developer API plan was discontinued -- this "
        "auth token can't reach any current endpoint (growth/growth_weekly/"
        "enterprise are all paid). Needs a paid CryptoPanic plan to work "
        "again; see fetch_cryptopanic_posts's docstring."
    )}


# CoinDesk RSS -- Layer 4's replacement news source (Sept 28 2026, see
# fetch_cryptopanic_posts's docstring for why CryptoPanic died). Free,
# keyless, no signup, established uptime.
#
# CoinDesk's RSS has no structured "currencies" tag the way CryptoPanic did
# -- this is a real, deliberate downgrade in signal quality, not a hidden
# equivalence. COINDESK_TICKER_MAP is a best-effort keyword match (ticker
# symbols matched as whole uppercase words only, full names matched
# case-insensitively) against a curated list of ~70 liquid/major coins --
# it will miss obscure names and can occasionally mistag (e.g. a coin whose
# ticker collides with an unrelated all-caps acronym in a headline). Treated
# as an advisory [News: ...] tag on an alert, same as CryptoPanic was --
# never fed into Stage1/Stage2 buy/sell decisions (see has_news_catalyst's
# call sites in executor/entrypoint.py -- nothing wires Layer 4 into it
# today, so this heuristic carries no direct execution risk).
COINDESK_TICKER_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP",
    "dogecoin": "DOGE", "shiba inu": "SHIB", "binance coin": "BNB",
    "cardano": "ADA", "avalanche": "AVAX", "polygon": "MATIC",
    "chainlink": "LINK", "polkadot": "DOT", "litecoin": "LTC", "tron": "TRX",
    "toncoin": "TON", "pepe": "PEPE", "dogwifhat": "WIF", "bonk": "BONK",
    "arbitrum": "ARB", "optimism": "OP", "sui": "SUI", "aptos": "APT",
    "near protocol": "NEAR", "cosmos": "ATOM", "uniswap": "UNI",
    "aave": "AAVE", "injective": "INJ", "sei": "SEI", "celestia": "TIA",
    "jupiter": "JUP", "render": "RNDR", "algorand": "ALGO",
    "internet computer": "ICP", "filecoin": "FIL", "hedera": "HBAR",
    "vechain": "VET", "stellar": "XLM", "monero": "XMR",
    "ethereum classic": "ETC", "bitcoin cash": "BCH", "cronos": "CRO",
    "kaspa": "KAS", "pyth network": "PYTH", "jito": "JTO", "ondo": "ONDO",
    "ethena": "ENA", "wormhole": "W", "starknet": "STRK", "dydx": "DYDX",
    "lido dao": "LDO", "maker": "MKR", "synthetix": "SNX", "curve": "CRV",
    "compound": "COMP", "yearn finance": "YFI", "sushiswap": "SUSHI",
    "the graph": "GRT", "the sandbox": "SAND", "decentraland": "MANA",
    "axie infinity": "AXS", "gala": "GALA", "immutable": "IMX",
    "flow": "FLOW", "multiversx": "EGLD", "theta network": "THETA",
    "tezos": "XTZ", "eos": "EOS", "neo": "NEO", "zcash": "ZEC",
    "dash": "DASH", "quant": "QNT", "kava": "KAVA", "celo": "CELO",
    "fetch.ai": "FET", "thorchain": "RUNE", "osmosis": "OSMO",
    "stacks": "STX", "iota": "IOTA",
}
COINDESK_KNOWN_TICKERS = set(COINDESK_TICKER_MAP.values())
_TICKER_WORD_RE = re.compile(r"\b[A-Z]{2,6}\b")


_COINDESK_NAME_PATTERNS = {
    name: re.compile(r"\b" + re.escape(name) + r"\b")
    for name in COINDESK_TICKER_MAP
}


def _extract_coindesk_currencies(title: str, summary: str = "") -> list:
    """Best-effort currency extraction -- see fetch_coindesk_rss's docstring
    for the honest limits of this heuristic.

    Bug found and fixed Sept 28 2026 (caught live, before this ever ran
    against a real feed): the original version used plain `name in lower`
    substring matching, which false-positived constantly -- "ETF Inflows"
    matched "flow" -> tagged FLOW, "Lawmakers" matched "maker" -> tagged
    MKR. Both full names and ticker symbols are now matched as whole words
    only (\b...\b), which is what COINDESK_TICKER_MAP's own crypto-ticker
    ambiguity (e.g. "ONE", "SC") already assumed but the code didn't
    actually enforce for the full-name side."""
    text = f"{title or ''} {summary or ''}"
    lower = text.lower()
    found = set()
    for name, ticker in COINDESK_TICKER_MAP.items():
        if _COINDESK_NAME_PATTERNS[name].search(lower):
            found.add(ticker)
    for token in _TICKER_WORD_RE.findall(text):
        if token in COINDESK_KNOWN_TICKERS:
            found.add(token)
    return sorted(found)


def fetch_coindesk_rss() -> dict:
    """Uses utils.http.get() directly, NOT get_json(). Real bug found and
    fixed live Sept 28 2026: get_json()'s non-JSON fallback truncates the
    response body to result["text"] = resp.text[:2000] (fine for a short
    error message, which is what that fallback was built for) -- but
    CoinDesk's real RSS feed is much longer than 2000 chars, so every fetch
    silently returned a truncated, syntactically-broken XML document.
    ET.fromstring correctly refused to parse it and parse_coindesk_rss
    returned an empty list every time -- "0 posts fetched" with ok=True and
    a real 200, looking like a working call that just found nothing, when
    the real cause was truncation. get() returns the raw requests.Response
    with the FULL .text, no truncation."""
    resp = get(CONFIG.coindesk_rss_url)
    return {"ok": resp.ok, "raw": {"status_code": resp.status_code, "text": resp.text}}


def parse_coindesk_rss(xml_text: str) -> list:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        guid = (item.findtext("guid") or link or title).strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        out.append({
            "id": guid,
            "title": title,
            "url": link,
            "currencies": _extract_coindesk_currencies(title, description),
            "published_at": pub_date,
        })
    return out


def parse_cryptopanic_posts(payload: dict) -> list:
    posts = payload.get("results", []) if payload else []
    out = []
    for p in posts:
        out.append({
            "id": p.get("id"),
            "title": p.get("title"),
            "url": p.get("url") or (p.get("source") or {}).get("domain"),
            "currencies": [c.get("code") for c in p.get("currencies", []) or []],
            "published_at": p.get("published_at"),
        })
    return out


# Binance's public CMS article-list endpoint. catalogId=48 is the
# "New Cryptocurrency Listing" catalog on binance.com/en/support/announcement --
# this is Binance's own public site backend, no auth required, but it is an
# unofficial/reverse-engineered integration point (not a documented public API)
# so it should be treated as best-effort and monitored for breakage.
def fetch_binance_new_listings(page_size: int = 10) -> dict:
    params = {"type": 1, "pageNo": 1, "pageSize": page_size, "catalogId": 48}
    result = get_json(CONFIG.binance_announcements_url, params=params)
    return {"ok": result["ok"], "raw": result}


def parse_binance_new_listings(payload: dict) -> list:
    articles = ((payload or {}).get("data") or {}).get("catalogs", [{}])[0].get("articles", []) \
        if payload and "data" in payload else (payload or {}).get("data", {}).get("articles", []) if payload else []
    out = []
    for a in articles or []:
        out.append({
            "title": a.get("title"),
            "code": a.get("code"),
            "release_date": a.get("releaseDate"),
        })
    return out


# Coinbase has no public "new listing announcement" feed -- the closest
# no-key public signal is diffing the products list over time (a symbol
# appearing that wasn't there on the last poll). This needs state (a
# previous-snapshot file/DB row), wired in the scheduler, not here.
def fetch_coinbase_products() -> dict:
    result = get_json(CONFIG.coinbase_products_url)
    return {"ok": result["ok"], "raw": result}


def diff_coinbase_new_symbols(previous_ids: set, current_products: list) -> list:
    current_ids = {p.get("id") for p in current_products}
    new_ids = current_ids - previous_ids
    return sorted(i for i in new_ids if i)

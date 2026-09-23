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
from config import CONFIG
from utils.http import get_json


def fetch_cryptopanic_posts(filter_: str = "rising") -> dict:
    if not CONFIG.cryptopanic_auth_token:
        return {"ok": False, "reason": "CRYPTOPANIC_AUTH_TOKEN not configured (see README -- separate free signup)"}
    params = {"auth_token": CONFIG.cryptopanic_auth_token, "filter": filter_, "kind": "news", "public": "true"}
    result = get_json(f"{CONFIG.cryptopanic_base_url}/posts/", params=params)
    return {"ok": result["ok"], "raw": result}


def parse_cryptopanic_posts(payload: dict) -> list:
    posts = payload.get("results", []) if payload else []
    out = []
    for p in posts:
        out.append({
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

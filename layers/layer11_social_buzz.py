"""
Layer 11 -- Social/community buzz proxy (DexScreener boosts).

HONEST FRAMING -- READ BEFORE ASSUMING THIS IS "TWITTER INTEGRATION".
Ali asked (Sept 23 2026) for a free tool to pick up Twitter/X buzz on
upcoming meme launches. Real research done live that night found there is
NO free, ongoing, reliable way to pull Twitter/X search data in 2026:
  - X's own API: the free tier is write-only (post/delete a tweet only).
    Read/search access requires a paid Basic tier ($100+/month) --
    confirmed against X's current developer pricing page.
  - Nitter (the open-source scraping front-end most "free Twitter data"
    guides still point to): dead. X sent cease-and-desist notices to
    public instance operators in 2026 and the project is confirmed shut
    down, not just unreliable -- this is not a "some instances are down"
    situation.
  - Third-party scraper APIs researched (TwitterAPI.io, SocialData.tools,
    Xpoz, FetchLayer, GetXAPI, Sorsa): real products, but NOT free on an
    ongoing basis -- each offers either a one-time trial credit (e.g.
    Xpoz: 500 credits once; TwitterAPI.io: $0.10 once) or a metered paid
    plan. None has a free tier that could run continuously inside a poll
    loop without eventually needing a card on file.

So this layer does NOT pull from Twitter/X, and does not claim to. It pulls
from DexScreener's public API (api.dexscreener.com/token-boosts/*), which
IS genuinely free, keyless, and (per docs.dexscreener.com/api/reference,
checked Sept 23 2026) carries no rate limit or ToS risk for this use.

Why a DexScreener "boost" is still a real buzz signal, not a random
substitute: a boost costs real money and is bought specifically to push a
token's visibility on DexScreener (where memecoin traders actually look).
A token getting boosted -- especially repeatedly, or for a large total
amount -- means real people are spending real money to promote it *right
now*, which is a legitimate, if different, proxy for the same underlying
thing Ali wants (catching a coin that's getting real community heat before
or as it runs). This is surfaced to Ali as exactly that: a proxy, not
Twitter data, never mislabeled as one.

Two free, keyless endpoints, fetched ONCE per poll cycle (not once per
token -- see fetch_boost_board) and matched in-memory against whatever
candidate tokens that cycle already has:
    GET /token-boosts/latest/v1  -- newest boost purchases
    GET /token-boosts/top/v1     -- currently highest-total boosted tokens

FAILS OPEN AS "no signal", NEVER BLOCKS: any network error or unexpected
response shape yields tag="none", ok=False -- same fail-open contract as
Layer 3's Adanos client. A broken buzz check must never crash or stall the
rest of the poll cycle.
"""
from dataclasses import dataclass
from typing import Optional

from utils.http import get_json

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"

# DexScreener's own chainId strings, mapped from this project's chain names.
# Robinhood Chain's slug ("robinhood") confirmed live Sept 23, 2026 while
# researching Ali's RBD/RobinDog question (RBD's own pair came back with
# chainId="robinhood" from the boosts endpoint) -- previously left
# unmapped as unconfirmed; now real.
CHAIN_ID_MAP = {
    "bsc": "bsc",
    "solana": "solana",
    "robinhood_chain": "robinhood",
}

# DexScreener sells boosts in fixed packages; a totalAmount at/above this
# means multiple real purchases have stacked on one token, not a single
# small buy -- arbitrary threshold, documented rather than hidden.
BOOST_SURGE_MIN_TOTAL = 500


@dataclass
class BuzzResult:
    tag: str  # "none" | "boosted" | "surging"
    amount: Optional[float] = None
    total_amount: Optional[float] = None


def fetch_boost_board() -> dict:
    """One call each to latest + top boosts -- meant to be called ONCE per
    poll cycle (run_poll_fast / run_poll_slow), then matched in-memory
    against every candidate token that cycle via check_buzz, rather than
    refetched per token. Fails open: a failed sub-call just yields an empty
    list for that half of the board rather than raising."""
    latest = get_json(f"{DEXSCREENER_BASE_URL}/token-boosts/latest/v1")
    top = get_json(f"{DEXSCREENER_BASE_URL}/token-boosts/top/v1")
    ok = bool(latest.get("ok")) or bool(top.get("ok"))
    return {
        "ok": ok,
        "latest": (latest.get("json") or []) if latest.get("ok") else [],
        "top": (top.get("json") or []) if top.get("ok") else [],
    }


def _find_entry(board_list: list, chain_id: str, token_address: str) -> Optional[dict]:
    token_address_lower = (token_address or "").lower()
    for entry in board_list or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("chainId") != chain_id:
            continue
        if (entry.get("tokenAddress") or "").lower() == token_address_lower:
            return entry
    return None


def check_buzz(board: Optional[dict], chain: str, token_address: str) -> BuzzResult:
    """Pure function -- no network call. board is whatever fetch_boost_board
    returned this cycle (or None, if the caller has no board yet -- yields
    'none' rather than crashing)."""
    if not board or not token_address:
        return BuzzResult(tag="none")
    chain_id = CHAIN_ID_MAP.get(chain)
    if not chain_id:
        return BuzzResult(tag="none")

    entry = _find_entry(board.get("top") or [], chain_id, token_address) or \
        _find_entry(board.get("latest") or [], chain_id, token_address)
    if not entry:
        return BuzzResult(tag="none")

    total = entry.get("totalAmount")
    amount = entry.get("amount")
    if isinstance(total, (int, float)) and total >= BOOST_SURGE_MIN_TOTAL:
        return BuzzResult(tag="surging", amount=amount, total_amount=total)
    return BuzzResult(tag="boosted", amount=amount, total_amount=total)

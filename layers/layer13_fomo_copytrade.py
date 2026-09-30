"""
Layer 13 -- Fomo copy-trading + thesis detection (dashboard-only).

Ali, Sept 30 2026: "in fomo trading section known trader always leaves
thesis on some coins, our system should be able to detect those as well
along with verifying them and add to the scoring of the coins band" and,
after correcting an earlier assumption, "you already have a list of the
known traders... 35 or 38 people" -- confirmed real: roster.py's 38-name
SELL_WATCH_ROSTER, sourced from Ali's own Fomo "Following" screenshots
(Sept 24 2026), matches fomoapi.io's own trader handles/display names
directly (verified live Sept 30: fomoapi.io's #1 24h leaderboard entry,
displayName "point farm capital", is a byte-for-byte match against
roster.TIER_1's "point farm capital").

WHAT THIS IS: a REST client for fomoapi.io (an independent, unofficial
third-party API over the Fomo social-trading dataset -- see
https://fomoapi.io/docs, key verified live against Ali's own
FOMOAPI_API_KEY Sept 30 2026) plus the detection/scoring glue that turns
a roster trader's buy or thesis into a dashboard card.

WHAT THIS IS NOT: an execution layer. fomoapi.io's data endpoints
(everything this module calls) are read-only. A SEPARATE paid product
exists (POST /v2/trading/buy|sell, a $1,000-to-start dedicated trading
account) that Ali may or may not purchase later -- that is a distinct,
explicit purchase decision on his side, wired nowhere in this codebase,
and this module has no code path that could place an order even if a
trading-account key were configured. See FINAL_CHECKLIST_2026-09-27.md's
Sept 30 ~2:05 PM update for the full writeup.

DESIGN RULE, same as Layer 12's caller tag (see layer12_caller_channels.py's
docstring -- this is the same principle, not a new one): a Fomo signal is
corroborating color, never a trigger on its own. Every coin a roster
trader buys or posts a thesis on still runs through the SAME Layer 0
structural scoring (layers.layer0_scoring.score_solana_mint) used for
every other coin in this system -- "trader bought it" and "our own score
says it's safe" are shown side by side, never collapsed into one signal.
Nothing here sends a Telegram alert (Ali, Sept 30 2026: "alert should show
in my dashboard not on telegram") -- state.record_fomo_signal is the only
write, and dashboard.py is the only reader.

BUDGET NOTE: fomoapi.io calls are metered in credits, not requests (see
docs -- leaderboard/alerts/theses are 125-1,250 credits each, free tier
is 250,000/month, no card). Coin SCORING after a Fomo signal fires still
spends real MadeOnSol budget (score_solana_mint = 3 calls), so this is
called from scheduler.run_poll_madeonsol() (Ali's own PC, stable IP),
never from GitHub Actions -- same architectural split as Layer 1/8/2+9,
see run_poll_madeonsol's own docstring for why.
"""
import time
from typing import List, Optional

from config import CONFIG
from utils.http import get_json
import state
from layers.roster import SELL_WATCH_ROSTER, tier_of
from layers.layer0_scoring import score_solana_mint

# fomoapi.io chain name -> this codebase's own chain identifier. Fomo/
# fomoapi.io cover more chains than this system scores (Robinhood Chain,
# base, bsc, ethereum, monad, arc) -- only solana is scored today (the
# only chain score_solana_mint supports), so every other chain's signal is
# still recorded to the dashboard (Ali sees the trader/thesis either way)
# but score/band stay None with a plain reason, never a guessed number.
SCORABLE_CHAINS = {"solana"}

_ROSTER_LOOKUP = {name.strip().lower(): name for name in SELL_WATCH_ROSTER}


def _match_roster(handle: Optional[str], display_name: Optional[str] = None) -> Optional[str]:
    """Returns the canonical roster.py name if `handle` or `display_name`
    matches (case-insensitive, whitespace-trimmed) a tracked trader, else
    None. Checks both fields because fomoapi.io alerts carry only a bare
    `trader` handle string while leaderboard/thesis rows carry both a
    handle and a displayName, and roster.py's names were transcribed by
    hand from Ali's screenshots so either could be the literal match."""
    for candidate in (handle, display_name):
        if not candidate:
            continue
        hit = _ROSTER_LOOKUP.get(candidate.strip().lower())
        if hit:
            return hit
    return None


def _headers() -> dict:
    return {"authorization": f"Bearer {CONFIG.fomoapi_api_key}"}


def fetch_fomo_alerts(since_iso: Optional[str] = None, alert_type: Optional[str] = None,
                       limit: int = 100) -> dict:
    """GET /v2/alerts -- the REST firehose fallback (buy/sell/thesis),
    newest first. Used instead of the WSS /ws/alerts stream because this
    runs as a bounded poll cycle (Task Scheduler, ~15 min), not a
    long-lived process -- a socket would need to stay connected between
    cycles, which nothing in this codebase's process model supports today.
    Fails closed (never raises) exactly like every other optional-layer
    fetch in this codebase; caller checks result["ok"]."""
    if not CONFIG.fomoapi_ready():
        return {"ok": False, "reason": "FOMOAPI_API_KEY not configured"}
    params = {"limit": limit}
    if alert_type:
        params["type"] = alert_type
    if since_iso:
        params["since"] = since_iso
    result = get_json(f"{CONFIG.fomoapi_base_url}/v2/alerts", headers=_headers(),
                       params=params, timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return {"ok": False, "reason": f"fomoapi.io /v2/alerts failed (HTTP {result.get('status_code')})"}
    body = result.get("json") or {}
    alerts = body.get("alerts") if isinstance(body, dict) else None
    if alerts is None:
        alerts = body if isinstance(body, list) else []
    return {"ok": True, "alerts": alerts}


def fetch_leaderboard(window: str = "24h", limit: int = 150) -> dict:
    """GET /v2/leaderboard/{window}. window is one of 24h/7d/30d/all
    (fomoapi.io's own set) -- no validation here beyond passing it
    through, a bad value is fomoapi.io's 400 to report back, not ours to
    pre-guess."""
    if not CONFIG.fomoapi_ready():
        return {"ok": False, "reason": "FOMOAPI_API_KEY not configured"}
    result = get_json(f"{CONFIG.fomoapi_base_url}/v2/leaderboard/{window}", headers=_headers(),
                       params={"limit": limit}, timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return {"ok": False, "reason": f"fomoapi.io /v2/leaderboard/{window} failed (HTTP {result.get('status_code')})"}
    body = result.get("json") or {}
    return {"ok": True, "traders": body.get("traders") or []}


def fetch_theses_for_token(mint: str, network: str = "sol", threshold: int = 10,
                            limit: int = 50) -> dict:
    """GET /v2/thesis/token/{mint} -- theses.exact written reasoning
    behind trades on this coin, real position-size-gated (?threshold=,
    default 10 here to skip dust positions, not fomoapi.io's own default
    of 0) so noise doesn't flood the dashboard on a high-volume coin."""
    if not CONFIG.fomoapi_ready():
        return {"ok": False, "reason": "FOMOAPI_API_KEY not configured"}
    result = get_json(f"{CONFIG.fomoapi_base_url}/v2/thesis/token/{mint}", headers=_headers(),
                       params={"network": network, "threshold": threshold, "limit": limit},
                       timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return {"ok": False, "reason": f"fomoapi.io /v2/thesis/token/{mint} failed (HTTP {result.get('status_code')})"}
    body = result.get("json") or {}
    if body.get("available") is False:
        return {"ok": True, "theses": []}
    return {"ok": True, "theses": body.get("theses") or []}


def fetch_trader_balance_usd(handle: str) -> Optional[float]:
    """GET /v2/users/{handle}/balances -- returns totalValueUsd (portfolio
    value across every chain fomoapi.io covers), the real "balance" figure
    Ali's $5k new-trader threshold is checked against. Returns None on any
    failure (missing key, 404, network) -- caller must treat None as
    "unknown," never as $0, so a fetch failure can't silently exclude a
    real candidate."""
    if not CONFIG.fomoapi_ready():
        return None
    result = get_json(f"{CONFIG.fomoapi_base_url}/v2/users/{handle}/balances", headers=_headers(),
                       timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return None
    body = result.get("json") or {}
    value = body.get("totalValueUsd")
    return float(value) if isinstance(value, (int, float)) else None


def fetch_trader_pnl(handle: str) -> dict:
    """GET /v2/users/{handle} -- Ali's ask (Sept 30 2026): show 24h/7d/30d
    profit-or-loss for every trader on both dashboard tables (roster
    signals and new-trader candidates), not just a single-window number.
    fomoapi.io's user-profile endpoint carries pnl.24h/7d/30d/all in one
    call (see this module's own docstring for where that shape was
    confirmed). Fails closed per-field, same convention as
    fetch_trader_balance_usd: a missing/unparseable field is None
    ("unknown"), never 0 -- a real $0 pnl and a failed lookup must never
    look the same on the dashboard."""
    empty = {"pnl_24h": None, "pnl_7d": None, "pnl_30d": None}
    if not CONFIG.fomoapi_ready():
        return empty
    result = get_json(f"{CONFIG.fomoapi_base_url}/v2/users/{handle}", headers=_headers(),
                       timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return empty
    body = result.get("json") or {}
    pnl = body.get("pnl") or {}
    if not isinstance(pnl, dict):
        return empty
    out = {}
    for key, field in (("pnl_24h", "24h"), ("pnl_7d", "7d"), ("pnl_30d", "30d")):
        v = pnl.get(field)
        out[key] = float(v) if isinstance(v, (int, float)) else None
    return out


def _score_if_solana(chain: str, mint: str) -> dict:
    """Runs the coin through the SAME Layer 0 structural scoring every
    other alert in this system uses -- never a Fomo-specific score. See
    module docstring's DESIGN RULE. chain names here are fomoapi.io's own
    ("solana"); everything outside SCORABLE_CHAINS returns a plain
    unscored reason rather than guessing."""
    if chain not in SCORABLE_CHAINS:
        return {"score": None, "band": None, "reason": f"chain '{chain}' not scored by this system yet"}
    result = score_solana_mint(mint, "solana", is_pregraduation=False)
    if result.get("error"):
        return {"score": None, "band": None, "reason": result["error"]}
    sr = result["score"]
    return {"score": sr.score, "band": sr.band, "reason": None}


def detect_roster_buys_and_theses(alerts: List[dict]) -> dict:
    """Pure-ish function (no network beyond the scoring call): walks a
    batch of fomoapi.io /v2/alerts rows, matches each against Ali's
    roster, scores the coin, and records a dashboard signal for every
    match. Returns counts for the cycle summary. `alerts` rows use
    fomoapi.io's documented shape -- alertType (buy/sell/thesis/...),
    trader (handle), token, tokenAddress, chain, usdValue, text."""
    buys_recorded = 0
    theses_recorded = 0
    for a in alerts:
        alert_type = a.get("alertType")
        if alert_type not in ("buy", "thesis"):
            continue
        handle = a.get("trader")
        roster_name = _match_roster(handle)
        if not roster_name:
            continue  # not one of Ali's tracked traders -- ignore, same as Layer 12's channel gate
        chain = a.get("chain") or "solana"
        mint = a.get("tokenAddress")
        symbol = a.get("token") or (mint[:8] if mint else "?")
        scored = _score_if_solana(chain, mint) if mint else {"score": None, "band": None,
                                                               "reason": "no tokenAddress on this alert"}
        trader_pnl = fetch_trader_pnl(handle) if handle else {"pnl_24h": None, "pnl_7d": None, "pnl_30d": None}
        if alert_type == "buy":
            state.record_fomo_signal(
                kind="buy", trader=roster_name, tier=tier_of(roster_name),
                token_symbol=symbol, token_address=mint or "", chain=chain,
                detail=a.get("text") or f"{roster_name} bought {symbol}",
                score=scored["score"], band=scored["band"],
                pnl_24h=trader_pnl["pnl_24h"], pnl_7d=trader_pnl["pnl_7d"], pnl_30d=trader_pnl["pnl_30d"],
            )
            buys_recorded += 1
        else:  # thesis
            state.record_fomo_signal(
                kind="thesis", trader=roster_name, tier=tier_of(roster_name),
                token_symbol=symbol, token_address=mint or "", chain=chain,
                detail=a.get("text") or f"{roster_name} posted a thesis on {symbol}",
                score=scored["score"], band=scored["band"],
                thesis_text=a.get("text"),
                thesis_link=(a.get("links") or [{}])[0].get("link") if a.get("links") else None,
                pnl_24h=trader_pnl["pnl_24h"], pnl_7d=trader_pnl["pnl_7d"], pnl_30d=trader_pnl["pnl_30d"],
            )
            theses_recorded += 1
    return {"buys_recorded": buys_recorded, "theses_recorded": theses_recorded}


def find_new_trader_candidates(leaderboard_traders: List[dict], min_balance_usd: float = 5000.0,
                                max_checked: int = 25) -> dict:
    """Ali's locked spec (Sept 30 2026): watch for traders NOT on the
    current roster with >= $5,000 balance, surface for approval, never
    auto-add. `leaderboard_traders` is a /v2/leaderboard/{window} result,
    already ranked by fomoapi.io -- checked top-down so the highest-PnL
    off-roster names get first look at the bounded per-cycle balance-call
    budget (max_checked, default 25 -- each /balances call costs real
    fomoapi.io credits, see module docstring's BUDGET NOTE)."""
    candidates_found = 0
    checked = 0
    for row in leaderboard_traders:
        if checked >= max_checked:
            break
        handle = row.get("handle")
        if not handle or _match_roster(handle, row.get("displayName")):
            continue  # already tracked -- not a candidate
        checked += 1
        balance = fetch_trader_balance_usd(handle)
        if balance is not None and balance >= min_balance_usd:
            trader_pnl = fetch_trader_pnl(handle)
            state.record_fomo_candidate(
                handle=handle, display_name=row.get("displayName") or handle,
                balance_usd=balance, pnl_usd=row.get("pnlUsd"), volume_usd=row.get("volumeUsd"),
                pnl_24h=trader_pnl["pnl_24h"], pnl_7d=trader_pnl["pnl_7d"], pnl_30d=trader_pnl["pnl_30d"],
            )
            candidates_found += 1
    return {"checked": checked, "candidates_found": candidates_found}

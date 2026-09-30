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

# fomoapi.io chain name -> this codebase's chain id. Anything not listed is
# passed through lowercased (still recorded to the dashboard either way).
FOMO_CHAIN_ALIASES = {
    "sol": "solana", "solana": "solana",
    "bsc": "bsc", "bnb": "bsc", "binance": "bsc",
    "base": "base",
    "robinhood": "robinhood_chain", "robinhood_chain": "robinhood_chain", "rhc": "robinhood_chain",
    "eth": "ethereum", "ethereum": "ethereum",
}

# Roster-buy convergence window for Stage 2 -- same 1h window Layer 2's
# MadeOnSol convergence uses (layers/layer2_convergence.CONVERGENCE_WINDOW).
FOMO_CONVERGENCE_WINDOW_SECONDS = 3600


def _normalize(name: Optional[str]) -> str:
    """Case/spacing/punctuation-insensitive key. Real reason (Sept 30 2026):
    roster.py was hand-transcribed from Fomo screenshots, which show
    DISPLAY names ("point farm capital", "Logan Lim", "Old Man Pervert"),
    while fomoapi.io /v2/alerts rows carry only the bare `trader` HANDLE
    (no spaces, often different casing/underscores). An exact-string match
    silently dropped every roster member whose handle isn't byte-identical
    to their display name -- the likely cause of zero roster buys."""
    if not name:
        return ""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


_ROSTER_LOOKUP = {_normalize(name): name for name in SELL_WATCH_ROSTER}


def normalize_chain(chain: Optional[str]) -> str:
    c = (chain or "solana").strip().lower()
    return FOMO_CHAIN_ALIASES.get(c, c)


def _match_roster(handle: Optional[str], display_name: Optional[str] = None,
                  learned: Optional[dict] = None, promoted: Optional[dict] = None) -> Optional[str]:
    """Returns the canonical roster name if `handle` or `display_name`
    matches a tracked trader, else None. Checks, in order: the static
    roster (normalized), handles learned from the leaderboard (handle ->
    display name that IS on the roster, see learn_handles_from_leaderboard),
    and traders auto-promoted from the candidate list (see
    maybe_auto_promote)."""
    learned = state.get_fomo_handle_map() if learned is None else learned
    promoted = state.get_fomo_promoted() if promoted is None else promoted
    for candidate in (handle, display_name):
        key = _normalize(candidate)
        if not key:
            continue
        if key in _ROSTER_LOOKUP:
            return _ROSTER_LOOKUP[key]
        if key in learned:
            return learned[key]
        if key in promoted:
            return promoted[key].get("display_name") or candidate
    return None


def tier_for(roster_name: str, promoted: Optional[dict] = None) -> str:
    t = tier_of(roster_name)
    if t != "untracked":
        return t
    promoted = state.get_fomo_promoted() if promoted is None else promoted
    key = _normalize(roster_name)
    if key in promoted or key in {_normalize(p.get("display_name")) for p in promoted.values()}:
        return "auto-promoted"
    return t


def learn_handles_from_leaderboard(traders: List[dict]) -> int:
    """Leaderboard rows carry BOTH handle and displayName. Whenever a
    displayName matches a roster name but the handle doesn't, remember
    handle -> roster name so bare-handle /v2/alerts rows match next time.
    Returns how many new mappings were learned."""
    learned = state.get_fomo_handle_map()
    added = 0
    for row in traders or []:
        handle_key = _normalize(row.get("handle"))
        if not handle_key or handle_key in _ROSTER_LOOKUP or handle_key in learned:
            continue
        hit = _ROSTER_LOOKUP.get(_normalize(row.get("displayName")))
        if hit:
            learned[handle_key] = hit
            added += 1
    if added:
        state.set_fomo_handle_map(learned)
    return added


def _alert_display_name(a: dict) -> Optional[str]:
    for key in ("displayName", "traderDisplayName", "traderName", "name"):
        if a.get(key):
            return a[key]
    user = a.get("user") or a.get("traderInfo")
    if isinstance(user, dict):
        return user.get("displayName") or user.get("name")
    return None


def alert_ts_seconds(a: dict) -> Optional[float]:
    """fomoapi.io's timestamp field/unit isn't pinned down live yet -- the
    old cursor code assumed `ts` in milliseconds on alerts[0] only, so any
    other shape meant the cursor NEVER advanced (re-reading the same 100
    alerts every cycle, duplicating dashboard rows). Accepts ts/timestamp/
    createdAt/time as epoch s or ms, or an ISO-8601 string."""
    import datetime
    for key in ("ts", "timestamp", "createdAt", "created_at", "time"):
        v = a.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return float(v) / 1000.0 if v > 1e11 else float(v)
        if isinstance(v, str):
            try:
                return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                try:
                    f = float(v)
                    return f / 1000.0 if f > 1e11 else f
                except ValueError:
                    continue
    return None


def newest_alert_iso(alerts: List[dict]) -> Optional[str]:
    import datetime
    stamps = [t for t in (alert_ts_seconds(a) for a in alerts or []) if t is not None]
    if not stamps:
        return None
    return datetime.datetime.fromtimestamp(max(stamps), tz=datetime.timezone.utc).isoformat()


def _signal_id(a: dict, kind: str, handle: Optional[str], mint: Optional[str]) -> str:
    if a.get("id") is not None:
        return f"fomo:{a['id']}"
    return f"fomo:{kind}:{_normalize(handle)}:{mint or ''}:{alert_ts_seconds(a) or ''}"


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


def _score_if_solana(chain: str, mint: str, allow_paid: bool = True) -> dict:
    """Runs the coin through the SAME Layer 0 structural scoring every
    other alert in this system uses -- never a Fomo-specific score. See
    module docstring's DESIGN RULE. chain names here are fomoapi.io's own
    ("solana"); everything outside SCORABLE_CHAINS returns a plain
    unscored reason rather than guessing."""
    if chain not in SCORABLE_CHAINS:
        return {"score": None, "band": None, "reason": f"chain '{chain}' not scored by this system yet"}
    if not allow_paid:
        return {"score": None, "band": None,
                "reason": "not scored on the GitHub Actions run (MadeOnSol budget is reserved for the PC run)"}
    result = score_solana_mint(mint, "solana", is_pregraduation=False)
    if result.get("error"):
        return {"score": None, "band": None, "reason": result["error"]}
    sr = result["score"]
    return {"score": sr.score, "band": sr.band, "reason": None}


def detect_roster_buys_and_theses(alerts: List[dict], allow_paid_scoring: bool = True) -> dict:
    """Walks a batch of fomoapi.io /v2/alerts rows, matches each against
    Ali's roster (see _match_roster), scores the coin, and records a
    dashboard signal for every match. Duplicates (same alert seen on an
    overlapping cursor or by both the PC and GitHub Actions runs) are
    skipped by state.record_fomo_signal's signal_id dedupe.

    Also returns:
      - unmatched: {handle: count} of buy/thesis traders NOT on the roster
        (what diag_fomo_roster.py and the dashboard show, so a transcription
        mismatch is visible instead of silently producing zero rows);
      - convergence: [{chain, mint, count, traders}] -- tokens where 2+
        distinct roster traders bought inside FOMO_CONVERGENCE_WINDOW_SECONDS,
        which scheduler.py feeds to executor Stage 2;
      - buyers_by_handle: {handle: [(chain, mint), ...]} for every buy
        (roster or not), used by the candidate insider check."""
    buys_recorded = 0
    theses_recorded = 0
    unmatched = {}
    convergence = {}
    buyers_by_handle = {}
    learned = state.get_fomo_handle_map()
    promoted = state.get_fomo_promoted()
    seen = state.fomo_signal_ids()
    for a in alerts:
        alert_type = (a.get("alertType") or a.get("type") or "").lower()
        if alert_type not in ("buy", "thesis"):
            continue
        handle = a.get("trader") or a.get("handle")
        display = _alert_display_name(a)
        chain = normalize_chain(a.get("chain") or a.get("network"))
        mint = a.get("tokenAddress") or a.get("address") or a.get("mint")
        if alert_type == "buy" and handle and mint:
            buyers_by_handle.setdefault(handle, []).append((chain, mint))
        roster_name = _match_roster(handle, display, learned=learned, promoted=promoted)
        if not roster_name:
            label = handle or display or "?"
            unmatched[label] = unmatched.get(label, 0) + 1
            continue
        symbol = a.get("token") or a.get("symbol") or (mint[:8] if mint else "?")
        sid = _signal_id(a, alert_type, handle, mint)
        if sid in seen:
            continue
        seen.add(sid)
        scored = (_score_if_solana(chain, mint, allow_paid=allow_paid_scoring) if mint
                  else {"score": None, "band": None, "reason": "no tokenAddress on this alert"})
        trader_pnl = fetch_trader_pnl(handle) if handle else {"pnl_24h": None, "pnl_7d": None, "pnl_30d": None}
        ts = alert_ts_seconds(a)
        common = dict(trader=roster_name, tier=tier_for(roster_name, promoted),
                      token_symbol=symbol, token_address=mint or "", chain=chain,
                      score=scored["score"], band=scored["band"],
                      pnl_24h=trader_pnl["pnl_24h"], pnl_7d=trader_pnl["pnl_7d"],
                      pnl_30d=trader_pnl["pnl_30d"], signal_id=sid, ts=ts)
        if alert_type == "buy":
            state.record_fomo_signal(kind="buy", detail=a.get("text") or f"{roster_name} bought {symbol}",
                                     **common)
            buys_recorded += 1
            if mint:
                count, traders = state.record_fomo_roster_buy(
                    chain, mint, roster_name, ts=ts, window_seconds=FOMO_CONVERGENCE_WINDOW_SECONDS)
                if count >= 2:
                    convergence[(chain, mint)] = {"chain": chain, "mint": mint, "count": count,
                                                  "traders": traders}
        else:
            links = a.get("links") or []
            state.record_fomo_signal(
                kind="thesis", detail=a.get("text") or f"{roster_name} posted a thesis on {symbol}",
                thesis_text=a.get("text"),
                thesis_link=(links[0] or {}).get("link") if links and isinstance(links[0], dict) else None,
                **common)
            theses_recorded += 1
    if unmatched:
        state.note_fomo_unmatched_handles(unmatched)
    return {"buys_recorded": buys_recorded, "theses_recorded": theses_recorded,
            "unmatched": unmatched, "convergence": list(convergence.values()),
            "buyers_by_handle": buyers_by_handle}


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

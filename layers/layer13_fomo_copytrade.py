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


# --- Credit governor (Sept 30 2026) ---
# REAL finding from the live diagnostic run: fomoapi.io answered HTTP 402
# {"error": "credits_exhausted", "remaining": 0} with x-credits-limit:
# 250000 -- the first Layer 13 build drained the whole free monthly bucket
# within hours of going live (leaderboard + up to 25 balance lookups + a
# profile lookup per signal, every cycle). That is why roster buys and
# theses showed zero. Every fomoapi.io call now goes through _fomo_get,
# which (a) refuses while a 402 back-off is active, (b) enforces a daily
# credit budget, and (c) records the real x-credits-cost / -remaining
# headers so the dashboard and diag_live.py show the true balance.
DEFAULT_CALL_COST = 125          # observed x-credits-cost for /v2/alerts
CREDITS_BACKOFF_SECONDS = 6 * 3600


def _daily_budget() -> int:
    import os
    try:
        return int(os.environ.get("FOMOAPI_DAILY_CREDIT_BUDGET", "7500"))  # ~250k/month over 31 days, with margin
    except ValueError:
        return 7500


def _fomo_get(path: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
    gov = state.get_fomo_credit_state()
    now = time.time()
    if gov.get("backoff_until", 0) > now:
        return {"ok": False, "status_code": 402,
                "reason": f"fomoapi.io credits exhausted -- backing off until "
                          f"{time.strftime('%H:%M UTC', time.gmtime(gov['backoff_until']))}"}
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    spent = gov.get("spent_today", 0) if gov.get("day") == day else 0
    if spent + DEFAULT_CALL_COST > _daily_budget():
        return {"ok": False, "status_code": None,
                "reason": f"fomoapi.io daily credit budget reached ({spent}/{_daily_budget()})"}
    result = get_json(f"{CONFIG.fomoapi_base_url}{path}", headers=_headers(), params=params or {},
                      timeout=timeout or CONFIG.http_timeout_seconds)
    hdrs = {str(k).lower(): v for k, v in (result.get("headers") or {}).items()}
    try:
        cost = int(hdrs.get("x-credits-cost", DEFAULT_CALL_COST))
    except (TypeError, ValueError):
        cost = DEFAULT_CALL_COST
    remaining = hdrs.get("x-credits-remaining")
    update = {"day": day, "spent_today": spent + (cost if result.get("ok") else 0), "last_call_ts": now}
    if remaining is not None:
        try:
            update["remaining"] = int(remaining)
        except (TypeError, ValueError):
            pass
    if result.get("status_code") == 402:
        update["backoff_until"] = now + CREDITS_BACKOFF_SECONDS
        update["last_error"] = "credits_exhausted"
    state.set_fomo_credit_state({**gov, **update})
    return result


def fetch_fomo_alerts(since_iso: Optional[str] = None, alert_type: Optional[str] = None,
                       limit: int = 100, before_iso: Optional[str] = None) -> dict:
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
    if before_iso:
        params["before"] = before_iso
    result = _fomo_get(f"/v2/alerts", params=params, timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return {"ok": False, "status_code": result.get("status_code"),
                "reason": result.get("reason") or f"fomoapi.io /v2/alerts failed (HTTP {result.get('status_code')})"}
    body = result.get("json") or {}
    alerts = body.get("alerts") if isinstance(body, dict) else None
    if alerts is None:
        alerts = body if isinstance(body, list) else []
    return {"ok": True, "alerts": alerts}


def fetch_new_alerts(since_iso: Optional[str], max_pages: int = 3, limit: int = 100) -> dict:
    """/v2/alerts returns the newest `limit` alerts across ALL Fomo users,
    newest first. On a busy feed one page can cover only a few minutes, so
    a single call per poll silently drops everything older (a real reason
    roster buys could show zero). Pages backward with `before=<oldest seen>`
    until the page reaches the stored cursor, up to max_pages. If the API
    ignores `before` (same page comes back) it stops instead of looping.
    `gap` is True when the cursor still wasn't reached -- reported, not
    hidden, so the dashboard/diag can say alerts were missed."""
    since_ts = None
    if since_iso:
        since_ts = alert_ts_seconds({"ts": since_iso})
    collected = []
    seen_ids = set()
    before = None
    pages = 0
    reached = since_ts is None
    for _ in range(max_pages):
        res = fetch_fomo_alerts(since_iso=since_iso, limit=limit, before_iso=before)
        pages += 1
        if not res.get("ok"):
            if not collected:
                return res
            break
        page = res.get("alerts") or []
        fresh = []
        for a in page:
            key = a.get("id") or (a.get("trader"), a.get("tokenAddress"), a.get("alertType"), alert_ts_seconds(a))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            fresh.append(a)
        if not fresh:
            break  # API ignored `before`, or nothing older -- stop
        collected.extend(fresh)
        stamps = [t for t in (alert_ts_seconds(a) for a in fresh) if t is not None]
        if not stamps or len(page) < limit:
            reached = True
            break
        oldest = min(stamps)
        if since_ts is not None and oldest <= since_ts:
            reached = True
            break
        import datetime
        before = datetime.datetime.fromtimestamp(oldest, tz=datetime.timezone.utc).isoformat()
    if since_ts is not None:
        collected = [a for a in collected if (alert_ts_seconds(a) or since_ts + 1) > since_ts]
    return {"ok": True, "alerts": collected, "pages": pages, "gap": not reached}


def fetch_leaderboard(window: str = "24h", limit: int = 150) -> dict:
    """GET /v2/leaderboard/{window}. window is one of 24h/7d/30d/all
    (fomoapi.io's own set) -- no validation here beyond passing it
    through, a bad value is fomoapi.io's 400 to report back, not ours to
    pre-guess."""
    if not CONFIG.fomoapi_ready():
        return {"ok": False, "reason": "FOMOAPI_API_KEY not configured"}
    result = _fomo_get(f"/v2/leaderboard/{window}", params={"limit": limit}, timeout=CONFIG.http_timeout_seconds)
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
    result = _fomo_get(f"/v2/thesis/token/{mint}", params={"network": network, "threshold": threshold, "limit": limit},
                       timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return {"ok": False, "reason": f"fomoapi.io /v2/thesis/token/{mint} failed (HTTP {result.get('status_code')})"}
    body = result.get("json") or {}
    if body.get("available") is False:
        return {"ok": True, "theses": []}
    return {"ok": True, "theses": body.get("theses") or []}


# Credit-budget caches (Sept 30 2026). fomoapi.io bills per call in credits
# (free tier 250,000/month). The first build called /users/{handle} for
# EVERY signal and /users/{handle}/balances for up to 25 leaderboard names
# EVERY cycle -- at a 10-15 min cadence that can exhaust the month's
# credits in days, after which every call fails and the dashboard shows
# zero. Trader profiles/balances change slowly, so they're cached.
PROFILE_CACHE_SECONDS = 6 * 3600
BALANCE_CACHE_SECONDS = 24 * 3600


def fetch_trader_profile(handle: str) -> Optional[dict]:
    """GET /v2/users/{handle}, cached PROFILE_CACHE_SECONDS. None on any
    failure (never cached, so it's retried next time)."""
    if not handle or not CONFIG.fomoapi_ready():
        return None
    cache_key = f"fomo_profile:{_normalize(handle)}"
    cached = state.cache_get(cache_key, PROFILE_CACHE_SECONDS)
    if cached is not None:
        return cached
    result = _fomo_get(f"/v2/users/{handle}", timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return None
    body = result.get("json")
    if not isinstance(body, dict):
        return None
    state.cache_set(cache_key, body)
    return body


def fetch_trader_balance_usd(handle: str) -> Optional[float]:
    """GET /v2/users/{handle}/balances -- returns totalValueUsd (portfolio
    value across every chain fomoapi.io covers), the real "balance" figure
    Ali's $5k new-trader threshold is checked against. Cached
    BALANCE_CACHE_SECONDS. Returns None on any failure -- caller must treat
    None as "unknown," never as $0."""
    if not CONFIG.fomoapi_ready():
        return None
    cache_key = f"fomo_balance:{_normalize(handle)}"
    cached = state.cache_get(cache_key, BALANCE_CACHE_SECONDS)
    if cached is not None:
        return cached.get("usd")
    result = _fomo_get(f"/v2/users/{handle}/balances", timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return None
    body = result.get("json") or {}
    value = body.get("totalValueUsd")
    usd = float(value) if isinstance(value, (int, float)) else None
    if usd is not None:
        state.cache_set(cache_key, {"usd": usd})
    return usd


def fetch_trader_pnl(handle: str) -> dict:
    """24h/7d/30d profit-or-loss for a trader (Ali's Sept 30 ask), read from
    the cached /v2/users/{handle} profile's `pnl` object. Fails closed
    per-field: missing/unparseable is None ("unknown"), never 0."""
    empty = {"pnl_24h": None, "pnl_7d": None, "pnl_30d": None}
    body = fetch_trader_profile(handle) if handle else None
    if not body:
        return empty
    pnl = body.get("pnl") or {}
    if not isinstance(pnl, dict):
        return empty
    out = {}
    for key, field in (("pnl_24h", "24h"), ("pnl_7d", "7d"), ("pnl_30d", "30d")):
        v = pnl.get(field)
        out[key] = float(v) if isinstance(v, (int, float)) else None
    return out


_BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def _looks_like_solana_address(v) -> bool:
    return isinstance(v, str) and 32 <= len(v) <= 44 and set(v) <= _BASE58


def extract_solana_wallets(profile: Optional[dict], limit: int = 3) -> List[str]:
    """Pulls Solana wallet addresses out of a /v2/users/{handle} profile.
    The exact field isn't pinned down live yet (diag_live.py prints the
    real shape), so this walks any key named like wallet/address and keeps
    values that are valid base58 of Solana-address length -- never
    guessing an address that isn't literally in the response."""
    found = []

    def walk(obj, key_hint=""):
        if len(found) >= limit:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, str(k).lower())
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key_hint)
        elif ("wallet" in key_hint or "address" in key_hint or key_hint in ("sol", "solana")) \
                and _looks_like_solana_address(obj) and obj not in found:
            found.append(obj)

    walk(profile or {})
    return found


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
    roster_sells = []
    buy_archive = []
    learned = state.get_fomo_handle_map()
    promoted = state.get_fomo_promoted()
    seen = state.fomo_signal_ids()
    for a in alerts:
        alert_type = (a.get("alertType") or a.get("type") or "").lower()
        if alert_type not in ("buy", "thesis", "sell"):
            continue
        handle = a.get("trader") or a.get("handle")
        display = _alert_display_name(a)
        chain = normalize_chain(a.get("chain") or a.get("network"))
        mint = a.get("tokenAddress") or a.get("address") or a.get("mint")
        if alert_type == "buy" and handle and mint:
            buyers_by_handle.setdefault(handle, []).append((chain, mint))
            # Every trader's buy (roster or not) goes to a 7-day archive so the
            # replay can measure WHICH Fomo traders are profitable to copy
            # (backtest_alert_replay.py --source fomo). No extra API calls.
            buy_archive.append({"trader": handle, "chain": chain, "token_address": mint,
                                "ts": alert_ts_seconds(a) or time.time()})
        roster_name = _match_roster(handle, display, learned=learned, promoted=promoted)
        if alert_type == "sell":
            # Checklist 4.5: a roster trader's sell feeds executor.copy_exit
            # (no scoring, no PnL lookup -- zero extra credits).
            if roster_name and mint:
                roster_sells.append({"chain": chain, "mint": mint, "trader": roster_name})
            continue
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
    if buy_archive:
        state.append_fomo_buy_archive(buy_archive)
    if unmatched:
        state.note_fomo_unmatched_handles(unmatched)
    return {"buys_recorded": buys_recorded, "theses_recorded": theses_recorded,
            "unmatched": unmatched, "convergence": list(convergence.values()),
            "buyers_by_handle": buyers_by_handle, "roster_sells": roster_sells}


def _auto_promote_enabled() -> bool:
    import os
    return os.environ.get("FOMO_AUTO_PROMOTE", "true").strip().lower() != "false"


def promotion_verdict(balance_usd: Optional[float], pnl_7d: Optional[float], pnl_30d: Optional[float],
                      insider: Optional[bool], min_balance_usd: float = 5000.0) -> tuple:
    """Auto-promote rule (Ali, Sept 30 2026: "apply all 3 things" --
    overriding the earlier never-auto-add lock). A candidate is promoted onto
    the tracked list only when ALL hold: balance >= $5k (Ali's number),
    positive 7d AND 30d PnL (consistently profitable, not one lucky day),
    and NOT flagged as an insider (a deployer-funded wallet trades its own
    supply -- following it means buying what it's about to dump). Unknown
    PnL never promotes. Returns (promote: bool, reason: str)."""
    if balance_usd is None or balance_usd < min_balance_usd:
        return False, "balance below $5k or unknown"
    if insider is True:
        return False, "insider-flagged (funded by / is a token deployer)"
    if pnl_7d is None or pnl_30d is None:
        return False, "7d/30d PnL unknown"
    if pnl_7d <= 0 or pnl_30d <= 0:
        return False, f"not consistently profitable (7d {pnl_7d:+,.0f}, 30d {pnl_30d:+,.0f})"
    return True, f"balance ${balance_usd:,.0f}, 7d {pnl_7d:+,.0f}, 30d {pnl_30d:+,.0f}, not insider"


def find_new_trader_candidates(leaderboard_traders: List[dict], min_balance_usd: float = 5000.0,
                                max_checked: int = 10, buyers_by_handle: Optional[dict] = None) -> dict:
    """Ali's spec (Sept 30 2026): watch for traders NOT on the roster with
    >= $5,000 balance and surface them. Now also (a) runs the free-RPC
    insider check (layers.layer10_insider_cluster.check_trader_insider)
    against the Solana coins that trader was seen buying, and (b) auto-
    promotes candidates passing promotion_verdict (FOMO_AUTO_PROMOTE=false
    turns promotion off). Checked top-down on the already-ranked leaderboard;
    balance lookups are cached 24h so re-seeing a trader costs no credits,
    and max_checked caps fresh lookups per run (credit budget)."""
    from layers.layer10_insider_cluster import check_trader_insider
    buyers_by_handle = buyers_by_handle or {}
    learned = state.get_fomo_handle_map()
    promoted = state.get_fomo_promoted()
    candidates_found = 0
    newly_promoted = []
    checked = 0
    for row in leaderboard_traders:
        if checked >= max_checked:
            break
        handle = row.get("handle")
        if not handle or _match_roster(handle, row.get("displayName"), learned=learned, promoted=promoted):
            continue  # already tracked -- not a candidate
        checked += 1
        balance = fetch_trader_balance_usd(handle)
        if balance is None or balance < min_balance_usd:
            continue
        trader_pnl = fetch_trader_pnl(handle)
        profile = fetch_trader_profile(handle)
        wallets = extract_solana_wallets(profile)
        mints = [m for c, m in buyers_by_handle.get(handle, []) if c == "solana"]
        try:
            insider = check_trader_insider(wallets, mints)
        except Exception as e:  # RPC trouble must never break discovery
            insider = {"insider": None, "detail": f"insider check error: {type(e).__name__}"}
        promote, why = promotion_verdict(balance, trader_pnl["pnl_7d"], trader_pnl["pnl_30d"],
                                         insider["insider"], min_balance_usd)
        promote = promote and _auto_promote_enabled()
        if promote:
            promoted[_normalize(handle)] = {"handle": handle, "display_name": row.get("displayName") or handle,
                                            "reason": why, "ts": time.time()}
            newly_promoted.append(handle)
        state.record_fomo_candidate(
            handle=handle, display_name=row.get("displayName") or handle,
            balance_usd=balance, pnl_usd=row.get("pnlUsd"), volume_usd=row.get("volumeUsd"),
            pnl_24h=trader_pnl["pnl_24h"], pnl_7d=trader_pnl["pnl_7d"], pnl_30d=trader_pnl["pnl_30d"],
            insider=insider["insider"], insider_detail=insider["detail"],
            wallet=wallets[0] if wallets else None, promoted=promote, promotion_reason=why,
        )
        candidates_found += 1
    if newly_promoted:
        state.set_fomo_promoted(promoted)
    return {"checked": checked, "candidates_found": candidates_found, "promoted": newly_promoted}

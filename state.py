"""
Cross-poll-cycle state for Layers 6/8/9, backed by Upstash Redis's REST API
when configured (UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN),
falling back to a local JSON file otherwise (useful for `--self-test` and
local runs, but does NOT persist between separate GitHub Actions runs --
only Upstash does that).

Why Upstash and not the alternatives considered:
  - GitHub's own actions/cache isn't built for a read-update-save-back
    pattern inside one workflow (restore/save race on overlapping runs),
    and entries evict after 7 days unused.
  - Committing state back to the repo via git works but is risky in a
    scheduled job: merge conflicts on overlapping runs, and it rewrites the
    repo every poll cycle just to store a few KB of state.
  - Upstash's REST API is a plain HTTP GET/POST per command (or one
    /pipeline call for several) -- no persistent connection, which is
    exactly the shape a fresh-process-per-run GitHub Actions job needs.

Every write here is a single small JSON blob under one key -- deliberately
simple over building a real Redis data-structure layer, since the volumes
involved (a handful of tracked tokens/wallets) don't need it.
"""
import json
import os
import time
from typing import Optional, List, Tuple

from config import CONFIG
from utils.http import get_json, post_json, ApiUnreachable

LOCAL_STATE_FILE = os.environ.get("GEM_ALERT_STATE_FILE", ".gem_alert_state.json")
MC_HISTORY_MAX_POINTS = 20
MC_HISTORY_MAX_AGE_SECONDS = 2 * 60 * 60  # 2 hours -- comfortably past Layer 8's 30-min window


def backend() -> str:
    return CONFIG.state_backend()


# --- low-level get/set, one JSON value per key ---

def _upstash_headers():
    return {"Authorization": f"Bearer {CONFIG.upstash_redis_rest_token}"}


def _upstash_get_raw(key: str) -> Optional[str]:
    try:
        result = get_json(f"{CONFIG.upstash_redis_rest_url}/get/{key}", headers=_upstash_headers())
    except ApiUnreachable:
        return None
    if not result["ok"]:
        return None
    body = result.get("json") or {}
    return body.get("result")  # Upstash wraps the value as {"result": "<value or null>"}


def _upstash_set_raw(key: str, value_str: str) -> bool:
    try:
        result = post_json(f"{CONFIG.upstash_redis_rest_url}/set/{key}",
                            headers=_upstash_headers(), data=value_str)
    except ApiUnreachable:
        return False
    return result["ok"]


def _upstash_get_many_raw(keys: List[str]) -> dict:
    """Batch GET via Upstash's /pipeline endpoint -- ONE HTTP round trip
    for however many keys, instead of one round trip per key (Ali, Sept 30
    2026: the dashboard's /api/data was hanging for minutes because
    position_state.list_open_positions()/list_closed_positions() did a
    sequential state.get_value() per position in the index -- with dozens
    of positions accumulated from weeks of testing, that's dozens of
    sequential HTTP calls, each subject to utils/http.py's own retry/
    backoff stack, easily compounding into minutes on any single slow or
    dropped call. This collapses all of them into one pipelined request.
    Falls back to per-key calls if Upstash is unreachable or the pipeline
    call itself fails, so behavior degrades rather than breaks."""
    if not keys:
        return {}
    try:
        result = post_json(f"{CONFIG.upstash_redis_rest_url}/pipeline",
                            headers=_upstash_headers(),
                            json=[["GET", k] for k in keys])
    except ApiUnreachable:
        return {k: _upstash_get_raw(k) for k in keys}
    if not result["ok"]:
        return {k: _upstash_get_raw(k) for k in keys}
    body = result.get("json")
    if not isinstance(body, list) or len(body) != len(keys):
        return {k: _upstash_get_raw(k) for k in keys}
    out = {}
    for k, item in zip(keys, body):
        out[k] = (item or {}).get("result") if isinstance(item, dict) else None
    return out


def _local_load_all() -> dict:
    if not os.path.exists(LOCAL_STATE_FILE):
        return {}
    try:
        with open(LOCAL_STATE_FILE) as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _local_save_all(data: dict):
    with open(LOCAL_STATE_FILE, "w") as f:
        json.dump(data, f)


def get_value(key: str):
    if backend() == "upstash":
        raw = _upstash_get_raw(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return _local_load_all().get(key)


def get_values(keys: List[str]) -> dict:
    """Batch version of get_value() -- returns {key: decoded_value_or_None}.
    Uses one pipelined Upstash call instead of len(keys) separate ones (see
    _upstash_get_many_raw's docstring). Local-file backend already has
    everything in memory, so it just does the equivalent dict lookups."""
    if backend() == "upstash":
        raw_map = _upstash_get_many_raw(keys)
        out = {}
        for k in keys:
            raw = raw_map.get(k)
            if raw is None:
                out[k] = None
                continue
            try:
                out[k] = json.loads(raw)
            except ValueError:
                out[k] = None
        return out
    data = _local_load_all()
    return {k: data.get(k) for k in keys}


def set_value(key: str, value) -> bool:
    encoded = json.dumps(value)
    if backend() == "upstash":
        return _upstash_set_raw(key, encoded)
    data = _local_load_all()
    data[key] = value
    _local_save_all(data)
    return True


# --- Layer 6: wallet holdings snapshot ---

def save_snapshot(held_assets: list):
    set_value("wallet_snapshot_latest", {"ts": time.time(), "assets": held_assets})


def last_snapshot():
    return get_value("wallet_snapshot_latest")


def held_token_addresses() -> set:
    snap = last_snapshot()
    if not snap:
        return set()
    tokens = set()
    for a in snap.get("assets", []):
        addr = (a.get("asset", {}) or {}).get("contracts_balances") or (a.get("asset", {}) or {}).get("symbol")
        if addr:
            tokens.add(addr)
    return tokens


# --- Layer 8: rolling market-cap history per token ---

def record_mc_point(token: str, mc_usd: float, ts: Optional[float] = None):
    ts = ts if ts is not None else time.time()
    key = f"mc_history:{token}"
    history = get_value(key) or []
    history.append([ts, mc_usd])
    cutoff = time.time() - MC_HISTORY_MAX_AGE_SECONDS
    history = [p for p in history if p[0] >= cutoff][-MC_HISTORY_MAX_POINTS:]
    set_value(key, history)


def get_mc_history(token: str) -> List[Tuple[float, float]]:
    return [(p[0], p[1]) for p in (get_value(f"mc_history:{token}") or [])]


# --- Holder-count history per token (Sept 25, 2026) -- real wiring for
# Layer 0/0b's holder_growth_rate_per_hr signal, which was previously
# hardcoded to None everywhere (see layers/layer0_scoring.py's
# compute_holder_growth_rate_per_hr) -- 20% of every score's total weight
# was a fixed placeholder, not real per-token data, on every chain. Same
# append-only capped-list pattern as record_mc_point/get_mc_history above,
# just keyed on holder count instead of market cap -- deliberately generic
# (works for any chain that can supply a current holder count).
HOLDER_HISTORY_MAX_POINTS = 20
HOLDER_HISTORY_MAX_AGE_SECONDS = 2 * 60 * 60  # matches MC_HISTORY_MAX_AGE_SECONDS


def record_holder_point(token: str, holder_count: float, ts: Optional[float] = None):
    ts = ts if ts is not None else time.time()
    key = f"holder_history:{token}"
    history = get_value(key) or []
    history.append([ts, holder_count])
    cutoff = time.time() - HOLDER_HISTORY_MAX_AGE_SECONDS
    history = [p for p in history if p[0] >= cutoff][-HOLDER_HISTORY_MAX_POINTS:]
    set_value(key, history)


def get_holder_history(token: str) -> List[Tuple[float, float]]:
    return [(p[0], p[1]) for p in (get_value(f"holder_history:{token}") or [])]


# --- Deployer track record -- self-built reputation from OUR OWN closed
# trades (Sept 30 2026, Ali: "build that and also do that vice versa on if
# any of my trade made loss or the developer rugged to add that in that
# list to blacklist this developer and avoid coins launched from him").
#
# Keyed by deployer wallet address. Every entry here comes from a position
# THIS system actually opened and closed -- never an external/unverified
# claim about a wallet's history -- so "trusted"/"blacklisted" only ever
# reflects a wallet we've directly traded against and watched the real
# outcome of (see executor/position_state.py's close_position, which
# writes here automatically on every close that resolves a deployer wallet
# and a priced P&L). Same append-only capped-list pattern as
# mc_history/holder_history above.
DEPLOYER_HISTORY_MAX_POINTS = 50
DEPLOYER_BLACKLIST_RUG_THRESHOLD = 1  # any single confirmed rug is enough -- zero tolerance
DEPLOYER_TRUST_MIN_WINS = 2  # need repeat proof, not one lucky trade


def record_deployer_outcome(deployer_wallet: str, token: str, chain: str,
                             outcome: str, pnl_usd: Optional[float], ts: Optional[float] = None):
    """outcome: 'win' | 'loss' | 'rug'. 'rug' means Layer 6's own
    exit-risk detector fired a defensive sell on a position this deployer's
    token was behind (see defensive_sell.py) -- a directly observed event,
    not a guess."""
    ts = ts if ts is not None else time.time()
    key = f"deployer_history:{deployer_wallet}"
    history = get_value(key) or []
    history.append({"ts": ts, "token": token, "chain": chain, "outcome": outcome,
                     "pnl_usd": pnl_usd})
    history = history[-DEPLOYER_HISTORY_MAX_POINTS:]
    set_value(key, history)


def get_deployer_history(deployer_wallet: str) -> List[dict]:
    return get_value(f"deployer_history:{deployer_wallet}") or []


def get_deployer_reputation(deployer_wallet: str) -> dict:
    """Summarizes get_deployer_history into wins/losses/rugs/net P&L plus a
    tier label:
      - 'blacklisted': >=1 confirmed rug against us. Zero tolerance -- a rug
        here means our own Layer 6 exit-risk detector already fired a real
        defensive sell against this exact wallet, so one is enough.
      - 'trusted': zero rugs, 2+ real wins, net positive P&L -- repeat
        proof, not a single lucky trade.
      - 'neutral': not enough data yet, or a mixed record that clears
        neither bar.
    """
    history = get_deployer_history(deployer_wallet)
    wins = sum(1 for h in history if h.get("outcome") == "win")
    losses = sum(1 for h in history if h.get("outcome") == "loss")
    rugs = sum(1 for h in history if h.get("outcome") == "rug")
    net_pnl_usd = sum((h.get("pnl_usd") or 0.0) for h in history)
    if rugs >= DEPLOYER_BLACKLIST_RUG_THRESHOLD:
        tier = "blacklisted"
    elif rugs == 0 and wins >= DEPLOYER_TRUST_MIN_WINS and net_pnl_usd > 0:
        tier = "trusted"
    else:
        tier = "neutral"
    return {"wallet": deployer_wallet, "wins": wins, "losses": losses, "rugs": rugs,
            "net_pnl_usd": net_pnl_usd, "sample_size": len(history), "tier": tier}


def is_deployer_blacklisted(deployer_wallet: Optional[str]) -> bool:
    if not deployer_wallet:
        return False
    return get_deployer_reputation(deployer_wallet)["tier"] == "blacklisted"


def is_deployer_trusted(deployer_wallet: Optional[str]) -> bool:
    if not deployer_wallet:
        return False
    return get_deployer_reputation(deployer_wallet)["tier"] == "trusted"


# ---------------------------------------------------------------------------
# MadeOnSol daily call budget -- added Sept 25 2026, real production bug
# caught live: Ali's BASIC-tier key has a real, confirmed 200-calls/day cap
# ("Daily rate limit exceeded -- 200/day for BASIC tier. Resets at midnight
# UTC.", seen live running backtest.py). Layer 1 (poll-fast.yml, every 10
# min, 1 real MadeOnSol call/cycle after chain_for_cycle's alternation) is
# ~144 calls/day on its own. Layer 8's deep-scoring (poll-slow.yml, every 20
# min, up to LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE=3 tokens/chain * 3 calls
# each * up to 2 chains) can burst as high as 18 calls/cycle -- worst case,
# fully saturated every cycle, that alone is ~1296 calls/day. Extending cron
# cadence alone doesn't cap this (alert volume is bursty, not steady), so
# this is a real per-day budget gate every MadeOnSol-calling function checks
# before spending calls, not a guess based on timing.
#
# Bucketed by UTC calendar day to match MadeOnSol's own stated reset time
# exactly (confirmed live: "Resets at midnight UTC") -- so this tracker's
# reset always lines up with the real one, not an approximation.
# ---------------------------------------------------------------------------
MADEONSOL_DAILY_BUDGET = 190  # safety margin under the real, confirmed 200/day BASIC-tier cap


def _madeonsol_budget_key() -> str:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return f"madeonsol_calls:{day}"


def record_madeonsol_calls(n: int = 1):
    """Call once per REAL MadeOnSol HTTP request actually sent (success or
    failure -- a rejected/errored call still counts against the real quota,
    same as any other API rate limit)."""
    key = _madeonsol_budget_key()
    count = get_value(key) or 0
    set_value(key, count + n)


def madeonsol_calls_today() -> int:
    return get_value(_madeonsol_budget_key()) or 0


def madeonsol_budget_remaining() -> int:
    return max(0, MADEONSOL_DAILY_BUDGET - madeonsol_calls_today())


# --- Layer 7: rolling alert-event log, for cross-layer correlation
# (Ali, Sept 23 2026: wired tonight). One list per TOKEN (not global) so a
# busy cycle across many tokens doesn't force scanning one huge shared list
# -- same sharding choice as mc_history above. Capped on both age and count,
# same two-sided cap pattern as record_mc_point, since a token that alerts
# constantly (e.g. a MEGA-ALERT feedback loop) shouldn't grow this key
# without bound. ---
ALERT_EVENT_MAX_AGE_SECONDS = 3600  # matches layer7_correlation.CORRELATION_WINDOW's upper bound
ALERT_EVENT_MAX_PER_TOKEN = 20


def log_alert_event(token: str, layer: str, ts: Optional[float] = None):
    ts = ts if ts is not None else time.time()
    key = f"alert_events:{token}"
    events = get_value(key) or []
    events.append([ts, layer])
    cutoff = time.time() - ALERT_EVENT_MAX_AGE_SECONDS
    events = [e for e in events if e[0] >= cutoff][-ALERT_EVENT_MAX_PER_TOKEN:]
    set_value(key, events)


def get_recent_alert_events(token: str) -> List[Tuple[float, str]]:
    cutoff = time.time() - ALERT_EVENT_MAX_AGE_SECONDS
    return [(e[0], e[1]) for e in (get_value(f"alert_events:{token}") or []) if e[0] >= cutoff]


# --- Dashboard: global rolling feed of full alert content (Ali, Sept 23
# 2026 -- "the dashboard should show the entire view... alerts coming").
# Separate from alert_events above on purpose: alert_events is per-TOKEN
# and stores only (ts, layer) for Layer 7's correlation math; this is one
# GLOBAL list storing the actual rendered alert (headline, tags, chain,
# layer) so the dashboard can show a real feed without re-deriving it from
# individual token keys. Capped the same two-sided way as mc_history. ---
ALERT_FEED_MAX_AGE_SECONDS = 24 * 3600
ALERT_FEED_MAX_ITEMS = 300
ALERT_FEED_KEY = "dashboard_alert_feed"


def log_full_alert(layer: str, chain: str, token_symbol: str, token_address: str,
                    headline: str, tags: dict, ts: Optional[float] = None):
    ts = ts if ts is not None else time.time()
    feed = get_value(ALERT_FEED_KEY) or []
    feed.append({
        "ts": ts, "layer": layer, "chain": chain, "token_symbol": token_symbol,
        "token_address": token_address, "headline": headline, "tags": tags or {},
    })
    cutoff = time.time() - ALERT_FEED_MAX_AGE_SECONDS
    feed = [f for f in feed if f["ts"] >= cutoff][-ALERT_FEED_MAX_ITEMS:]
    set_value(ALERT_FEED_KEY, feed)


def get_alert_feed(limit: int = 100) -> list:
    feed = get_value(ALERT_FEED_KEY) or []
    return list(reversed(feed))[:limit]  # newest first


# --- Dashboard: global rolling trade log (Tasks Left #3/#6, Sept 25 2026 --
# "dashboard should show... records of buy, sell open positions realized
# profit"). Same shape/pattern as the alert feed above: one global
# append-only list, capped both by age and count, newest first on read. One
# entry per real execute_buy_*/execute_sell() attempt from swap_executor.py
# (see its _log_trade helper) -- success AND failure both logged, so the
# dashboard shows what was actually attempted, not just what worked. ---
TRADE_LOG_MAX_AGE_SECONDS = 30 * 24 * 3600
TRADE_LOG_MAX_ITEMS = 500
TRADE_LOG_KEY = "dashboard_trade_log"


def log_trade_event(side: str, chain: str, token: str, ok: bool, reason: str,
                     amount_tokens: Optional[float] = None, usd_amount: Optional[float] = None,
                     tx_signature: Optional[str] = None, ts: Optional[float] = None):
    ts = ts if ts is not None else time.time()
    log = get_value(TRADE_LOG_KEY) or []
    log.append({
        "ts": ts, "side": side, "chain": chain, "token": token, "ok": ok, "reason": reason,
        "amount_tokens": amount_tokens, "usd_amount": usd_amount, "tx_signature": tx_signature,
    })
    cutoff = time.time() - TRADE_LOG_MAX_AGE_SECONDS
    log = [t for t in log if t["ts"] >= cutoff][-TRADE_LOG_MAX_ITEMS:]
    set_value(TRADE_LOG_KEY, log)


def get_trade_log(limit: int = 100) -> list:
    log = get_value(TRADE_LOG_KEY) or []
    return list(reversed(log))[:limit]  # newest first


# --- Layer 2b: self-computed pump.fun "smart money" wallet tracker (Ali,
# Sept 23 2026 -- see layers/layer2b_pumpfun_smart_money.py's docstring for
# why this exists instead of a third-party leaderboard). One record per
# (wallet, mint): an OPEN position (buy seen, no matching sell yet) or,
# once a sell closes it, the realized SOL PnL rolls into that wallet's
# running stats. ---
def _pumpfun_position_key(wallet: str, mint: str) -> str:
    return f"pumpfun_pos:{wallet}:{mint}"


def _pumpfun_wallet_stats_key(wallet: str) -> str:
    return f"pumpfun_wallet_stats:{wallet}"


def record_pumpfun_trade(wallet: str, mint: str, direction: str,
                          sol_delta: Optional[float], ts: Optional[float] = None) -> Optional[dict]:
    """direction: 'buy' or 'sell'. A buy opens/adds to a position (sol_delta
    expected negative -- stored as spent = abs(sol_delta)). A sell against
    an open position closes it and rolls (sol_received - sol_spent) into
    the wallet's running stats, returning the updated stats dict. A sell
    with NO open position on that mint (this system started watching
    mid-position, or the matching buy used an undecoded instruction
    variant) is ignored for PnL purposes and returns None -- a PnL number
    built on half a trade would be actively misleading, not just
    incomplete."""
    ts = ts if ts is not None else time.time()
    pos_key = _pumpfun_position_key(wallet, mint)
    if direction == "buy":
        pos = get_value(pos_key) or {"sol_spent": 0.0, "opened_ts": ts}
        pos["sol_spent"] += abs(sol_delta) if sol_delta is not None else 0.0
        set_value(pos_key, pos)
        return None
    if direction == "sell":
        pos = get_value(pos_key)
        if not pos:
            return None
        sol_received = sol_delta if (sol_delta is not None and sol_delta > 0) else 0.0
        realized_pnl = sol_received - pos["sol_spent"]
        set_value(pos_key, None)
        stats = get_value(_pumpfun_wallet_stats_key(wallet)) or {"closed_trades": 0, "wins": 0, "total_realized_sol": 0.0}
        stats["closed_trades"] += 1
        stats["total_realized_sol"] += realized_pnl
        if realized_pnl > 0:
            stats["wins"] += 1
        set_value(_pumpfun_wallet_stats_key(wallet), stats)
        return stats
    return None


def get_pumpfun_wallet_stats(wallet: str) -> Optional[dict]:
    return get_value(_pumpfun_wallet_stats_key(wallet))


# --- Layer 9: prior balance per (wallet, token), for % of position sold ---

def record_balance(wallet: str, token: str, balance: float):
    set_value(f"balance:{wallet}:{token}", balance)


def get_prior_balance(wallet: str, token: str) -> Optional[float]:
    return get_value(f"balance:{wallet}:{token}")


def prior_balances_map(pairs: List[Tuple[str, str]]) -> dict:
    """Convenience for layer9_sell_mirror.detect_sell_events, which wants a
    {(wallet, token): balance} dict built up front rather than one lookup
    per event."""
    return {(w, t): get_prior_balance(w, t) for w, t in pairs if get_prior_balance(w, t) is not None}


# --- Layer 8 event-triggered re-scoring: last full score per token, and a
# pending queue of (token, chain) pairs waiting for the next slow-cycle deep
# score. Replaces a timer-based re-check (would either waste budget on quiet
# coins or miss a fast turnaround) with "re-score only when the token's own
# cheap metric -- market cap, the same series already recorded for Layer 8's
# momentum check -- has actually moved since the last full score." ---

def set_last_score(token: str, score: int, band: str, mc_usd: Optional[float] = None):
    set_value(f"last_score:{token}", {"score": score, "band": band, "mc": mc_usd, "ts": time.time()})


def get_last_score(token: str) -> Optional[dict]:
    return get_value(f"last_score:{token}")


def queue_rescan(token: str, chain: str, is_pregraduation: bool):
    """Adds (token, chain) to the pending deep-rescore queue, de-duped --
    queuing the same token twice before it's popped is a no-op."""
    pending = get_value("pending_deep_rescans") or []
    if not any(p.get("token") == token for p in pending):
        pending.append({"token": token, "chain": chain, "is_pregraduation": is_pregraduation})
        set_value("pending_deep_rescans", pending)


def pop_pending_rescans(limit: int) -> list:
    """Pops up to `limit` entries off the front of the queue (oldest first)
    and persists the remainder -- whatever doesn't fit this cycle's cap
    waits for the next one rather than being dropped."""
    pending = get_value("pending_deep_rescans") or []
    popped, remaining = pending[:limit], pending[limit:]
    if popped:
        set_value("pending_deep_rescans", remaining)
    return popped


def pending_rescan_count() -> int:
    return len(get_value("pending_deep_rescans") or [])


# --- Layer 1: alternating-chain discovery cadence (halves Layer 1's
# MadeOnSol calls/day, see layers.layer1_deployer.chain_for_cycle and the
# README's call-budget section for why). Each chain is only checked on
# roughly every other fast cycle now, so its last-successfully-checked
# timestamp is tracked here and passed back as MadeOnSol's `since` param on
# its next check -- otherwise an alert that fires during that chain's
# skipped cycle would be silently missed rather than just delayed. ---

def binance_seen_listings() -> set:
    """Layer 4's Binance new-listings feed has no dedup of its own -- every
    poll cycle it just returns whatever's currently in Binance's top-N
    recent-listings list, so without this, the same 1-3 articles get
    re-alerted on every single cycle until they fall out of that list
    (confirmed live, Sept 24 2026 -- this is what was flooding Ali's
    Telegram with repeat Binance alerts). Keyed by article code (falls
    back to title if code is missing), capped like layer0c's seen-set."""
    return set(get_value("binance_seen_listings") or [])


BINANCE_SEEN_CAP = 500


def mark_binance_seen(article_ids) -> None:
    existing = list(get_value("binance_seen_listings") or [])
    existing_set = set(existing)
    new_ones = [a for a in article_ids if a and a not in existing_set]
    merged = existing + new_ones
    if len(merged) > BINANCE_SEEN_CAP:
        merged = merged[-BINANCE_SEEN_CAP:]
    set_value("binance_seen_listings", merged)


def cryptopanic_seen_posts() -> set:
    """Layer 4's CryptoPanic rising-news feed has the same no-dedup problem
    Binance's listing feed had (see binance_seen_listings) -- filter=rising
    returns whatever is currently trending, so the same post would re-alert
    every cycle without this. Keyed by CryptoPanic's own post id."""
    return set(get_value("cryptopanic_seen_posts") or [])


CRYPTOPANIC_SEEN_CAP = 500


def mark_cryptopanic_seen(post_ids) -> None:
    existing = list(get_value("cryptopanic_seen_posts") or [])
    existing_set = set(existing)
    new_ones = [p for p in post_ids if p and p not in existing_set]
    merged = existing + new_ones
    if len(merged) > CRYPTOPANIC_SEEN_CAP:
        merged = merged[-CRYPTOPANIC_SEEN_CAP:]
    set_value("cryptopanic_seen_posts", merged)


def coindesk_seen_posts() -> set:
    """Same no-dedup problem as cryptopanic_seen_posts (see its docstring)
    -- CoinDesk's RSS feed always returns its current front page, so the
    same story would re-alert every cycle without this. Keyed by the RSS
    item's guid (falls back to link/title if a feed item is missing one --
    see parse_coindesk_rss)."""
    return set(get_value("coindesk_seen_posts") or [])


COINDESK_SEEN_CAP = 500


def mark_coindesk_seen(post_ids) -> None:
    existing = list(get_value("coindesk_seen_posts") or [])
    existing_set = set(existing)
    new_ones = [p for p in post_ids if p and p not in existing_set]
    merged = existing + new_ones
    if len(merged) > COINDESK_SEEN_CAP:
        merged = merged[-COINDESK_SEEN_CAP:]
    set_value("coindesk_seen_posts", merged)


def layer0c_seen_mints() -> set:
    """Layer 0c (StonkFun) has no documented 'since' cursor on
    /tokens?sort=newest, unlike Layer 1's MadeOnSol endpoint -- so dedup
    is done with a capped set of already-alerted mints instead of a
    timestamp. Capped at LAYER0C_SEEN_CAP so this never grows unbounded."""
    return set(get_value("layer0c_seen_mints") or [])


LAYER0C_SEEN_CAP = 1000


def mark_layer0c_seen(mints) -> None:
    """mints: iterable of mint strings just alerted on this cycle. Merges
    into the stored seen-set and truncates to the most recent
    LAYER0C_SEEN_CAP entries (oldest dropped first)."""
    existing = list(get_value("layer0c_seen_mints") or [])
    existing_set = set(existing)
    new_ones = [m for m in mints if m and m not in existing_set]
    merged = existing + new_ones
    if len(merged) > LAYER0C_SEEN_CAP:
        merged = merged[-LAYER0C_SEEN_CAP:]
    set_value("layer0c_seen_mints", merged)


def layer0c_momentum_checked_mints() -> set:
    """Separate from layer0c_seen_mints() (that's for the SOL-only
    structural-score alert path) -- this tracks which mints the momentum
    scan (poll_layer0c_momentum, cross-quote-type) has already spent a
    deep /tokens/{mint} lookup on, so re-checking the same recent launch
    every fast cycle doesn't burn the per-cycle deep-lookup cap for
    nothing."""
    return set(get_value("layer0c_momentum_checked_mints") or [])


LAYER0C_MOMENTUM_CHECKED_CAP = 2000


def mark_layer0c_momentum_checked(mints) -> None:
    existing = list(get_value("layer0c_momentum_checked_mints") or [])
    existing_set = set(existing)
    new_ones = [m for m in mints if m and m not in existing_set]
    merged = existing + new_ones
    if len(merged) > LAYER0C_MOMENTUM_CHECKED_CAP:
        merged = merged[-LAYER0C_MOMENTUM_CHECKED_CAP:]
    set_value("layer0c_momentum_checked_mints", merged)


def layer0c_snipe_bought_mints() -> set:
    """Mints the snipe worker has already bought (or already fired a
    stage1 trigger for) -- separate from the alert-only dedup sets above.
    Prevents the continuous worker from re-buying the same mint every poll
    cycle while it stays inside the lookback window."""
    return set(get_value("layer0c_snipe_bought_mints") or [])


LAYER0C_SNIPE_BOUGHT_CAP = 2000


def mark_layer0c_snipe_bought(mints) -> None:
    existing = list(get_value("layer0c_snipe_bought_mints") or [])
    existing_set = set(existing)
    new_ones = [m for m in mints if m and m not in existing_set]
    merged = existing + new_ones
    if len(merged) > LAYER0C_SNIPE_BOUGHT_CAP:
        merged = merged[-LAYER0C_SNIPE_BOUGHT_CAP:]
    set_value("layer0c_snipe_bought_mints", merged)


def get_layer1_last_checked(chain: str) -> Optional[str]:
    return get_value(f"layer1_last_checked:{chain}")


def set_layer1_last_checked(chain: str, iso_ts: str):
    set_value(f"layer1_last_checked:{chain}", iso_ts)


# --- Layer 3: Adanos monthly-quota tracking. Adanos's free tier is 250
# requests/month total (confirmed at adanos.org/pricing) -- far too tight to
# poll broadly, so layers.layer3_backing_check checks a short, already-
# interesting list of tokens rather than scanning everything, and reads
# Adanos's own X-RateLimit-Remaining-Monthly response header after every call
# so the NEXT call can be skipped pre-emptively once the quota's known to be
# at zero, instead of only finding out via a 429 after spending it. ---

def record_adanos_quota(remaining: Optional[int]):
    set_value("adanos_quota", {"remaining": remaining, "ts": time.time()})


def get_adanos_quota() -> Optional[dict]:
    return get_value("adanos_quota")


# --- Poll-fast self-loop lock (Sept 27 2026) -----------------------------
# GitHub's own `schedule:` cron trigger for poll-fast.yml is declared as
# every 10 minutes but was confirmed live (Sept 27 2026) to actually fire
# every 2.5-5.5 hours in practice -- a GitHub Actions platform throttle on
# scheduled events, not a bug in this code. Fix: poll-fast now loops
# internally with a real wall-clock sleep(600) instead of relying on
# GitHub to re-trigger it (see run_poll_fast_loop in scheduler.py). This
# lock stops two loops from running at once if GitHub's `schedule:` does
# fire again while a previous loop from an earlier run is still going --
# without it, a double-fire would silently double the MadeOnSol call rate.

def _upstash_set_raw_opts(key: str, value_str: str, query: str) -> Optional[dict]:
    """Low-level SET with Upstash REST query-string options (EX=, NX=, etc).
    Returns the parsed response body, or None if unreachable."""
    try:
        url = f"{CONFIG.upstash_redis_rest_url}/set/{key}?{query}"
        result = post_json(url, headers=_upstash_headers(), data=value_str)
    except ApiUnreachable:
        return None
    if not result["ok"]:
        return None
    return result.get("json") or {}


def acquire_lock(key: str, ttl_seconds: int, owner: str = "1") -> bool:
    """True if the lock was newly acquired (key didn't already exist).
    Local-file backend has no cross-process concept of this, so it always
    grants the lock there -- local mode is never the overlapping-schedule
    case this exists for."""
    if backend() != "upstash":
        return True
    body = _upstash_set_raw_opts(key, owner, f"EX={int(ttl_seconds)}&NX=true")
    if body is None:
        # Upstash unreachable -- fail open rather than silently never polling;
        # worst case is a rare double-run, not a stuck system.
        return True
    return body.get("result") is not None


def refresh_lock(key: str, ttl_seconds: int, owner: str = "1") -> bool:
    """Re-affirms the TTL on a lock this process already holds. Returns
    False on any failure to reach Upstash -- caller should treat that as
    lock-lost and stop looping rather than assume it still holds."""
    if backend() != "upstash":
        return True
    body = _upstash_set_raw_opts(key, owner, f"EX={int(ttl_seconds)}")
    return body is not None


def release_lock(key: str):
    if backend() != "upstash":
        return
    try:
        get_json(f"{CONFIG.upstash_redis_rest_url}/del/{key}", headers=_upstash_headers())
    except ApiUnreachable:
        pass


# --- Soft-fail watch list (Ali, Sept 28 2026) -----------------------------
# Real gap Ali flagged live: a Solana/RHC token that fails Layer 0's
# structural score (band D) only gets a second look via
# scheduler._maybe_queue_rescan, which triggers ONLY when a fresh market-cap
# point comes in for it -- and the only source of fresh MC points today is
# Layer 2's tracked-KOL-wallet feed. A token no tracked wallet ever trades
# never gets a second MC point, so it silently never re-queues, even if it
# quietly becomes structurally clean (liquidity locked, concentration drops,
# real holder growth starts) within its first hours -- exactly the scenario
# Ali described: "our system not detecting it further as it had done the
# scan of the coin earlier."
#
# This tracks D-band Solana tokens independent of MC/KOL activity, so
# scheduler._run_soft_fail_watch_cycle can cheaply re-check them (via
# layer0_scoring.free_recheck_solana_signals -- ZERO MadeOnSol budget, only
# free RPC) on its own cadence and queue a real re-score (via the SAME
# queue_rescan/pop_pending_rescans path _maybe_queue_rescan already uses)
# only once a real improvement signal shows up. Bounded in both directions:
# capped item count (a busy night shouldn't grow this without bound, same
# philosophy as ALERT_FEED_MAX_ITEMS) and capped age -- Ali's own framing
# was "minutes and hours", not an indefinite hold, so a token that hasn't
# turned around within the window is dropped for good, same as a real
# hard-fail (mint/freeze authority live, honeypot) that was never likely to
# change in the first place.
SOFT_FAIL_WATCH_KEY = "soft_fail_watch"
SOFT_FAIL_WATCH_MAX_AGE_SECONDS = 12 * 3600
SOFT_FAIL_WATCH_MAX_ITEMS = 300


def watch_add(token: str, chain: str, is_pregraduation: bool, score: int, reasons: list,
              ts: Optional[float] = None):
    """Adds a D-band token to the soft-fail watch list, deduped by token --
    an already-watched token keeps its original first_seen_ts (the age
    window is measured from when it FIRST failed, not refreshed on a repeat
    D score every cycle) but gets its last-known score/reasons updated."""
    ts = ts if ts is not None else time.time()
    watch = get_value(SOFT_FAIL_WATCH_KEY) or []
    existing = next((w for w in watch if w.get("token") == token), None)
    if existing:
        existing["last_score"] = score
        existing["last_reasons"] = reasons
    else:
        watch.append({
            "token": token, "chain": chain, "is_pregraduation": is_pregraduation,
            "first_seen_ts": ts, "last_checked_ts": ts,
            "last_score": score, "last_reasons": reasons, "free_checks_done": 0,
        })
    cutoff = ts - SOFT_FAIL_WATCH_MAX_AGE_SECONDS
    watch = [w for w in watch if w.get("first_seen_ts", 0) >= cutoff][-SOFT_FAIL_WATCH_MAX_ITEMS:]
    set_value(SOFT_FAIL_WATCH_KEY, watch)


def get_soft_fail_watch() -> list:
    """Returns the current watch list, pruned of anything past
    SOFT_FAIL_WATCH_MAX_AGE_SECONDS -- self-cleaning on read, same pattern
    as the other capped lists in this file, so a token that never turns
    around ages out on its own without a separate sweep job."""
    watch = get_value(SOFT_FAIL_WATCH_KEY) or []
    cutoff = time.time() - SOFT_FAIL_WATCH_MAX_AGE_SECONDS
    fresh = [w for w in watch if w.get("first_seen_ts", 0) >= cutoff]
    if len(fresh) != len(watch):
        set_value(SOFT_FAIL_WATCH_KEY, fresh)
    return fresh


def watch_remove(token: str):
    watch = get_value(SOFT_FAIL_WATCH_KEY) or []
    remaining = [w for w in watch if w.get("token") != token]
    if len(remaining) != len(watch):
        set_value(SOFT_FAIL_WATCH_KEY, remaining)


def watch_touch(token: str, ts: Optional[float] = None):
    """Bumps last_checked_ts/free_checks_done for a watched token after a
    free-signal recheck, regardless of outcome -- lets the scheduler's
    per-cycle cap round-robin across the whole list (oldest-checked-first)
    instead of the same few tokens (whatever sorts first) hogging every
    cycle."""
    ts = ts if ts is not None else time.time()
    watch = get_value(SOFT_FAIL_WATCH_KEY) or []
    changed = False
    for w in watch:
        if w.get("token") == token:
            w["last_checked_ts"] = ts
            w["free_checks_done"] = w.get("free_checks_done", 0) + 1
            changed = True
    if changed:
        set_value(SOFT_FAIL_WATCH_KEY, watch)


# --- Post-alert monitoring pass (Ali, Sept 28 2026 -- checklist item: "a
# flagged token that rugs 10 minutes later still shows as a live alert with
# no correction"). This is the mirror image of the soft-fail watch list
# above: that one catches a D-band token that quietly gets BETTER after
# being suppressed; this one catches an A/B-band (or HIGH-RISK MOMENTUM)
# token that gets WORSE after being alerted on. Every real per-token alert
# scheduler._handle_scored actually sends gets a one-time follow-up entry
# here; scheduler._run_post_alert_monitor_cycle re-checks it once real price
# history exists for the 15-60 min window (see that function's docstring
# for why it's a single pass, not a repeating watch) and sends a DOWNGRADE
# follow-up if the token craters. Bounded the same way as SOFT_FAIL_WATCH
# above: capped item count and capped age, since a token this old has
# already aged out of the window this pass cares about either way.
POST_ALERT_MONITOR_KEY = "post_alert_monitor"
POST_ALERT_MONITOR_MIN_AGE_SECONDS = 15 * 60   # per spec: don't check before 15 min
POST_ALERT_MONITOR_MAX_AGE_SECONDS = 60 * 60   # per spec: window closes at 60 min
POST_ALERT_MONITOR_MAX_ITEMS = 300


def post_alert_monitor_add(token: str, chain: str, headline: str, band: str, score: int,
                            ts: Optional[float] = None, price_at_alert: Optional[float] = None):
    """Adds a just-sent alert to the post-alert monitor queue, deduped by
    token -- a token that alerts again before its first follow-up check
    fires just gets its headline/band/score/price_at_alert refreshed, not a
    second entry (one follow-up check per token in flight is enough; a
    second real alert on the same token already tells Ali something
    changed).

    price_at_alert (Ali, Sept 28 2026 -- closing the RHC gap this pass
    launched with): the Birdeye-covered chains (solana/base/bsc/ethereum)
    don't need this -- scheduler._run_post_alert_monitor_cycle re-derives
    the whole price path from real historical OHLCV at check time. Robinhood
    Chain has no Birdeye mapping, so its check instead compares a real
    DexScreener price snapshot taken now (this field) against one taken
    again at check time -- see
    layers.layer0_scoring.fetch_dexscreener_token_price_usd's docstring.
    None for any chain/entry that doesn't use the snapshot-compare path."""
    ts = ts if ts is not None else time.time()
    queue = get_value(POST_ALERT_MONITOR_KEY) or []
    existing = next((q for q in queue if q.get("token") == token), None)
    if existing:
        existing["headline"] = headline
        existing["band"] = band
        existing["score"] = score
        existing["price_at_alert"] = price_at_alert
    else:
        queue.append({
            "token": token, "chain": chain, "headline": headline, "band": band, "score": score,
            "alert_ts": ts, "checked": False, "price_at_alert": price_at_alert,
        })
    cutoff = ts - POST_ALERT_MONITOR_MAX_AGE_SECONDS
    queue = [q for q in queue if q.get("alert_ts", 0) >= cutoff][-POST_ALERT_MONITOR_MAX_ITEMS:]
    set_value(POST_ALERT_MONITOR_KEY, queue)


def get_post_alert_monitor() -> list:
    """Returns the current post-alert monitor queue, pruned of anything past
    POST_ALERT_MONITOR_MAX_AGE_SECONDS -- self-cleaning on read, same
    pattern as get_soft_fail_watch above."""
    queue = get_value(POST_ALERT_MONITOR_KEY) or []
    cutoff = time.time() - POST_ALERT_MONITOR_MAX_AGE_SECONDS
    fresh = [q for q in queue if q.get("alert_ts", 0) >= cutoff]
    if len(fresh) != len(queue):
        set_value(POST_ALERT_MONITOR_KEY, fresh)
    return fresh


def post_alert_monitor_remove(token: str):
    queue = get_value(POST_ALERT_MONITOR_KEY) or []
    remaining = [q for q in queue if q.get("token") != token]
    if len(remaining) != len(queue):
        set_value(POST_ALERT_MONITOR_KEY, remaining)


# --- Layer 12 -- Telegram caller-channel signals (Ali, Sept 28 2026) ---
# See layers/layer12_caller_channels.py's module docstring for the full
# picture: a token address mentioned in a configured caller channel gets
# recorded here, and scheduler._handle_scored rides it as a "Caller" tag on
# a real alert if that token also independently passes structural scoring
# within CALLER_SIGNAL_WINDOW_SECONDS of the mention. Never a detection
# mechanism on its own -- corroborating color only, same as Backing/Buzz.
CALLER_SIGNALS_KEY = "caller_signals"
CALLER_SIGNAL_MAX_AGE_SECONDS = 2 * 3600
CALLER_SIGNAL_MAX_ITEMS = 500
CALLER_UPDATE_OFFSET_KEY = "caller_update_offset"


def record_caller_signal(token: str, channel_name: str, ts: Optional[float] = None):
    """Records that `token` was mentioned in `channel_name` at `ts` (default
    now). Deliberately does NOT dedupe by token -- the same token called in
    two different channels, or called twice, is itself a real signal (see
    get_caller_signal, which returns the single most recent match, not a
    count) -- callers wanting "was this token called more than once" can
    read every match themselves via get_value(CALLER_SIGNALS_KEY)."""
    ts = ts if ts is not None else time.time()
    signals = get_value(CALLER_SIGNALS_KEY) or []
    signals.append({"token": token, "channel": channel_name, "ts": ts})
    cutoff = ts - CALLER_SIGNAL_MAX_AGE_SECONDS
    signals = [s for s in signals if s.get("ts", 0) >= cutoff][-CALLER_SIGNAL_MAX_ITEMS:]
    set_value(CALLER_SIGNALS_KEY, signals)


def get_caller_signal(token: str) -> Optional[dict]:
    """Returns the most recent still-live caller-channel mention of `token`
    (within CALLER_SIGNAL_MAX_AGE_SECONDS), or None if it was never
    mentioned or its mention has aged out."""
    signals = get_value(CALLER_SIGNALS_KEY) or []
    cutoff = time.time() - CALLER_SIGNAL_MAX_AGE_SECONDS
    matches = [s for s in signals if s.get("token") == token and s.get("ts", 0) >= cutoff]
    if not matches:
        return None
    return max(matches, key=lambda s: s.get("ts", 0))


def get_caller_update_offset() -> Optional[int]:
    """Telegram getUpdates cursor -- the next update_id to request, so a
    poll cycle never reprocesses updates Telegram already delivered."""
    return get_value(CALLER_UPDATE_OFFSET_KEY)


def set_caller_update_offset(offset: Optional[int]):
    if offset is not None:
        set_value(CALLER_UPDATE_OFFSET_KEY, offset)


# --- Layer 13: Fomo copy-trading/thesis dashboard feed (Ali, Sept 30 2026
# -- "alert should show in my dashboard not on telegram"). Same
# append-only, age+count-capped, newest-first pattern as CALLER_SIGNALS_KEY
# above, kept in its own key so a Layer 12 (Telegram caller) reader never
# picks these up and vice versa. dashboard.py reads this directly --
# nothing here sends a Telegram alert; see layers/layer13_fomo_copytrade.py's
# module docstring for the full "corroborating signal, not a trigger" design.
FOMO_SIGNALS_KEY = "fomo_signals"
FOMO_SIGNAL_MAX_AGE_SECONDS = 48 * 3600
FOMO_SIGNAL_MAX_ITEMS = 300

FOMO_CANDIDATES_KEY = "fomo_new_trader_candidates"
FOMO_CANDIDATE_MAX_AGE_SECONDS = 7 * 24 * 3600
FOMO_CANDIDATE_MAX_ITEMS = 100

FOMO_ALERTS_SINCE_KEY = "fomo_alerts_since_ts"


def record_fomo_signal(kind: str, trader: str, tier: str, token_symbol: str,
                        token_address: str, chain: str, detail: str,
                        score: Optional[int] = None, band: Optional[str] = None,
                        thesis_text: Optional[str] = None, thesis_link: Optional[str] = None,
                        pnl_24h: Optional[float] = None, pnl_7d: Optional[float] = None,
                        pnl_30d: Optional[float] = None, ts: Optional[float] = None,
                        signal_id: Optional[str] = None):
    """One dashboard-only Fomo signal: kind is "buy" or "thesis". Never
    sent to Telegram -- Ali's explicit call (Sept 30 2026). score/band are
    this coin's own Layer 0 structural score, run independently of the
    Fomo signal itself (see layers/layer13_fomo_copytrade.py) -- None
    means scoring hasn't completed or failed this cycle, not that the coin
    scored zero; dashboard.py must render that distinction, not collapse
    it to a number. pnl_24h/7d/30d (Ali, Sept 30 2026 ask) are the
    triggering TRADER's own track record at the moment this signal fired,
    from fomoapi.io's /v2/users/{handle} -- None means the lookup failed,
    never $0."""
    ts = ts if ts is not None else time.time()
    signals = get_value(FOMO_SIGNALS_KEY) or []
    if signal_id and any(x.get("signal_id") == signal_id for x in signals):
        return  # already recorded (overlapping cursor / both runners saw it)
    signals.append({
        "signal_id": signal_id,
        "kind": kind, "trader": trader, "tier": tier, "token_symbol": token_symbol,
        "token_address": token_address, "chain": chain, "detail": detail,
        "score": score, "band": band, "thesis_text": thesis_text, "thesis_link": thesis_link,
        "pnl_24h": pnl_24h, "pnl_7d": pnl_7d, "pnl_30d": pnl_30d,
        "ts": ts,
    })
    cutoff = ts - FOMO_SIGNAL_MAX_AGE_SECONDS
    signals = [s for s in signals if s.get("ts", 0) >= cutoff][-FOMO_SIGNAL_MAX_ITEMS:]
    set_value(FOMO_SIGNALS_KEY, signals)


def get_fomo_signal_feed(limit: int = 100) -> list:
    signals = get_value(FOMO_SIGNALS_KEY) or []
    cutoff = time.time() - FOMO_SIGNAL_MAX_AGE_SECONDS
    signals = [s for s in signals if s.get("ts", 0) >= cutoff]  # real-time re-filter, not just
    return list(reversed(signals))[:limit]  # write-time trim -- see get_caller_signal's own pattern


def record_fomo_candidate(handle: str, display_name: str, balance_usd: float,
                           pnl_usd: Optional[float] = None, volume_usd: Optional[float] = None,
                           pnl_24h: Optional[float] = None, pnl_7d: Optional[float] = None,
                           pnl_30d: Optional[float] = None, ts: Optional[float] = None,
                           **extra):
    """A trader NOT on Ali's roster who cleared the $5k balance threshold
    (Ali's locked number, Sept 30 2026) -- surfaced for Ali to approve
    adding to the roster, never auto-added (roster.py's own docstring:
    curation is Ali's judgment call, this codebase doesn't guess trust).
    Deduped by handle -- a repeat sighting refreshes the existing entry's
    numbers/ts rather than piling up duplicates. pnl_24h/7d/30d (Ali,
    Sept 30 2026 ask) supplement the leaderboard's single-window pnl_usd
    with the trader's full track record from fomoapi.io's /v2/users/{handle}."""
    ts = ts if ts is not None else time.time()
    candidates = get_value(FOMO_CANDIDATES_KEY) or []
    candidates = [c for c in candidates if c.get("handle") != handle]
    candidates.append({
        "handle": handle, "display_name": display_name, "balance_usd": balance_usd,
        "pnl_usd": pnl_usd, "volume_usd": volume_usd,
        "pnl_24h": pnl_24h, "pnl_7d": pnl_7d, "pnl_30d": pnl_30d, "ts": ts,
        **extra,  # insider/insider_detail/wallet/promoted/promotion_reason (Sept 30 2026)
    })
    cutoff = ts - FOMO_CANDIDATE_MAX_AGE_SECONDS
    candidates = [c for c in candidates if c.get("ts", 0) >= cutoff][-FOMO_CANDIDATE_MAX_ITEMS:]
    set_value(FOMO_CANDIDATES_KEY, candidates)


def get_fomo_candidates(limit: int = 50) -> list:
    candidates = get_value(FOMO_CANDIDATES_KEY) or []
    cutoff = time.time() - FOMO_CANDIDATE_MAX_AGE_SECONDS
    candidates = [c for c in candidates if c.get("ts", 0) >= cutoff]  # real-time re-filter
    return list(reversed(candidates))[:limit]  # newest first


def get_fomo_alerts_since() -> Optional[str]:
    """ISO timestamp cursor for GET /v2/alerts?since= -- so a poll cycle
    only asks fomoapi.io for events newer than the last one already
    processed, same purpose as get_caller_update_offset above."""
    return get_value(FOMO_ALERTS_SINCE_KEY)


def set_fomo_alerts_since(iso_ts: Optional[str]):
    if iso_ts:
        set_value(FOMO_ALERTS_SINCE_KEY, iso_ts)


# --- Auto-buy verdict per token (Sept 30 2026) ---
# Ali: "if we would have the funds and system would have been live would
# these trades on these alerts have been executed". Every Stage 1/Stage 2
# trigger decision on an alert-worthy token is recorded here -- fired or
# not, and the exact reason -- so the dashboard's Alerts tab can show the
# real answer per row instead of anyone having to reconstruct it.

def _autobuy_key(token: str) -> str:
    return f"autobuy_verdict:{token}"


def record_autobuy_verdict(token: str, chain: str, result: dict, ts: Optional[float] = None):
    if not token or not isinstance(result, dict):
        return
    buy = result.get("buy") or {}
    set_value(_autobuy_key(token), {
        "chain": chain, "stage": result.get("stage"), "fired": bool(result.get("fired")),
        "reason": result.get("reason"), "position_usd": result.get("position_usd"),
        "buy_ok": buy.get("ok") if buy else None, "buy_reason": buy.get("reason") if buy else None,
        "ts": ts if ts is not None else time.time(),
    })


def get_autobuy_verdicts(tokens: List[str]) -> dict:
    """{token: verdict_or_None} in one batched read."""
    tokens = [t for t in dict.fromkeys(tokens) if t]
    if not tokens:
        return {}
    raw = get_values([_autobuy_key(t) for t in tokens])
    return {t: raw.get(_autobuy_key(t)) for t in tokens}


# --- Layer 13 support (Sept 30 2026 roster-matching fix) ---
FOMO_HANDLE_MAP_KEY = "fomo_handle_map"
FOMO_PROMOTED_KEY = "fomo_promoted_traders"
FOMO_UNMATCHED_KEY = "fomo_unmatched_handles"
FOMO_ROSTER_BUYS_KEY = "fomo_roster_buys"
FOMO_LAST_RUN_KEY = "fomo_layer13_last_run"


def fomo_signal_ids() -> set:
    return {x.get("signal_id") for x in (get_value(FOMO_SIGNALS_KEY) or []) if x.get("signal_id")}


def get_fomo_handle_map() -> dict:
    """normalized fomoapi.io handle -> canonical roster name, learned from
    leaderboard rows whose displayName matched the roster."""
    v = get_value(FOMO_HANDLE_MAP_KEY)
    return v if isinstance(v, dict) else {}


def set_fomo_handle_map(mapping: dict):
    set_value(FOMO_HANDLE_MAP_KEY, mapping)


def get_fomo_promoted() -> dict:
    """normalized handle -> {handle, display_name, reason, ts} for
    new-trader candidates auto-promoted onto the tracked list."""
    v = get_value(FOMO_PROMOTED_KEY)
    return v if isinstance(v, dict) else {}


def set_fomo_promoted(promoted: dict):
    set_value(FOMO_PROMOTED_KEY, promoted)


def note_fomo_unmatched_handles(counts: dict, ts: Optional[float] = None):
    """Rolling tally of fomoapi.io traders whose buys/theses did NOT match
    the roster -- visible on the dashboard/diag so a name mismatch can't
    hide behind "zero roster buys" again."""
    ts = ts if ts is not None else time.time()
    cur = get_value(FOMO_UNMATCHED_KEY) or {}
    for handle, n in counts.items():
        e = cur.get(handle) or {"count": 0}
        cur[handle] = {"count": e["count"] + n, "last_ts": ts}
    if len(cur) > 300:
        cur = dict(sorted(cur.items(), key=lambda kv: kv[1].get("last_ts", 0))[-300:])
    set_value(FOMO_UNMATCHED_KEY, cur)


def get_fomo_unmatched_handles() -> dict:
    v = get_value(FOMO_UNMATCHED_KEY)
    return v if isinstance(v, dict) else {}


def record_fomo_roster_buy(chain: str, mint: str, trader: str, ts: Optional[float] = None,
                           window_seconds: int = 3600) -> Tuple[int, list]:
    """Records one roster trader's buy of (chain, mint); returns (distinct
    roster traders who bought it inside the window, their names)."""
    now = time.time()
    ts = ts if ts is not None else now
    data = get_value(FOMO_ROSTER_BUYS_KEY) or {}
    key = f"{chain}:{mint}"
    entries = [e for e in data.get(key, []) if e.get("ts", 0) >= now - window_seconds]
    if not any(e.get("trader") == trader for e in entries):
        entries.append({"trader": trader, "ts": ts})
    data[key] = entries
    data = {k: v for k, v in data.items() if v and max(e.get("ts", 0) for e in v) >= now - 6 * 3600}
    set_value(FOMO_ROSTER_BUYS_KEY, data)
    traders = sorted({e["trader"] for e in entries})
    return len(traders), traders


def set_fomo_last_run(info: dict):
    set_value(FOMO_LAST_RUN_KEY, {**info, "ts": time.time()})


def get_fomo_last_run() -> Optional[dict]:
    return get_value(FOMO_LAST_RUN_KEY)


# --- Runner heartbeats (Sept 30 2026) ---
# Every poll cycle (poll-fast / poll-slow on GitHub Actions, --poll-madeonsol
# on Ali's PC) stamps its completion here, so "is the PC Task Scheduler job
# actually running?" is a fact on the dashboard, not a question.
RUNNER_HEARTBEATS_KEY = "runner_heartbeats"


def record_runner_heartbeat(name: str, where: str = "", note: str = "", ts: Optional[float] = None):
    beats = get_value(RUNNER_HEARTBEATS_KEY) or {}
    beats[name] = {"ts": ts if ts is not None else time.time(), "where": where, "note": note}
    set_value(RUNNER_HEARTBEATS_KEY, beats)


def get_runner_heartbeats() -> dict:
    v = get_value(RUNNER_HEARTBEATS_KEY)
    return v if isinstance(v, dict) else {}


# --- Small TTL cache for paid third-party lookups (Sept 30 2026) ---
def cache_get(key: str, max_age_seconds: float):
    v = get_value(f"cache:{key}")
    if not isinstance(v, dict) or "ts" not in v:
        return None
    if time.time() - v["ts"] > max_age_seconds:
        return None
    return v.get("value")


def cache_set(key: str, value):
    set_value(f"cache:{key}", {"ts": time.time(), "value": value})


FOMO_CREDIT_STATE_KEY = "fomo_credit_state"


def get_fomo_credit_state() -> dict:
    v = get_value(FOMO_CREDIT_STATE_KEY)
    return v if isinstance(v, dict) else {}


def set_fomo_credit_state(v: dict):
    set_value(FOMO_CREDIT_STATE_KEY, v)

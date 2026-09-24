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

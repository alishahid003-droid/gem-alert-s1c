"""
Layer 0c -- StonkFun structural scoring + discovery.

Added Sept 22, 2026, after Ali flagged that two real runner coins (TACZ,
LEVERCAT) turned out to have launched on StonkFun, not pump.fun -- and
S1c's entire discovery pipeline (Layer 0/0b/1) is scoped to pump.fun via
MadeOnSol. This module closes that gap by polling StonkFun's own public
API directly.

StonkFun's API (https://www.stonkfun.xyz/api/public/v1) is confirmed
public, free, keyless: "no API key and nothing to sign up for," 300
req/min per IP (25/min for launch-quote endpoints, which this module
doesn't use). Verified directly against a live GET /tokens?sort=newest
request and a live GET /tokens/{mint} request (Sept 22, 2026) -- real
JSON, not docs taken on faith. No new account, no new secret needed --
matches Ali's standing "no RPC provider account" constraint even more
directly than MadeOnSol did.

HONEST SCOPE LIMITS -- read before assuming this layer is equivalent to
Layer 0/0b:

1. StonkFun's own API gives far fewer structural signals than MadeOnSol/
   Mobula do for pump.fun. Confirmed present: mint, symbol, name, creator
   wallet, market cap, peak market cap, liquidity, 24h volume, graduation
   progress, created/graduated timestamps. Confirmed ABSENT: holder
   concentration, mint/freeze authority state, bundler/sniper %, LP lock
   status -- the exact signals layer0_scoring.score_token() weighs most
   heavily. score_stonkfun_token() below only uses what StonkFun actually
   returns; it is NOT a drop-in replacement for layer0_scoring's score and
   its A/B/C/D bands should not be compared directly against that layer's.
   Mobula MIGHT fill some of this gap (it's chain-level, not platform-
   gated, so a StonkFun-launched SPL token could show up in a Mobula
   holder lookup same as any other mint) -- not wired here, flagged as the
   natural next step if this layer proves worth keeping.

2. StonkFun launches aren't always SOL-paired. LEVERCAT itself launched
   against "xSOL" (a leveraged wrapper, category "leverage"), and StonkFun
   also supports stock-paired launches per its own marketing. Discovery
   here is filtered to plain SOL/wSOL-quoted launches ONLY
   (STONKFUN_ALLOWED_QUOTE_SYMBOLS below) -- an exotic-quote-token launch
   is skipped entirely, not scored. Reason: swap_executor.py's buy paths
   assume a SOL-denominated route; buying into a leverage- or stock-paired
   pool is a structurally different trade (a second swap leg, a different
   risk/liquidation profile) this codebase has no logic for at all.
   Widening this later is a real, separate scope decision, not a bug fix.

3. StonkFun doesn't publish a deployer-reputation tier the way MadeOnSol's
   Deployer Hunter does. compute_stonkfun_deployer_tier() below is a
   deliberately conservative v1 built from StonkFun's own
   /launches?creator=<wallet> endpoint -- see that function's own
   docstring for exactly what it can and can't detect yet (it can flag an
   obvious serial-spammer wallet; it CANNOT yet detect "one prior genuine
   hit," because that needs a graduation/peak-mcap field this endpoint
   doesn't return). Do not treat this tier as equivalent in reliability to
   Layer 1's MadeOnSol-sourced one.

STATUS: discovery + scoring logic only, fixture-testable, no live network
call made from this build environment (StonkFun's domain is blocked from
both this cloud sandbox and the device's shell sandbox, same reachability
wall as MadeOnSol/Mobula/etc -- confirmed directly, even though no key is
needed here). NOT wired into poll-fast.yml yet -- see README for the
wiring step and current status.
"""
from dataclasses import dataclass
from typing import Optional

from config import CONFIG
from utils.http import get_json

# Discovery is filtered to these quote symbols only -- see scope limit #2.
STONKFUN_ALLOWED_QUOTE_SYMBOLS = {"SOL", "WSOL", "WRAPPED SOL"}

# A wallet with at least this many total StonkFun launches, none of which
# reached DEPLOYER_SPAMMER_MAX_MCAP, is flagged "spammer". Deliberately
# conservative -- see compute_stonkfun_deployer_tier's docstring for why
# this can't yet detect "elite"/"good" the way Layer 1 can.
DEPLOYER_SPAMMER_MIN_LAUNCHES = 5
DEPLOYER_SPAMMER_MAX_MCAP = 10_000


@dataclass
class StonkfunSignals:
    market_cap_usd: Optional[float] = None
    peak_market_cap_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume24h_usd: Optional[float] = None
    graduation_progress: Optional[float] = None  # 0-1
    status: Optional[str] = None  # "new" | "graduated" | ...


@dataclass
class ScoreResult:
    score: int
    band: str
    reasons: list


# Deliberately looser bands than layer0_scoring's -- this score is built
# from a thinner signal set (see scope limit #1), so treating it as
# equally discriminating would be dishonest. Used as a pre-filter (skip
# obvious junk), not a substitute for Layer 0/0b's structural rigor.
BANDS = {"A": 65, "B": 45, "C": 25}  # else D


def score_stonkfun_token(sig: StonkfunSignals) -> ScoreResult:
    reasons = []
    points = 0.0
    max_points = 0.0

    def add(weight, earned, reason=None):
        nonlocal points, max_points
        max_points += weight
        points += earned
        if reason:
            reasons.append(reason)

    # Liquidity depth (weight 35) -- the single most load-bearing signal
    # this thin dataset actually has.
    if sig.liquidity_usd is not None:
        w = 35
        earned = w * min(1.0, sig.liquidity_usd / 50_000)  # full credit at $50k+ liquidity
        add(w, earned, f"liquidity ${sig.liquidity_usd:,.0f}")
    else:
        add(35, 0, "liquidity unknown -- scored as worst case")

    # Volume-to-liquidity ratio as an organic-activity proxy (weight 25).
    if sig.volume24h_usd is not None and sig.liquidity_usd:
        w = 25
        ratio = sig.volume24h_usd / max(sig.liquidity_usd, 1.0)
        # sweet spot ~0.5-5x; too low = dead, too high = wash-trade/bot risk
        if 0.5 <= ratio <= 5.0:
            earned = w
        elif ratio < 0.5:
            earned = w * (ratio / 0.5)
        else:
            earned = w * max(0.0, 1.0 - (ratio - 5.0) / 15.0)
        add(w, earned, f"vol/liq ratio {ratio:.2f}")
    else:
        add(25, 12, "volume/liquidity ratio unknown -- scored neutral")

    # Graduation progress (weight 20) -- further along the bonding curve
    # is weak evidence of real organic demand, not proof.
    if sig.graduation_progress is not None:
        w = 20
        add(w, w * min(1.0, sig.graduation_progress), f"graduation progress {sig.graduation_progress*100:.0f}%")
    else:
        add(20, 10, "graduation progress unknown -- scored neutral")

    # Peak-vs-current market cap (weight 20) -- a token trading well below
    # its own peak is a soft dump/rug-adjacent flag, not a hard one.
    if sig.peak_market_cap_usd and sig.market_cap_usd is not None and sig.peak_market_cap_usd > 0:
        w = 20
        retained = min(1.0, sig.market_cap_usd / sig.peak_market_cap_usd)
        add(w, w * retained, f"retained {retained*100:.0f}% of peak mcap")
    else:
        add(20, 10, "peak/current mcap comparison unavailable -- scored neutral")

    score = round(100 * points / max_points) if max_points else 0
    if score >= BANDS["A"]:
        band = "A"
    elif score >= BANDS["B"]:
        band = "B"
    elif score >= BANDS["C"]:
        band = "C"
    else:
        band = "D"
    return ScoreResult(score=score, band=band, reasons=reasons)


def fetch_new_stonkfun_tokens(limit: int = 25) -> dict:
    """GET /tokens?sort=newest. No API key -- StonkFun's endpoint is
    genuinely keyless (see module docstring)."""
    result = get_json(f"{CONFIG.stonkfun_base_url}/tokens",
                       params={"sort": "newest", "limit": limit})
    return {"ok": result["ok"], "raw": result}


def parse_stonkfun_tokens(payload: dict) -> list:
    """payload is the JSON body of a /tokens response. Filters to
    SOL/wSOL-quoted launches only (scope limit #2) and returns normalized
    dicts ready for score_stonkfun_token()."""
    tokens = payload.get("data") or payload.get("tokens") or []
    out = []
    for t in tokens:
        quote_symbol = ((t.get("quote") or {}).get("symbol") or "").upper()
        if quote_symbol not in STONKFUN_ALLOWED_QUOTE_SYMBOLS:
            continue
        market = t.get("market") or {}
        sig = StonkfunSignals(
            market_cap_usd=market.get("marketCapUsd"),
            peak_market_cap_usd=market.get("peakMarketCapUsd"),
            liquidity_usd=market.get("liquidityUsd"),
            volume24h_usd=market.get("volume24hUsd"),
            graduation_progress=t.get("graduationProgress"),
            status=t.get("status"),
        )
        result = score_stonkfun_token(sig)
        out.append({
            "mint": t.get("mint"),
            "symbol": t.get("symbol"),
            "name": t.get("name"),
            "created_at": t.get("createdAt"),
            "score": result.score,
            "band": result.band,
            "reasons": result.reasons,
        })
    return out


def poll_layer0c(limit: int = 25) -> dict:
    fetched = fetch_new_stonkfun_tokens(limit)
    if not fetched["ok"]:
        return {"ok": False, "reason": f"status {fetched['raw']['status_code']}", "tokens": []}
    body = fetched["raw"].get("json")
    if body is None:
        return {"ok": False, "reason": "non-JSON response", "tokens": []}
    return {"ok": True, "tokens": parse_stonkfun_tokens(body)}


def fetch_stonkfun_launches_by_creator(creator_wallet: str, limit: int = 50) -> dict:
    """GET /launches?creator=<wallet> -- this wallet's StonkFun launch
    history, used to build a heuristic deployer signal (scope limit #3)."""
    result = get_json(f"{CONFIG.stonkfun_base_url}/launches",
                       params={"creator": creator_wallet, "limit": limit})
    return {"ok": result["ok"], "raw": result}


def compute_stonkfun_deployer_tier_from_payload(payload: dict) -> dict:
    """Pure parse -- payload is the JSON body of a /launches response.
    Split out from compute_stonkfun_deployer_tier so this logic is
    fixture-testable with no live network call, same pattern as
    layer1_deployer's fetch/parse split.

    Heuristic only, deliberately conservative -- see scope limit #3 in the
    module docstring. v1 uses ONLY fields StonkFun's /launches endpoint is
    confirmed to return: mint, name, symbol, creator, quote, launchpad,
    mode, transferFee, startMarketCapUsd, targetMarketCapUsd, createdAt.
    No graduation status or peak/live market cap comes back on this
    endpoint -- getting that per past launch would mean one
    GET /tokens/{mint} call per historical launch, an N+1 pattern this
    module deliberately does NOT do inline (same reasoning as Layer 8's
    queue-driven, capped deep-scoring elsewhere in this codebase, not
    duplicated here). So this tier is currently launch-count-based only:
    it can flag an obvious serial-spammer wallet, but it CANNOT yet detect
    a wallet with one prior genuine hit -- that needs the graduation/peak-
    mcap data above, which is real future work, not silently faked with
    fields that may not exist in the actual response."""
    launches = payload.get("data") or payload.get("launches") or []
    total = len(launches)
    if total == 0:
        return {"tier": "unknown", "reason": "no launch history found", "launch_count": 0}

    max_start_mcap = max((l.get("startMarketCapUsd") or 0) for l in launches)

    if total >= DEPLOYER_SPAMMER_MIN_LAUNCHES and max_start_mcap < DEPLOYER_SPAMMER_MAX_MCAP:
        tier = "spammer"
    else:
        # Not enough confirmed signal to call elite/good yet -- see docstring.
        tier = "neutral"

    return {"tier": tier, "launch_count": total, "max_start_mcap_usd": max_start_mcap}


def compute_stonkfun_deployer_tier(creator_wallet: str) -> dict:
    """Fetch + parse. See compute_stonkfun_deployer_tier_from_payload for
    what this tier can and can't detect."""
    fetched = fetch_stonkfun_launches_by_creator(creator_wallet)
    if not fetched["ok"]:
        return {"tier": "unknown", "reason": f"status {fetched['raw']['status_code']}"}
    body = fetched["raw"].get("json")
    if body is None:
        return {"tier": "unknown", "reason": "non-JSON response"}
    return compute_stonkfun_deployer_tier_from_payload(body)


# --- Momentum / cross-quote gem detection (Sept 23, 2026 addition) ---
#
# Ali's explicit ask after reviewing LEVERCAT: don't let the SOL-only quote
# filter above (scope limit #2) cause this system to miss a token that
# launched recently and moved enormously, just because it can't be
# auto-bought yet. Alerting needs no buy route -- only execute_buy_* needs
# one. So this path runs INDEPENDENTLY of STONKFUN_ALLOWED_QUOTE_SYMBOLS and
# scores momentum (launch-to-peak market cap multiple) for ANY quote type,
# tagging whether swap_executor could actually route a buy for it.
#
# Ground truth used to pick the threshold: LEVERCAT's real StonkFun record
# (fetched Sept 22-23, 2026 via GET /tokens/{mint}, mint
# AGi2s9zPRPHs3zEDPhPTroumTEXK5ufymYSfEFndCSSW): startMarketCapUsd $2,948.75
# -> peakMarketCapUsd $8,092,369.74 -- a ~2,744x launch-to-peak move,
# graduating in 13m35s. MOMENTUM_GEM_MIN_MULTIPLE below (20x) is
# deliberately far below that -- this is one confirmed real data point, not
# a tuned threshold, and it is set low/inclusive on purpose rather than
# reverse-engineered to exactly hit 2,744x. Expect false positives; that is
# the accepted trade-off for an alert-only, no-buy-route layer.
#
# HONEST TIMING CAVEAT: this only fires on FAST poll cycles (every ~10 min).
# LEVERCAT went from launch to full graduation in under 14 minutes -- a move
# that fast may already be near its local peak by the time a 10-minute cycle
# even sees it as "new". This layer is realistic for catches that play out
# over tens of minutes to hours, not ones that finish inside one poll
# interval. Said plainly in the alert tag (see scheduler.py), not hidden.

MOMENTUM_GEM_MIN_MULTIPLE = 20  # peak mcap >= 20x start mcap
MOMENTUM_LOOKBACK_HOURS = 48    # only consider launches within this window
MOMENTUM_MAX_DEEP_LOOKUPS_PER_CYCLE = 15  # cap per-mint detail calls/cycle


def fetch_stonkfun_token_detail(mint: str) -> dict:
    """GET /tokens/{mint} -- returns BOTH current/peak market data AND the
    embedded launch record (startMarketCapUsd), confirmed via a live fetch
    of LEVERCAT's real record (see module docstring addition above). One
    call gets everything this momentum check needs -- no second call to
    /launches required."""
    result = get_json(f"{CONFIG.stonkfun_base_url}/tokens/{mint}")
    return {"ok": result["ok"], "raw": result}


def parse_stonkfun_token_detail(payload: dict) -> Optional[dict]:
    """payload is the JSON body of a /tokens/{mint} response. Returns None
    if the response doesn't have the shape this needs (missing token/launch
    keys) rather than raising -- a malformed response should skip this one
    candidate, not crash the poll cycle."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    token = data.get("token") if data else None
    launch = data.get("launch") if data else None
    if not token or not launch:
        return None
    market = token.get("market") or {}
    quote_symbol = ((token.get("quote") or {}).get("symbol") or "").upper()
    return {
        "mint": token.get("mint"),
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "quote_symbol": quote_symbol,
        "executable": quote_symbol in STONKFUN_ALLOWED_QUOTE_SYMBOLS,
        "created_at": token.get("createdAt"),
        "creator": launch.get("creator"),
        "start_mcap_usd": launch.get("startMarketCapUsd"),
        "current_mcap_usd": market.get("marketCapUsd"),
        "peak_mcap_usd": market.get("peakMarketCapUsd"),
        "liquidity_usd": market.get("liquidityUsd"),
        "status": token.get("status"),
    }


def compute_momentum(detail: dict) -> Optional[dict]:
    """detail is parse_stonkfun_token_detail()'s output. Returns the
    launch-to-peak and launch-to-current multiples, or None if start_mcap
    is missing/zero (can't compute a multiple off nothing)."""
    start = detail.get("start_mcap_usd")
    peak = detail.get("peak_mcap_usd")
    current = detail.get("current_mcap_usd")
    if not start or start <= 0:
        return None
    peak_multiple = (peak / start) if peak is not None else None
    current_multiple = (current / start) if current is not None else None
    is_gem = peak_multiple is not None and peak_multiple >= MOMENTUM_GEM_MIN_MULTIPLE
    return {
        "start_mcap_usd": start,
        "peak_mcap_usd": peak,
        "current_mcap_usd": current,
        "peak_multiple": peak_multiple,
        "current_multiple": current_multiple,
        "is_momentum_gem": is_gem,
    }


def _within_lookback(created_at, now_utc=None) -> bool:
    """created_at is an ISO 8601 string like StonkFun returns. Returns False
    (safe default -- skip it) on anything unparseable rather than raising."""
    if not created_at:
        return False
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = now_utc or datetime.now(timezone.utc)
        age_hours = (now - ts).total_seconds() / 3600.0
        return 0 <= age_hours <= MOMENTUM_LOOKBACK_HOURS
    except (ValueError, TypeError):
        return False


def poll_layer0c_momentum(limit: int = 25, max_deep_lookups: int = MOMENTUM_MAX_DEEP_LOOKUPS_PER_CYCLE,
                           already_checked=None) -> dict:
    """Cross-quote-type momentum scan -- runs INDEPENDENTLY of
    parse_stonkfun_tokens()'s SOL-only filter (see module docstring
    addition above). Two-stage, cost-capped like Layer 8's deep-scoring:

    1. Cheap: GET /tokens?sort=newest once -- gives createdAt for every
       candidate at no extra cost.
    2. Capped: for up to max_deep_lookups of the newest, not-already-checked
       candidates within MOMENTUM_LOOKBACK_HOURS, fetch the per-mint detail
       (fetch_stonkfun_token_detail) to get startMarketCapUsd and compute
       the launch-to-peak multiple.

    already_checked: mint strings to skip (the caller's dedup set) -- avoids
    re-spending a deep lookup on a mint already scored this run/recent runs.
    Returns {"ok": bool, "gems": [...], "checked": [mint, ...]}."""
    fetched = fetch_new_stonkfun_tokens(limit)
    if not fetched["ok"]:
        return {"ok": False, "reason": f"status {fetched['raw']['status_code']}", "gems": [], "checked": []}
    body = fetched["raw"].get("json")
    if body is None:
        return {"ok": False, "reason": "non-JSON response", "gems": [], "checked": []}

    already_checked = already_checked or set()
    tokens = body.get("data") or body.get("tokens") or []
    candidates = [
        t for t in tokens
        if t.get("mint") and t.get("mint") not in already_checked and _within_lookback(t.get("createdAt"))
    ][:max_deep_lookups]

    gems = []
    checked = []
    for t in candidates:
        mint = t["mint"]
        checked.append(mint)
        detail_fetch = fetch_stonkfun_token_detail(mint)
        if not detail_fetch["ok"]:
            continue
        detail_body = detail_fetch["raw"].get("json")
        if detail_body is None:
            continue
        detail = parse_stonkfun_token_detail(detail_body)
        if detail is None:
            continue
        momentum = compute_momentum(detail)
        if momentum is None or not momentum["is_momentum_gem"]:
            continue
        gems.append({**detail, **momentum})

    return {"ok": True, "gems": gems, "checked": checked}


# --- Proactive snipe candidate check (Sept 23, 2026 addition) ---
#
# Ali's direct pushback on the momentum layer above: it's reactive -- it
# only fires after a coin has ALREADY moved 20x+ (MOMENTUM_GEM_MIN_MULTIPLE),
# by which point a fast mover may be near its local top. He wants a
# genuinely earlier catch: buy while a launch is still ramping, not after
# it's already run.
#
# What this function changes vs. compute_momentum() above:
#   1. Uses CURRENT mcap vs start, never peak. Peak includes information
#      that, in a real live poll, would not exist yet at decision time --
#      scoring against peak here would be look-ahead bias, quietly making
#      this look better in hindsight than it could ever perform live.
#   2. A much LOWER trigger multiple (SNIPE_TRIGGER_MULTIPLE, 3x) than the
#      alert-only layer's 20x -- the whole point of "proactive" is acting
#      on a smaller, earlier signal, accepting more false positives in
#      exchange for not missing the front of a real move.
#   3. Adds a hard deployer-spammer reject (compute_stonkfun_deployer_tier)
#      and a minimum liquidity floor -- a 3x move on $50 of liquidity is
#      noise, not a signal; both are cheap, real filters against the most
#      obvious junk, not a claim that the survivors are winners.
#
# HONEST LIMIT, stated plainly: no combination of these filters gives this
# system predictive edge over the other bots and traders watching the same
# public data. This buys EARLIER when it buys at all -- it does not buy
# ONLY winners. It still needs to be run on a poll loop fast enough to see
# a launch within its first few minutes (see worker_stonkfun_snipe.py --
# GitHub Actions' cron granularity cannot do this; this needs a
# continuously-running process).

SNIPE_TRIGGER_MULTIPLE = 3       # current mcap >= 3x start mcap -- deliberately low/early, unvalidated
SNIPE_MIN_LIQUIDITY_USD = 2_000  # below this, a "3x" is noise on a near-empty pool


def is_snipe_candidate(detail: dict, deployer_tier_result: Optional[dict] = None) -> dict:
    """detail is parse_stonkfun_token_detail()'s output. deployer_tier_result
    is compute_stonkfun_deployer_tier_from_payload()'s output for this
    token's creator wallet (pass None if not fetched -- treated as unknown,
    not as a reject; a spammer-tier deployer is the only hard reject here).
    Returns {"candidate": bool, "reasons": [...], "current_multiple": float|None}."""
    reasons = []
    start = detail.get("start_mcap_usd")
    current = detail.get("current_mcap_usd")
    liquidity = detail.get("liquidity_usd")

    if not start or start <= 0:
        return {"candidate": False, "reasons": ["no start market cap on record"], "current_multiple": None}

    current_multiple = (current / start) if current is not None else None

    if deployer_tier_result and deployer_tier_result.get("tier") == "spammer":
        reasons.append(f"rejected: deployer flagged spammer ({deployer_tier_result.get('launch_count')} "
                        f"low-mcap launches on record)")
        return {"candidate": False, "reasons": reasons, "current_multiple": current_multiple}

    if liquidity is None or liquidity < SNIPE_MIN_LIQUIDITY_USD:
        reasons.append(f"rejected: liquidity ${liquidity if liquidity is not None else 0:,.0f} below "
                        f"${SNIPE_MIN_LIQUIDITY_USD:,.0f} floor")
        return {"candidate": False, "reasons": reasons, "current_multiple": current_multiple}

    if current_multiple is None or current_multiple < SNIPE_TRIGGER_MULTIPLE:
        reasons.append(f"below snipe trigger ({current_multiple if current_multiple is not None else 0:.1f}x "
                        f"< {SNIPE_TRIGGER_MULTIPLE}x)")
        return {"candidate": False, "reasons": reasons, "current_multiple": current_multiple}

    reasons.append(f"{current_multiple:.1f}x current vs start mcap, liquidity ${liquidity:,.0f}, "
                    f"deployer tier {(deployer_tier_result or {}).get('tier', 'unknown')}")
    return {"candidate": True, "reasons": reasons, "current_multiple": current_multiple}

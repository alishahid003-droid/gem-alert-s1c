"""
Layer 3 -- Genuine-backing check (Adanos Reddit Crypto Sentiment API).

SWAPPED FROM LunarCrush (Sept 7, 2026): LunarCrush's real price is $90/month
(Ali's earlier $24/mo figure was a mis-statement, confirmed directly against
LunarCrush's own pricing page) -- too much for a $0-target build. Adanos
(adanos.org) has a real free tier with actual API access instead.

THIS IS NOT A DROP-IN REPLACEMENT -- built against Adanos's actual documented
API (api.adanos.org/docs, api.adanos.org/openapi.reddit-crypto.yaml, and
adanos.org/pricing + adanos.org/reddit-crypto-sentiment), not assumed to
share LunarCrush's shape. Three honest findings from reading those docs that
change this layer's real scope, flagged here rather than silently designed
around:

1. CRYPTO COVERAGE IS REDDIT-ONLY. Adanos sells five sentiment products
   (Reddit, X/Twitter, News, Polymarket -- plus a Reddit-specific crypto
   product), but only ONE of them, Reddit Crypto Sentiment
   (/reddit/crypto/v1/*), actually covers cryptocurrencies. X/Twitter, News,
   and Polymarket sentiment on Adanos are stock/ETF-only products with no
   crypto equivalent (confirmed via adanos.org's own product list). So
   despite the original ask describing this as multi-source (X/Twitter via
   Grok, Reddit, financial news, Polymarket, crypto communities), what
   Adanos can actually deliver for a memecoin is Reddit sentiment alone --
   a real scope reduction, not a cosmetic one.
2. NO AI SPIKE EXPLANATION FOR CRYPTO. The stock product has a
   `/stock/{ticker}/explain` endpoint returning an AI-generated explanation
   of what's driving a spike; Adanos's crypto product page and OpenAPI spec
   confirm there is no equivalent `/token/{symbol}/explain` for crypto.
   What IS available and used here instead: `top_mentions` (highest-
   engagement Reddit posts/comments, as evidence a human can read) and
   `top_subreddits` -- real signal, just not an AI-authored summary.
3. SYMBOL-KEYED, NOT CONTRACT-ADDRESS-KEYED -- a real collision risk.
   Adanos's crypto endpoint takes a ticker symbol (`/token/{symbol}`), same
   as LunarCrush's topic endpoint before it, with no way to pass a contract
   address to disambiguate. This project's own pattern data (section 1,
   finding #8) already established that ticker names are squatted across
   unrelated chains/projects ("Shroom"/"Mars" examples) -- so a `$PONS`-
   style symbol lookup on Adanos could genuinely return Reddit sentiment for
   a different, unrelated coin sharing that ticker. Handled by labeling
   every Layer 3 result "symbol-matched, not contract-verified" (see
   check_backing_spike) rather than presenting it as verified per-token
   data, and by treating the `/search` endpoint's `name`/`aliases`/
   `market_cap_rank` fields as a manual cross-check a human can use, not an
   automated filter -- fail closed on ambiguity, don't guess it away.

CONFIRMED TRADE-OFF (per Ali, matches Adanos's own product page's exact
words: "Data refreshes hourly"): this data updates hourly, not real-time.
Fine for spike-detection (this layer's actual job), but nothing here should
assume sub-minute freshness -- a token that flips from quiet to spiking
Reddit attention may take up to ~60 minutes to show up here.

RATE-LIMIT REALITY THAT SHAPES THIS LAYER'S DESIGN: Adanos's free tier is
250 requests/month total (confirmed at adanos.org/pricing) -- roughly
8/day, nowhere near enough to poll every token Layer 1/0b discovers. So
Layer 3 is NOT meant to scan broadly; it's meant to be called selectively,
by whatever wires it into the live loop later, against a short list of
tokens that already look interesting from other layers (convergence,
momentum override, held positions) -- see MONTHLY_QUOTA_TOTAL below. This
module tracks Adanos's own `X-RateLimit-Remaining-Monthly` response header
(state.record_adanos_quota / get_adanos_quota) so a call can be skipped
pre-emptively once the quota's known to be exhausted, rather than only
finding out via a 429 after spending it.

Field names below are as documented in Adanos's OpenAPI spec, read through
a doc-summarizing fetch tool rather than raw JSON -- same caveat this
project already applies to Mobula's unconfirmed multi-wallet shape
(layers/wallet_balance.py): parsed defensively, and this layer is NOT yet
wired into scheduler.py's live loop (unchanged status from before this
swap -- still queued behind Layers 3/7 wiring per Ali's existing hold), so
the first live call against Ali's real key is still what actually confirms
this.

HARD RULE FROM THE SPEC, UNCHANGED, enforced in code not just a comment: a
spike NEVER auto-tags "verified real" on its own. CASHCAT (real backing,
winner) and MEME (surface-only AMC reference, lost a tracked trader money)
produce the SAME spike shape -- only an explicit, separately-confirmed
backing signal can upgrade a spike to "verified real". Absent that, every
spike surfaces as "needs-verification", full stop.
"""
from dataclasses import dataclass
from typing import Optional

from config import CONFIG
from utils.http import get_json
import state

MONTHLY_QUOTA_TOTAL = 250  # Adanos free tier -- see module docstring

SPIKE_VOLUME_MULTIPLIER = 3.0   # current mentions vs baseline mentions
SPIKE_MIN_ABSOLUTE_VOLUME = 50  # ignore noise on tokens with near-zero baseline


@dataclass
class BackingResult:
    spike_detected: bool
    tag: str  # "none" | "needs-verification" | "verified-real"
    reason: str = ""


def _quota_exhausted() -> bool:
    """Fails OPEN (allows the call) if the quota state is unknown -- the
    first-ever call, or Upstash being unavailable -- since a live 429 is
    still a safe, cheap way to find out. Fails CLOSED (skips the call)
    only once a real prior response has told us the quota is actually at
    zero, so a quiet month doesn't get artificially capped early."""
    q = state.get_adanos_quota()
    if not q or q.get("remaining") is None:
        return False
    return q["remaining"] <= 0


def _record_quota_from_headers(headers: dict):
    """Adanos documents X-RateLimit-Remaining-Monthly on every response --
    recorded here so the next call can check state.get_adanos_quota()
    instead of guessing. Silently no-ops if the header's missing or
    malformed rather than letting a header-parsing issue crash the caller."""
    if not headers:
        return
    remaining = headers.get("X-RateLimit-Remaining-Monthly") or headers.get("x-ratelimit-remaining-monthly")
    if remaining is None:
        return
    try:
        state.record_adanos_quota(int(remaining))
    except (TypeError, ValueError):
        pass


def fetch_reddit_crypto_token(symbol: str, days: Optional[int] = None) -> dict:
    if not CONFIG.adanos_api_key:
        return {"ok": False, "reason": "ADANOS_API_KEY not configured"}
    if _quota_exhausted():
        return {"ok": False, "reason": "Adanos monthly quota exhausted (250/month, free tier) -- "
                                        "skipped without calling, see state.get_adanos_quota()"}
    headers = {"X-API-Key": CONFIG.adanos_api_key}
    params = {"days": days} if days else {}
    result = get_json(f"{CONFIG.adanos_base_url}/reddit/crypto/v1/token/{symbol}",
                       headers=headers, params=params)
    _record_quota_from_headers(result.get("headers") or {})
    return {"ok": result["ok"], "raw": result}


def parse_reddit_crypto_token(payload: dict) -> dict:
    """payload is the JSON body of a GET /reddit/crypto/v1/token/{symbol}
    response. See module docstring for the schema-confidence caveat."""
    data = payload or {}
    if data.get("found") is False:
        return {"found": False}
    daily = data.get("daily_trend") or data.get("trend_history") or []
    return {
        "found": True,
        "buzz_score": data.get("buzz_score"),
        "sentiment_score": data.get("sentiment_score"),
        "bullish_pct": data.get("bullish_pct"),
        "bearish_pct": data.get("bearish_pct"),
        "mentions": data.get("mentions"),
        "unique_posts": data.get("unique_posts"),
        "subreddit_count": data.get("subreddit_count"),
        "trend": data.get("trend"),
        "top_subreddits": data.get("top_subreddits") or [],
        "top_mentions": data.get("top_mentions") or [],  # evidence, NOT an AI explanation -- see docstring point 2
        "daily_series": daily,
    }


def baseline_from_daily_series(daily_series: list, metric: str = "mentions", exclude_latest: bool = True) -> Optional[float]:
    """Adanos's /token/{symbol} response already carries its own historical
    daily breakdown (daily_trend) -- unlike LunarCrush's topic endpoint,
    which only gave a current snapshot and needed our own state-tracked
    history to compute a baseline. Using Adanos's own history means Layer 3
    can compute a same-call baseline even on the very first check of a
    token (no cold-start problem the way Layer 8's MC history had one), at
    the cost of one call instead of zero. Averages every day except the
    most recent (which is what current_volume in detect_spike represents);
    returns None if there's nothing to average, which detect_spike already
    treats as a cold start."""
    points = [d.get(metric) for d in (daily_series or [])
              if isinstance(d, dict) and d.get(metric) is not None]
    if exclude_latest and points:
        points = points[:-1]
    if not points:
        return None
    return sum(points) / len(points)


def detect_spike(current_volume: Optional[float], baseline_volume: Optional[float]) -> bool:
    if current_volume is None:
        return False
    if current_volume < SPIKE_MIN_ABSOLUTE_VOLUME:
        return False
    if not baseline_volume or baseline_volume <= 0:
        # No baseline yet (cold start) -- an absolute-volume-only heuristic,
        # deliberately conservative (fail closed rather than spike on noise).
        return current_volume >= SPIKE_MIN_ABSOLUTE_VOLUME * SPIKE_VOLUME_MULTIPLIER
    return current_volume >= baseline_volume * SPIKE_VOLUME_MULTIPLIER


def classify_backing(spike_detected: bool, externally_confirmed_real: bool = False) -> BackingResult:
    """externally_confirmed_real must come from something OTHER than the
    spike itself (a manual Ali confirmation, a curated real-backing list,
    a verified official announcement) -- never derived from Adanos data
    alone. Defaults to False, which is the safe default per spec."""
    if not spike_detected:
        return BackingResult(spike_detected=False, tag="none", reason="no mention/sentiment spike detected")
    if externally_confirmed_real:
        return BackingResult(spike_detected=True, tag="verified-real",
                              reason="spike detected AND independently confirmed as genuine backing")
    return BackingResult(spike_detected=True, tag="needs-verification",
                          reason="spike detected but backing not independently confirmed -- "
                                 "same shape as both CASHCAT (real) and MEME (surface-only), do not assume")


def check_backing_spike(symbol: str, days: int = 7, externally_confirmed_real: bool = False) -> dict:
    """One-call convenience tying fetch -> parse -> baseline -> detect_spike
    -> classify_backing together for a single symbol. NOT called from
    scheduler.py yet -- Layer 3 stays unwired from the live poll loop, same
    status as before this swap (queued behind Layers 3/7 wiring, per Ali's
    existing hold) -- this just gives whoever wires it later one function to
    call instead of re-assembling the pieces, and bakes in the symbol-
    collision caveat (point 3 above) directly into the result label."""
    fetched = fetch_reddit_crypto_token(symbol, days=days)
    if not fetched["ok"]:
        return {"ok": False, "reason": fetched.get("reason", "fetch failed"), "result": None, "parsed": None}
    body = fetched["raw"].get("json")
    if body is None:
        return {"ok": False, "reason": f"non-JSON response, status {fetched['raw']['status_code']}",
                 "result": None, "parsed": None}
    parsed = parse_reddit_crypto_token(body)
    if not parsed.get("found"):
        return {"ok": True, "result": BackingResult(spike_detected=False, tag="none",
                                                       reason="no Reddit crypto data for this symbol yet"),
                "parsed": parsed, "label": f"{symbol}: symbol-matched, not contract-verified"}
    baseline = baseline_from_daily_series(parsed["daily_series"])
    spike = detect_spike(parsed.get("mentions"), baseline)
    result = classify_backing(spike, externally_confirmed_real)
    return {"ok": True, "result": result, "parsed": parsed,
            "label": f"{symbol}: symbol-matched, not contract-verified"}

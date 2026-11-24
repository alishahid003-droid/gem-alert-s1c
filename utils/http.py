"""Thin HTTP wrapper: retries transient failures, never raises on 4xx from a
data source (a layer should degrade, not crash the whole poll cycle), and
makes every call diagnosable when something is actually blocked at the
network level (as opposed to the API just saying "no results")."""
import threading
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Shared, thread-safe connection pool (Sept 30 2026 -- Ali's real dashboard
# log showed a single /api/data build_data() call taking 12-26 SECONDS
# total, even after the Upstash-pipelining fix collapsed dozens of position
# reads into one call. Root cause: every call here used the bare
# `requests.request(...)` module function, which opens a brand-new TCP+TLS
# connection for every single HTTP call, with no keep-alive reuse -- from
# Ali's real Pakistan connection to Upstash/DexScreener/fomoapi.io's
# servers, each fresh handshake alone can cost 500ms-1.5s+, and build_data()
# makes 8-10+ such calls per request. A shared requests.Session() with
# urllib3's connection pooling reuses the underlying TCP+TLS connection
# across calls to the same host, cutting that per-call handshake cost to
# near zero after the first call. Session objects use a thread-local-free,
# lock-protected connection pool internally and are the documented,
# standard way to share one across threads (dashboard.py's ThreadPoolExecutor
# for concurrent DexScreener price lookups is exactly that case) -- the
# extra Lock here only guards the lazy one-time construction, not each call.
_session = None
_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = requests.Session()
                # FIXED 14 Nov 2026 (Ali, real incident): requests' own default
                # User-Agent ("python-requests/x.x.x") gets a hard 403 Forbidden
                # from Robinhood Chain's RPC -- confirmed live, on Ali's own
                # machine, that the SAME request with a curl or browser
                # User-Agent gets a clean 200 with the correct chainId. This was
                # silently indistinguishable from real rate-limiting/downtime in
                # every error this session saw ("all pool endpoints failed"),
                # because manual debugging all day used curl, which was never
                # affected. A real browser UA here costs nothing and fixes this
                # for every call through this shared session -- every chain's
                # RPC, DexScreener, GoPlus, MadeOnSol, Mobula, all of it.
                _session.headers.update({
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/131.0.0.0 Safari/537.36"),
                })
    return _session


class ApiUnreachable(Exception):
    """Raised only for genuine network-level failures (DNS, timeout, connection
    refused/blocked) -- NOT for ordinary HTTP error status codes."""


# 429-aware retry, added Sept 25 2026 per Ali's "polling calls should be well
# calculated and managed covering scanning along with execution side without
# any failure" instruction. Real gap this closes: the @retry decorator below
# only catches ConnectionError/Timeout -- a 429 response comes back as a
# normal (non-exception) Response object with status_code==429, so it was
# NEVER retried before this, at any call site, on either the scanning side
# (MadeOnSol/DexScreener in layers/) or the execution side (DexScreener in
# swap_executor.py's _rhc_native_price_usd).
#
# Deliberately conservative about WHEN to retry: only when the response
# carries a real `Retry-After` header, which is the API itself telling us
# how long a SHORT-lived throttle lasts (DexScreener/GoPlus per-minute style
# limits are the real target here). MadeOnSol's own 429 body (confirmed live
# Sept 25 2026, see backtest.py's module docstring) carries a `resets_at`
# field showing the reset is hours away (a DAILY quota, not a per-minute
# one) and does NOT set Retry-After -- so that case correctly falls through
# untouched and is left for the caller to handle exactly as before (MadeOnSol
# calls are already gated by state.py's MADEONSOL_DAILY_BUDGET on the
# scanning side; retrying a daily-quota 429 a few seconds later would just
# waste a call). No Retry-After header -> no retry, same behavior as before
# this existed.
_MAX_429_RETRIES = 2
_MAX_RETRY_AFTER_SECONDS = 30.0  # cap so a misbehaving/huge Retry-After can't stall a poll cycle


def _retry_after_seconds(resp):
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None  # Retry-After can also be an HTTP-date; not handled here, treated as "don't retry"
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


# FIXED 24 Nov 2026 (Ali: "fix the poll-fast hang right now" -- real incident,
# local_runner.log: poll-fast ran 22335s/6.2 HOURS before exiting, when its own
# subprocess timeout is 25 minutes). Root cause: `timeout=20` as a bare number
# is passed straight through to requests/urllib3 as a single socket timeout
# that only reliably bounds the CONNECT phase; once the TCP connection is
# established and bytes start trickling in (a slow/flaky link -- Ali's own
# connection out of Pakistan to GoPlus's servers here), a chunked response
# that stalls mid-stream inside `.content`/`.json()` is NOT guaranteed to be
# cut off by that single value, because urllib3's read-timeout semantics
# differ for a single float vs. an explicit (connect, read) tuple. Confirmed:
# the hung run's traceback (logs/local_poll-fast.log, run starting
# 2026-11-24 15:58:39) shows this EXACT call -- fetch_goplus_security() ->
# get_json() -> get() -> here -- finally raising
# `ChunkedEncodingError: Response ended prematurely` 6.2 hours later, which is
# not ConnectionError/Timeout so the @retry decorator below never even saw
# it, and it is not a 429 so the loop above it never ran either. The 25-minute
# subprocess timeout in local_runner.py did NOT fire because the *process*
# never hung at the OS level -- a single `requests` call inside it did, for
# hours, with no Python-level deadline enforcing a cutover. Passing an
# explicit (connect_timeout, read_timeout) tuple instead of a bare number
# makes urllib3 enforce a hard per-chunk read deadline, so a stalled stream
# now raises requests.exceptions.ReadTimeout (which IS caught below and
# converted to ApiUnreachable, and IS retried by the @retry decorator on
# get()/post()) within read_timeout seconds of the last received byte,
# instead of hanging indefinitely. Values: connect_timeout stays at the
# existing 20s (no behavior change for a slow-to-connect host); read_timeout
# is a new, separate 20s cap on silence between chunks once a response has
# started streaming -- generous for any real API response in this codebase
# (GoPlus/DexScreener/GeckoTerminal all normally reply in under 2s), tight
# enough that the worst case for ANY single HTTP call through this module is
# now ~20s connect + 20s stall + tenacity's bounded 3-attempt backoff, not 6+
# hours. This is a narrow correction to how an existing default is passed to
# an existing library, not a new retry/timeout system.
def _as_connect_read_timeout(timeout):
    if isinstance(timeout, tuple):
        return timeout
    return (timeout, timeout)


def _request_with_429_retry(method, url, headers=None, params=None, data=None, json=None, timeout=20):
    attempts = 0
    while True:
        try:
            import api_budget          # shared per-minute pacing across all runner processes
            api_budget.gate(url)
        except Exception:  # noqa: BLE001
            pass
        try:
            resp = _get_session().request(method, url, headers=headers, params=params, data=data, json=json,
                                           timeout=_as_connect_read_timeout(timeout))
            # Force the body to download NOW, inside this try/except, instead of
            # lazily on first .content/.json() access later in get_json()/
            # post_json() (outside this function, outside the @retry decorator,
            # and outside this except clause) -- that lazy-access gap is exactly
            # where the real 6.2-hour hang occurred (ChunkedEncodingError raised
            # from inside resp.json() in get_json(), several frames away from
            # any retry/except logic). Reading .content here, under the same
            # read-timeout and exception handling as the request itself, is what
            # actually makes the fix above effective for a stream that stalls or
            # truncates mid-body rather than mid-connect.
            _ = resp.content
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                requests.exceptions.StreamConsumedError) as e:
            raise ApiUnreachable(f"{method} {url} failed at network level: {e}") from e
        try:
            import api_budget          # per-API call ledger (Oct 13 2026): audit calls/day vs free-tier limits
            api_budget.record(url, resp.status_code)
        except Exception:  # noqa: BLE001
            pass
        if resp.status_code != 429 or attempts >= _MAX_429_RETRIES:
            return resp
        wait_s = _retry_after_seconds(resp)
        if wait_s is None:
            return resp  # no Retry-After -- likely a daily/quota-style 429, retrying now won't help
        attempts += 1
        time.sleep(wait_s)


# FIXED 24 Nov 2026, same incident as _as_connect_read_timeout above: this
# decorator's retry_if_exception_type only ever listed requests.ConnectionError
# and requests.Timeout, but _request_with_429_retry() (the function this
# decorator actually wraps) catches those and re-raises them as ApiUnreachable
# -- so this retry clause has never actually matched anything raised by a
# network failure; it was silently inert. Added ApiUnreachable so a transient
# network failure (including the ChunkedEncodingError case above) is now
# genuinely retried up to 3 times with backoff, instead of failing instantly
# on the first blip with no retry at all.
@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, ApiUnreachable)),
)
def get(url, headers=None, params=None, timeout=20):
    return _request_with_429_retry("GET", url, headers=headers, params=params, timeout=timeout)


def get_json(url, headers=None, params=None, timeout=20):
    resp = get(url, headers=headers, params=params, timeout=timeout)
    result = {
        "ok": resp.ok,
        "status_code": resp.status_code,
        "url": resp.url,
        "headers": dict(resp.headers),  # e.g. Adanos's X-RateLimit-* quota headers (Layer 3)
    }
    try:
        result["json"] = resp.json()
    except ValueError:
        result["json"] = None
        result["text"] = resp.text[:2000]
    return result


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, ApiUnreachable)),
)
def post(url, headers=None, params=None, data=None, json=None, timeout=20):
    return _request_with_429_retry("POST", url, headers=headers, params=params, data=data, json=json, timeout=timeout)


def post_json(url, headers=None, params=None, data=None, json=None, timeout=20):
    resp = post(url, headers=headers, params=params, data=data, json=json, timeout=timeout)
    result = {
        "ok": resp.ok,
        "status_code": resp.status_code,
        "url": resp.url,
    }
    try:
        result["json"] = resp.json()
    except ValueError:
        result["json"] = None
        result["text"] = resp.text[:2000]
    return result

# For a {"ok": False, "raw": <get_json result>} shaped failure, builds a real
# diagnostic string from the raw HTTP response instead of a generic default.
# A caller with its own top-level "reason" key (e.g. a hardcoded "API key not
# configured" case) should prefer that -- this is the fallback for everything
# else (bad auth, rate limits, upstream API errors, etc.), which used to be
# swallowed into the literal string "fetch failed" with zero diagnostic value
# (Ali, Sept 24 2026 -- found live, this is exactly what made Layer 1 and
# Layer 2+9's failures undiagnosable).
def describe_fetch_failure(fetch_result: dict) -> str:
    if "reason" in fetch_result:
        return fetch_result["reason"]
    raw = fetch_result.get("raw") or {}
    status = raw.get("status_code")
    detail = raw.get("json") if raw.get("json") is not None else raw.get("text")
    if status is not None:
        return f"HTTP {status}: {str(detail)[:300]}"
    return "fetch failed (no status_code in response -- see raw)"

"""Thin HTTP wrapper: retries transient failures, never raises on 4xx from a
data source (a layer should degrade, not crash the whole poll cycle), and
makes every call diagnosable when something is actually blocked at the
network level (as opposed to the API just saying "no results")."""
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


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


def _request_with_429_retry(method, url, headers=None, params=None, data=None, json=None, timeout=20):
    attempts = 0
    while True:
        try:
            resp = requests.request(method, url, headers=headers, params=params, data=data, json=json, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise ApiUnreachable(f"{method} {url} failed at network level: {e}") from e
        if resp.status_code != 429 or attempts >= _MAX_429_RETRIES:
            return resp
        wait_s = _retry_after_seconds(resp)
        if wait_s is None:
            return resp  # no Retry-After -- likely a daily/quota-style 429, retrying now won't help
        attempts += 1
        time.sleep(wait_s)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
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
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
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

"""Thin HTTP wrapper: retries transient failures, never raises on 4xx from a
data source (a layer should degrade, not crash the whole poll cycle), and
makes every call diagnosable when something is actually blocked at the
network level (as opposed to the API just saying "no results")."""
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class ApiUnreachable(Exception):
    """Raised only for genuine network-level failures (DNS, timeout, connection
    refused/blocked) -- NOT for ordinary HTTP error status codes."""


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
)
def get(url, headers=None, params=None, timeout=20):
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise ApiUnreachable(f"GET {url} failed at network level: {e}") from e
    return resp


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
    try:
        resp = requests.post(url, headers=headers, params=params, data=data, json=json, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise ApiUnreachable(f"POST {url} failed at network level: {e}") from e
    return resp


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

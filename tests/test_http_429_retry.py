"""Covers the 429-aware retry added to utils/http.py Sept 25 2026, per Ali's
"polling calls should be well calculated and managed covering scanning
along with execution side without any failure" instruction. Real gap this
closed: a 429 response is a normal (non-exception) Response object, so the
pre-existing @retry decorator (which only catches ConnectionError/Timeout)
never retried it, at any call site -- scanning (MadeOnSol/DexScreener in
layers/) or execution (DexScreener in swap_executor.py).
"""
import types
import pytest

import utils.http as http_mod


class _FakeResponse:
    def __init__(self, status_code, headers=None, json_body=None, ok=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_body = json_body if json_body is not None else {}
        self.url = "https://example.test/x"
        self.text = "{}"
        self.ok = ok if ok is not None else (200 <= status_code < 400)

    def json(self):
        return self._json_body


def test_get_retries_once_on_429_with_retry_after_then_succeeds(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, params=None, data=None, json=None, timeout=20):
        calls.append(method)
        if len(calls) == 1:
            return _FakeResponse(429, headers={"Retry-After": "0"})
        return _FakeResponse(200, json_body={"ok": True})

    monkeypatch.setattr(http_mod.requests, "request", fake_request)
    result = http_mod.get_json("https://example.test/x")

    assert len(calls) == 2  # one 429, one real retry after Retry-After
    assert result["ok"] is True
    assert result["json"] == {"ok": True}


def test_get_does_not_retry_429_without_retry_after_header(monkeypatch):
    """This is the MadeOnSol daily-quota shape -- resets_at hours away, no
    Retry-After header. Must NOT be retried (would just waste a call)."""
    calls = []

    def fake_request(method, url, headers=None, params=None, data=None, json=None, timeout=20):
        calls.append(method)
        return _FakeResponse(429, headers={}, json_body={"resets_at": "2026-09-26T00:00:00.000Z"})

    monkeypatch.setattr(http_mod.requests, "request", fake_request)
    result = http_mod.get_json("https://example.test/x")

    assert len(calls) == 1  # no retry attempted
    assert result["ok"] is False
    assert result["status_code"] == 429


def test_get_gives_up_after_max_429_retries(monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, params=None, data=None, json=None, timeout=20):
        calls.append(method)
        return _FakeResponse(429, headers={"Retry-After": "0"})

    monkeypatch.setattr(http_mod.requests, "request", fake_request)
    result = http_mod.get_json("https://example.test/x")

    # 1 initial + _MAX_429_RETRIES(2) retries = 3 total, then gives up
    assert len(calls) == 1 + http_mod._MAX_429_RETRIES
    assert result["ok"] is False
    assert result["status_code"] == 429


def test_retry_after_seconds_caps_huge_value():
    resp = _FakeResponse(429, headers={"Retry-After": "99999"})
    assert http_mod._retry_after_seconds(resp) == http_mod._MAX_RETRY_AFTER_SECONDS


def test_retry_after_seconds_ignores_http_date_format():
    resp = _FakeResponse(429, headers={"Retry-After": "Fri, 26 Sep 2026 00:00:00 GMT"})
    assert http_mod._retry_after_seconds(resp) is None


def test_post_json_still_works_normally(monkeypatch):
    def fake_request(method, url, headers=None, params=None, data=None, json=None, timeout=20):
        assert method == "POST"
        return _FakeResponse(200, json_body={"ok": True})

    monkeypatch.setattr(http_mod.requests, "request", fake_request)
    result = http_mod.post_json("https://example.test/x", json={"a": 1})
    assert result["ok"] is True


def test_connection_error_still_raises_apiunreachable(monkeypatch):
    def fake_request(method, url, headers=None, params=None, data=None, json=None, timeout=20):
        raise http_mod.requests.ConnectionError("boom")

    monkeypatch.setattr(http_mod.requests, "request", fake_request)
    with pytest.raises(http_mod.ApiUnreachable):
        http_mod.get("https://example.test/x")

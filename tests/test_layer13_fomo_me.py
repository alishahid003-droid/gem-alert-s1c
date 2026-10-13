import time
import pytest
import state
from layers import layer13_fomo_copytrade as f


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)


def test_resumes_when_v2_me_shows_credits(monkeypatch):
    now = time.time()
    state.set_fomo_credit_state({"remaining": 0, "backoff_until": now + 5 * 3600, "last_me_ts": 0})
    calls = []
    def fake_get_json(url, headers=None, params=None, timeout=None):
        calls.append(url)
        if url.endswith("/v2/me"):
            return {"ok": True, "status_code": 200, "headers": {}, "json": {"credits": {"remaining": 249000}}}
        return {"ok": True, "status_code": 200, "headers": {"x-credits-cost": "125", "x-credits-remaining": "248875"}, "json": {}}
    monkeypatch.setattr(f, "get_json", fake_get_json)
    r = f._fomo_get("/v2/alerts")
    assert r["ok"] and any(c.endswith("/v2/me") for c in calls) and any(c.endswith("/v2/alerts") for c in calls)
    assert state.get_fomo_credit_state()["credits_back_ts"] > 0


def test_stays_backed_off_without_spending_a_data_call(monkeypatch):
    now = time.time()
    state.set_fomo_credit_state({"remaining": 0, "backoff_until": now + 5 * 3600, "last_me_ts": 0})
    urls = []
    def fake_get_json(url, headers=None, params=None, timeout=None):
        urls.append(url)
        return {"ok": True, "status_code": 200, "headers": {}, "json": {"credits": {"remaining": 0}}}
    monkeypatch.setattr(f, "get_json", fake_get_json)
    r = f._fomo_get("/v2/alerts")
    assert r["ok"] is False and r["status_code"] == 402 and urls == [f.CONFIG.fomoapi_base_url + "/v2/me"]
    assert f._fomo_get("/v2/alerts")["ok"] is False and len(urls) == 1     # throttled: no second /v2/me for 30 min

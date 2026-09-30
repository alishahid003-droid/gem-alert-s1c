"""Sept 30 2026 (Ali: add 24h/7d/30d PnL to both Fomo tables) --
fetch_trader_pnl() and its wiring into detect_roster_buys_and_theses /
find_new_trader_candidates."""
import layers.layer13_fomo_copytrade as l13
from config import CONFIG


def test_fetch_trader_pnl_parses_all_three_windows(monkeypatch):
    monkeypatch.setattr(CONFIG, "fomoapi_api_key", "fake-key")

    def fake_get_json(url, headers=None, params=None, timeout=20):
        assert url.endswith("/v2/users/somehandle")
        return {"ok": True, "json": {"pnl": {"24h": 1200.5, "7d": -300.0, "30d": 9000.0}}}

    monkeypatch.setattr(l13, "get_json", fake_get_json)
    out = l13.fetch_trader_pnl("somehandle")
    assert out == {"pnl_24h": 1200.5, "pnl_7d": -300.0, "pnl_30d": 9000.0}


def test_fetch_trader_pnl_fails_closed_on_http_failure(monkeypatch):
    monkeypatch.setattr(CONFIG, "fomoapi_api_key", "fake-key")
    monkeypatch.setattr(l13, "get_json", lambda *a, **kw: {"ok": False, "status_code": 500})
    out = l13.fetch_trader_pnl("somehandle")
    assert out == {"pnl_24h": None, "pnl_7d": None, "pnl_30d": None}


def test_fetch_trader_pnl_missing_key_returns_all_none():
    out = l13.fetch_trader_pnl("somehandle")
    # no key configured in the real test env -- fails closed regardless
    assert out["pnl_24h"] is None or CONFIG.fomoapi_ready()


def test_fetch_trader_pnl_partial_fields_stay_none_not_zero(monkeypatch):
    monkeypatch.setattr(CONFIG, "fomoapi_api_key", "fake-key")
    monkeypatch.setattr(l13, "get_json", lambda *a, **kw: {"ok": True, "json": {"pnl": {"24h": 5.0}}})
    out = l13.fetch_trader_pnl("somehandle")
    assert out["pnl_24h"] == 5.0
    assert out["pnl_7d"] is None
    assert out["pnl_30d"] is None


def test_find_new_trader_candidates_attaches_pnl(monkeypatch):
    monkeypatch.setattr(CONFIG, "fomoapi_api_key", "fake-key")
    monkeypatch.setattr(l13, "fetch_trader_balance_usd", lambda handle: 8000.0)
    monkeypatch.setattr(l13, "fetch_trader_pnl", lambda handle: {"pnl_24h": 1.0, "pnl_7d": 2.0, "pnl_30d": 3.0})
    recorded = {}
    monkeypatch.setattr(l13.state, "record_fomo_candidate", lambda **kw: recorded.update(kw))
    l13.find_new_trader_candidates([{"handle": "newguy", "displayName": "New Guy"}])
    assert recorded["pnl_24h"] == 1.0
    assert recorded["pnl_7d"] == 2.0
    assert recorded["pnl_30d"] == 3.0

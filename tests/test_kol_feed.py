from layers import kol_feed


def _stub_state(monkeypatch, store=None):
    store = store if store is not None else {}
    monkeypatch.setattr(kol_feed.state, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(kol_feed.state, "set_value", lambda k, v: store.__setitem__(k, v))
    return store


def test_unfiltered_call_used_when_it_contains_both_directions(monkeypatch):
    _stub_state(monkeypatch)

    def fake_get_json(url, headers=None, params=None, timeout=20):
        assert params.get("action") is None
        return {"ok": True, "status_code": 200, "url": url, "json": {"trades": [
            {"action": "buy", "kol_name": "Unipcs"},
            {"action": "sell", "kol_name": "Unipcs"},
        ]}}

    monkeypatch.setattr(kol_feed, "get_json", fake_get_json)
    monkeypatch.setattr(kol_feed.CONFIG, "madeonsol_api_key", "msk_test")

    result = kol_feed.fetch_kol_feed_both("solana")
    assert result["ok"] is True
    assert result["mode"] == "unfiltered"
    assert result["calls_made"] == 1
    assert len(result["buy_trades"]) == 1
    assert len(result["sell_trades"]) == 1


def test_falls_back_to_filtered_calls_when_unfiltered_is_single_direction(monkeypatch):
    _stub_state(monkeypatch)
    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(params.get("action"))
        action = params.get("action")
        if action is None:
            # simulate an endpoint that silently defaults to buy-only
            trades = [{"action": "buy", "kol_name": "Unipcs"}]
        elif action == "buy":
            trades = [{"action": "buy", "kol_name": "Unipcs"}]
        else:
            trades = [{"action": "sell", "kol_name": "Unipcs"}]
        return {"ok": True, "status_code": 200, "url": url, "json": {"trades": trades}}

    monkeypatch.setattr(kol_feed, "get_json", fake_get_json)
    monkeypatch.setattr(kol_feed.CONFIG, "madeonsol_api_key", "msk_test")

    result = kol_feed.fetch_kol_feed_both("solana")
    assert result["ok"] is True
    assert result["mode"] == "filtered_fallback"
    # 1 wasted unfiltered probe + 2 filtered calls -- see kol_feed.py docstring
    assert result["calls_made"] == 3
    assert calls == [None, "buy", "sell"]
    assert len(result["buy_trades"]) == 1
    assert len(result["sell_trades"]) == 1


def test_probe_skipped_once_unfiltered_mode_is_confirmed(monkeypatch):
    store = _stub_state(monkeypatch, {"kol_feed_confirmed_unfiltered:solana": True})
    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(params.get("action"))
        return {"ok": True, "status_code": 200, "url": url, "json": {"trades": [
            {"action": "buy", "kol_name": "Unipcs"},
            {"action": "sell", "kol_name": "Unipcs"},
        ]}}

    monkeypatch.setattr(kol_feed, "get_json", fake_get_json)
    monkeypatch.setattr(kol_feed.CONFIG, "madeonsol_api_key", "msk_test")

    result = kol_feed.fetch_kol_feed_both("solana")
    # still only 1 real call made (the confirmed mode doesn't add a second
    # "double-check" call -- confirmation is permanent, not re-verified)
    assert result["calls_made"] == 1
    assert calls == [None]


def test_unconfigured_short_circuits_with_zero_calls(monkeypatch):
    _stub_state(monkeypatch)
    monkeypatch.setattr(kol_feed.CONFIG, "madeonsol_api_key", None)
    result = kol_feed.fetch_kol_feed_both("solana")
    assert result["ok"] is False
    assert result["calls_made"] == 0


def test_both_calls_failing_surfaces_the_real_http_error(monkeypatch):
    # Before this fix, fetch_kol_feed_both("reason") always came back as the
    # literal string "fetch failed" no matter what actually went wrong --
    # buy_fetch never had a top-level "reason" key for a real HTTP failure,
    # only for the one hardcoded "no API key" case. That's why Layer 2+9's
    # "skipped: fetch failed" print carried zero diagnostic value (Ali,
    # Sept 24 2026 -- this is what a live poll-slow run actually printed
    # right before a separate NameError crashed the same run).
    _stub_state(monkeypatch)

    def fake_get_json(url, headers=None, params=None, timeout=20):
        return {"ok": False, "status_code": 401, "url": url, "json": {"error": "invalid API key"}}

    monkeypatch.setattr(kol_feed, "get_json", fake_get_json)
    monkeypatch.setattr(kol_feed.CONFIG, "madeonsol_api_key", "msk_test")

    result = kol_feed.fetch_kol_feed_both("solana")
    assert result["ok"] is False
    assert "401" in result["reason"]
    assert "invalid API key" in result["reason"]

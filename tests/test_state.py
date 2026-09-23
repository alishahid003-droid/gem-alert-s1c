import time

import state as state_module


def _reset_local_state(tmp_path, monkeypatch):
    state_file = tmp_path / "test_state.json"
    # config.CONFIG is a module-level singleton; monkeypatch its attributes
    # directly rather than relying on env vars + reload, so the real
    # environment (which may or may not have Upstash vars set) can't leak in.
    monkeypatch.setattr(state_module, "LOCAL_STATE_FILE", str(state_file))
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", None)


def test_backend_is_local_json_without_upstash_config(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.backend() == "local_json"


def test_backend_is_upstash_when_both_vars_set(monkeypatch):
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", "tok123")
    assert state_module.backend() == "upstash"


def test_get_set_roundtrip_local_json(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.get_value("nope") is None
    state_module.set_value("k1", {"a": 1})
    assert state_module.get_value("k1") == {"a": 1}


def test_wallet_snapshot_and_held_tokens(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.last_snapshot() is None
    assert state_module.held_token_addresses() == set()
    held = [{"asset": {"symbol": "SGEM", "contracts_balances": "0xTOKEN"}}]
    state_module.save_snapshot(held)
    snap = state_module.last_snapshot()
    assert snap is not None
    assert "0xTOKEN" in state_module.held_token_addresses()


def test_mc_history_records_and_trims_old_points(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    now = time.time()
    state_module.record_mc_point("TOKEN_A", 8000, ts=now - 3 * 60 * 60)  # 3hr old -- should get trimmed
    state_module.record_mc_point("TOKEN_A", 120000, ts=now)
    history = state_module.get_mc_history("TOKEN_A")
    assert len(history) == 1
    assert history[0][1] == 120000


def test_mc_history_caps_at_max_points(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    now = time.time()
    for i in range(30):
        state_module.record_mc_point("TOKEN_B", 1000 + i, ts=now - i)
    history = state_module.get_mc_history("TOKEN_B")
    assert len(history) <= state_module.MC_HISTORY_MAX_POINTS


def test_balance_roundtrip_and_prior_balances_map(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.get_prior_balance("W1", "TOKEN_A") is None
    state_module.record_balance("W1", "TOKEN_A", 10.0)
    assert state_module.get_prior_balance("W1", "TOKEN_A") == 10.0
    mapping = state_module.prior_balances_map([("W1", "TOKEN_A"), ("W2", "TOKEN_B")])
    assert mapping == {("W1", "TOKEN_A"): 10.0}


def test_upstash_get_raw_parses_result_wrapper(monkeypatch):
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", "tok123")

    def fake_get_json(url, headers=None, params=None, timeout=20):
        assert headers == {"Authorization": "Bearer tok123"}
        assert url == "https://example.upstash.io/get/mykey"
        return {"ok": True, "status_code": 200, "url": url, "json": {"result": '{"x": 1}'}}

    monkeypatch.setattr(state_module, "get_json", fake_get_json)
    result = state_module._upstash_get_raw("mykey")
    assert result == '{"x": 1}'


def test_upstash_get_raw_returns_none_on_network_failure(monkeypatch):
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", "tok123")

    def fake_get_json(*a, **k):
        raise state_module.ApiUnreachable("blocked")

    monkeypatch.setattr(state_module, "get_json", fake_get_json)
    assert state_module._upstash_get_raw("mykey") is None


def test_last_score_roundtrip(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.get_last_score("TOKEN_A") is None
    state_module.set_last_score("TOKEN_A", 40, "D", mc_usd=8000)
    last = state_module.get_last_score("TOKEN_A")
    assert last["score"] == 40
    assert last["band"] == "D"
    assert last["mc"] == 8000


def test_queue_and_pop_rescans_dedupes_and_caps(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    state_module.queue_rescan("TOKEN_A", "solana", True)
    state_module.queue_rescan("TOKEN_A", "solana", True)  # duplicate, no-op
    state_module.queue_rescan("TOKEN_B", "robinhood_chain", False)
    assert state_module.pending_rescan_count() == 2

    popped = state_module.pop_pending_rescans(limit=1)
    assert len(popped) == 1
    assert state_module.pending_rescan_count() == 1

    rest = state_module.pop_pending_rescans(limit=10)
    assert len(rest) == 1
    assert state_module.pending_rescan_count() == 0


def test_layer1_last_checked_roundtrip_is_per_chain(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.get_layer1_last_checked("solana") is None
    assert state_module.get_layer1_last_checked("robinhood_chain") is None
    state_module.set_layer1_last_checked("solana", "2026-09-07T00:00:00+00:00")
    assert state_module.get_layer1_last_checked("solana") == "2026-09-07T00:00:00+00:00"
    # The other chain's timestamp must stay independent, not shared.
    assert state_module.get_layer1_last_checked("robinhood_chain") is None

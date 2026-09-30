import state as state_module


def _reset_local_state(tmp_path, monkeypatch):
    state_file = tmp_path / "test_state.json"
    monkeypatch.setattr(state_module, "LOCAL_STATE_FILE", str(state_file))
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", None)


def test_get_values_local_backend_matches_get_value(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    state_module.set_value("a", {"n": 1})
    state_module.set_value("b", {"n": 2})
    out = state_module.get_values(["a", "b", "missing"])
    assert out == {"a": {"n": 1}, "b": {"n": 2}, "missing": None}


def test_get_values_empty_list_returns_empty_dict(tmp_path, monkeypatch):
    _reset_local_state(tmp_path, monkeypatch)
    assert state_module.get_values([]) == {}


def test_get_values_upstash_uses_one_pipeline_call(monkeypatch):
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", "tok123")

    calls = []

    def fake_post_json(url, headers=None, params=None, data=None, json=None, timeout=20):
        calls.append((url, json))
        assert url.endswith("/pipeline")
        assert json == [["GET", "k1"], ["GET", "k2"], ["GET", "k3"]]
        return {"ok": True, "status_code": 200,
                "json": [{"result": '{"v": 1}'}, {"result": None}, {"result": '{"v": 3}'}]}

    monkeypatch.setattr(state_module, "post_json", fake_post_json)
    out = state_module.get_values(["k1", "k2", "k3"])
    assert len(calls) == 1, "should be exactly ONE pipeline HTTP call, not one per key"
    assert out == {"k1": {"v": 1}, "k2": None, "k3": {"v": 3}}


def test_get_values_upstash_falls_back_per_key_on_pipeline_failure(monkeypatch):
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", "tok123")

    def fake_post_json(url, headers=None, params=None, data=None, json=None, timeout=20):
        return {"ok": False, "status_code": 500, "json": None}

    per_key_calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        per_key_calls.append(url)
        return {"ok": True, "json": {"result": '{"v": 9}'}}

    monkeypatch.setattr(state_module, "post_json", fake_post_json)
    monkeypatch.setattr(state_module, "get_json", fake_get_json)
    out = state_module.get_values(["k1", "k2"])
    assert len(per_key_calls) == 2
    assert out == {"k1": {"v": 9}, "k2": {"v": 9}}

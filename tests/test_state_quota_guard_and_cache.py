"""Oct 5 2026 -- Upstash free-tier quota incident: 500K commands/month hit,
every request answered 400 "max requests limit exceeded", every read looked
empty. Covers the read cache, the quota guard, the buy refusal and the cycle
throttle added in response."""
import time

import state as state_module


def _upstash(monkeypatch):
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", "https://example.upstash.io")
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", "tok")


def test_get_value_cache_serves_repeat_reads_and_decodes_fresh_copies(monkeypatch):
    _upstash(monkeypatch)
    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        return {"ok": True, "json": {"result": '{"a": [1]}'}}

    monkeypatch.setattr(state_module, "get_json", fake_get_json)
    first = state_module.get_value("k")
    first["a"].append(99)                      # caller mutates what it read
    second = state_module.get_value("k")
    assert len(calls) == 1, "second read must come from the cache"
    assert second == {"a": [1]}, "cache must hand out a fresh copy, never the mutated object"


def test_set_value_writes_through_to_cache(monkeypatch):
    _upstash(monkeypatch)
    monkeypatch.setattr(state_module, "post_json", lambda *a, **k: {"ok": True, "status_code": 200, "json": {}})
    monkeypatch.setattr(state_module, "get_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not hit the network")))
    assert state_module.set_value("k", {"n": 5})
    assert state_module.get_value("k") == {"n": 5}


def test_failed_read_is_not_cached(monkeypatch):
    _upstash(monkeypatch)
    seq = iter([{"ok": False, "status_code": 500, "json": None},
                {"ok": True, "json": {"result": '{"n": 1}'}}])
    monkeypatch.setattr(state_module, "get_json", lambda *a, **k: next(seq))
    assert state_module.get_value("k") is None          # transient failure
    assert state_module.get_value("k") == {"n": 1}      # next read must retry, not serve a cached None


def test_cache_expires_after_ttl(monkeypatch):
    _upstash(monkeypatch)
    monkeypatch.setenv("STATE_READ_CACHE_TTL_SECONDS", "20")
    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        return {"ok": True, "json": {"result": "1"}}

    monkeypatch.setattr(state_module, "get_json", fake_get_json)
    state_module.get_value("k")
    state_module._READ_CACHE["k"] = (time.time() - 21, "1")
    state_module.get_value("k")
    assert len(calls) == 2


def test_get_values_counts_every_key_in_the_pipeline(monkeypatch):
    _upstash(monkeypatch)
    monkeypatch.setattr(state_module, "post_json", lambda *a, **k: {
        "ok": True, "status_code": 200, "json": [{"result": "1"}, {"result": "2"}, {"result": None}]})
    before = state_module.COMMAND_COUNTER["n"]
    state_module.get_values(["x", "y", "z"])
    assert state_module.COMMAND_COUNTER["n"] - before == 3      # Upstash bills one command per key


def test_quota_error_blocks_further_calls_and_is_reported(monkeypatch):
    _upstash(monkeypatch)
    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        return {"ok": False, "status_code": 400,
                "json": {"error": "ERR max requests limit exceeded. Limit: 500000, Usage: 500000."}}

    monkeypatch.setattr(state_module, "get_json", fake_get_json)
    assert state_module.get_value("k") is None
    assert state_module.upstash_blocked()
    assert "limit" in state_module.upstash_block_reason().lower()
    assert state_module.get_value("other") is None
    assert len(calls) == 1, "while blocked, no further requests may be sent (rejected calls still count)"
    assert state_module.set_value("k", 1) is False


def test_blocked_flag_clears_after_window(monkeypatch):
    _upstash(monkeypatch)
    state_module._UPSTASH_BLOCK["until"] = time.time() - 1
    assert not state_module.upstash_blocked()


def test_other_400_is_not_treated_as_quota(monkeypatch):
    _upstash(monkeypatch)
    monkeypatch.setattr(state_module, "get_json",
                        lambda *a, **k: {"ok": False, "status_code": 400, "json": {"error": "ERR syntax error"}})
    state_module.get_value("k")
    assert not state_module.upstash_blocked()


def test_real_buys_refused_while_state_store_blocked(monkeypatch):
    from executor import swap_executor
    monkeypatch.setattr(swap_executor.EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(swap_executor.EXECUTOR_CONFIG, "ready_for_chain", lambda chain: True)
    _upstash(monkeypatch)
    state_module._UPSTASH_BLOCK.update({"until": time.time() + 100, "reason": "max requests limit exceeded"})
    for fn, arg in ((swap_executor.execute_buy_solana, "MINT"), (swap_executor.execute_buy_bsc, "0xabc"),
                    (swap_executor.execute_buy_robinhood_chain, "0xabc")):
        res = fn(arg, 5.0)
        assert not res.ok and "refused" in res.reason and "Upstash" in res.reason


def test_cycle_throttle_skips_recent_cycle_and_runs_when_due(monkeypatch):
    import scheduler
    _upstash(monkeypatch)
    store = {}
    monkeypatch.setattr(state_module, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(state_module, "set_value", lambda k, v: store.__setitem__(k, v) or True)
    assert scheduler._cycle_due("poll-fast", "POLL_FAST_MIN_INTERVAL_SECONDS", 1500) is True
    assert scheduler._cycle_due("poll-fast", "POLL_FAST_MIN_INTERVAL_SECONDS", 1500) is False
    store["cycle_last:poll-fast"] = time.time() - 1600
    assert scheduler._cycle_due("poll-fast", "POLL_FAST_MIN_INTERVAL_SECONDS", 1500) is True


def test_cycle_throttle_off_for_local_backend_and_when_zero(monkeypatch):
    import scheduler
    assert scheduler._cycle_due("poll-fast", "POLL_FAST_MIN_INTERVAL_SECONDS", 1500) is True   # local_json
    _upstash(monkeypatch)
    monkeypatch.setenv("POLL_FAST_MIN_INTERVAL_SECONDS", "0")
    assert scheduler._cycle_due("poll-fast", "POLL_FAST_MIN_INTERVAL_SECONDS", 1500) is True


def test_cycle_skipped_while_upstash_blocked(monkeypatch):
    import scheduler
    _upstash(monkeypatch)
    state_module._UPSTASH_BLOCK.update({"until": time.time() + 100, "reason": "max requests limit exceeded"})
    assert scheduler._cycle_due("poll-slow", "POLL_SLOW_MIN_INTERVAL_SECONDS", 3000) is False

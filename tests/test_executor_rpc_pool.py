"""
Tests for the multi-chain RPC pool failover (Solana, BSC, Robinhood Chain --
Ali, Sept 23 2026: "point 6 should cover all 3 chains"). Covers: first
healthy endpoint used directly, failover within a chain's pool, rate-limit
handling, whole-pool failure, cooldown skipping on the next call, an
unknown/empty chain failing cleanly, and that each chain's cooldown state
is independent (a dead Solana endpoint doesn't affect BSC's pool).
"""
import pytest

import state
import executor.rpc_pool as rpc_pool


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _fake_post_json(results_by_url):
    calls = []

    def _post_json(url, json=None, timeout=None):
        calls.append(url)
        return results_by_url[url]

    return _post_json, calls


def test_first_healthy_endpoint_is_used_directly(monkeypatch):
    sol_pool = rpc_pool.RPC_ENDPOINT_POOLS["solana"]
    good = {"ok": True, "status_code": 200, "json": {"result": {"slot": 123}}}
    fake, calls = _fake_post_json({sol_pool[0]: good})
    monkeypatch.setattr(rpc_pool, "post_json", fake)
    result = rpc_pool.rpc_call("solana", "getSlot", [])
    assert result["ok"] is True
    assert result["result"] == {"slot": 123}
    assert result["endpoint_used"] == sol_pool[0]
    assert calls == [sol_pool[0]]


def test_failing_first_endpoint_fails_over_to_the_next_one(monkeypatch):
    sol_pool = rpc_pool.RPC_ENDPOINT_POOLS["solana"]
    bad = {"ok": False, "status_code": 503, "json": None}
    good = {"ok": True, "status_code": 200, "json": {"result": "healthy"}}
    fake, calls = _fake_post_json({sol_pool[0]: bad, sol_pool[1]: good})
    monkeypatch.setattr(rpc_pool, "post_json", fake)
    result = rpc_pool.rpc_call("solana", "getSlot", [])
    assert result["ok"] is True
    assert result["endpoint_used"] == sol_pool[1]
    assert calls[:2] == [sol_pool[0], sol_pool[1]]


def test_rate_limited_response_fails_over_not_treated_as_hard_error(monkeypatch):
    bsc_pool = rpc_pool.RPC_ENDPOINT_POOLS["bsc"]
    rate_limited = {"ok": True, "status_code": 429, "json": {"error": {"message": "Too many requests"}}}
    good = {"ok": True, "status_code": 200, "json": {"result": "healthy"}}
    fake, calls = _fake_post_json({bsc_pool[0]: rate_limited, bsc_pool[1]: good})
    monkeypatch.setattr(rpc_pool, "post_json", fake)
    result = rpc_pool.rpc_call("bsc", "eth_blockNumber", [])
    assert result["ok"] is True
    assert result["endpoint_used"] == bsc_pool[1]


def test_whole_pool_failing_returns_ok_false_not_an_exception(monkeypatch):
    sol_pool = rpc_pool.RPC_ENDPOINT_POOLS["solana"]
    bad = {"ok": False, "status_code": 500, "json": None}
    fake, calls = _fake_post_json({url: bad for url in sol_pool})
    monkeypatch.setattr(rpc_pool, "post_json", fake)
    result = rpc_pool.rpc_call("solana", "getSlot", [])
    assert result["ok"] is False
    assert "reason" in result
    assert len(result["tried"]) == len(sol_pool)


def test_a_failed_endpoint_is_skipped_on_the_very_next_call(monkeypatch):
    sol_pool = rpc_pool.RPC_ENDPOINT_POOLS["solana"]
    bad = {"ok": False, "status_code": 503, "json": None}
    good = {"ok": True, "status_code": 200, "json": {"result": "healthy"}}
    fake, calls = _fake_post_json({sol_pool[0]: bad, sol_pool[1]: good})
    monkeypatch.setattr(rpc_pool, "post_json", fake)

    rpc_pool.rpc_call("solana", "getSlot", [])  # marks endpoint 0 as cooling down
    calls.clear()

    result = rpc_pool.rpc_call("solana", "getSlot", [])
    assert result["ok"] is True
    assert sol_pool[0] not in calls


def test_unknown_chain_fails_cleanly_without_calling_anything(monkeypatch):
    fake, calls = _fake_post_json({})
    monkeypatch.setattr(rpc_pool, "post_json", fake)
    result = rpc_pool.rpc_call("dogecoin", "getSlot", [])
    assert result["ok"] is False
    assert "no RPC endpoints configured" in result["reason"]
    assert calls == []


def test_robinhood_chain_pool_has_exactly_one_candidate_endpoint():
    # Documents the real current state rather than assuming more exist --
    # only one official endpoint was found (Robinhood's own docs), and it's
    # explicitly rate-limited per those docs.
    assert len(rpc_pool.RPC_ENDPOINT_POOLS["robinhood_chain"]) == 1
    assert "robinhood" in rpc_pool.RPC_ENDPOINT_POOLS["robinhood_chain"][0]


def test_cooldown_is_independent_per_chain(monkeypatch):
    sol_pool = rpc_pool.RPC_ENDPOINT_POOLS["solana"]
    bsc_pool = rpc_pool.RPC_ENDPOINT_POOLS["bsc"]
    bad = {"ok": False, "status_code": 503, "json": None}
    good = {"ok": True, "status_code": 200, "json": {"result": "healthy"}}
    fake, calls = _fake_post_json({
        sol_pool[0]: bad,
        sol_pool[1]: good,
        bsc_pool[0]: good,  # same relative position (index 0) as the failed Solana one
    })
    monkeypatch.setattr(rpc_pool, "post_json", fake)

    rpc_pool.rpc_call("solana", "getSlot", [])  # trips solana's endpoint 0 into cooldown
    calls.clear()

    result = rpc_pool.rpc_call("bsc", "eth_blockNumber", [])  # bsc's endpoint 0 must be unaffected
    assert result["ok"] is True
    assert result["endpoint_used"] == bsc_pool[0]

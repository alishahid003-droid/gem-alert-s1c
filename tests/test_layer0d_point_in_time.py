import pytest

import state
from layers import layer0d_point_in_time as pit


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    # Same isolation pattern as tests/test_layer1_deployer.py -- these
    # functions record real MadeOnSol call-budget state, so tests must not
    # touch the live on-device state file.
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setattr(state.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(state.CONFIG, "upstash_redis_rest_token", None)


def test_deployer_wallet_onchain_short_page_means_genesis_reached(monkeypatch):
    # A page shorter than SIGNATURES_PER_PAGE means we've reached the
    # oldest signature in one call -- must NOT keep paging.
    calls = []

    def fake_rpc_call(chain, method, params):
        calls.append(method)
        if method == "getSignaturesForAddress":
            return {"ok": True, "result": [{"signature": "sigNEWER"}, {"signature": "sigOLDEST"}]}
        if method == "getTransaction":
            return {"ok": True, "result": {
                "transaction": {"message": {"accountKeys": [{"pubkey": "DeployerWallet111"}]}}
            }}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(pit, "rpc_call", fake_rpc_call)
    result = pit.fetch_deployer_wallet_onchain("SomeMint111")
    assert result["ok"] is True
    assert result["deployer_wallet"] == "DeployerWallet111"
    assert result["launch_signature"] == "sigOLDEST"
    assert result["pages_used"] == 1
    assert calls.count("getSignaturesForAddress") == 1


def test_deployer_wallet_onchain_truncates_at_max_pages(monkeypatch):
    # Every page comes back full (SIGNATURES_PER_PAGE items) -- genesis
    # never reached, so this must fail honestly with truncated=True rather
    # than loop forever or silently return a wrong "oldest" signature.
    full_page = [{"signature": f"sig{i}"} for i in range(pit.SIGNATURES_PER_PAGE)]

    def fake_rpc_call(chain, method, params):
        return {"ok": True, "result": full_page}

    monkeypatch.setattr(pit, "rpc_call", fake_rpc_call)
    result = pit.fetch_deployer_wallet_onchain("HighVolumeMint", max_pages=3)
    assert result["ok"] is False
    assert result["truncated"] is True
    assert result["pages_used"] == 3


def test_deployer_wallet_onchain_rpc_failure_propagates(monkeypatch):
    def fake_rpc_call(chain, method, params):
        return {"ok": False, "reason": "all RPC endpoints down"}

    monkeypatch.setattr(pit, "rpc_call", fake_rpc_call)
    result = pit.fetch_deployer_wallet_onchain("SomeMint")
    assert result["ok"] is False
    assert "all RPC endpoints down" in result["reason"]


def test_deployer_asof_blocked_without_madeonsol_key(monkeypatch):
    monkeypatch.setattr(pit.CONFIG, "madeonsol_api_key", None)
    result = pit.fetch_deployer_asof("SomeWallet", "solana", "2026-06-01")
    assert result["ok"] is False
    assert "MADEONSOL_API_KEY" in result["reason"]


def test_deployer_asof_uses_rhc_prefix_for_robinhood_chain(monkeypatch):
    monkeypatch.setattr(pit.CONFIG, "madeonsol_api_key", "fake-key")
    captured_url = {}

    def fake_get_json(url, headers=None, params=None):
        captured_url["url"] = url
        return {"ok": True, "json": {"as_of": True, "snapshot": {"tier": "elite"}}}

    monkeypatch.setattr(pit, "get_json", fake_get_json)
    result = pit.fetch_deployer_asof("SomeWallet", "robinhood_chain", "2026-06-01")
    assert result["ok"] is True
    assert result["snapshot"]["tier"] == "elite"
    assert "/rhc/deployer-hunter/SomeWallet/as-of" in captured_url["url"]


def test_birdeye_ohlcv_blocked_without_key(monkeypatch):
    monkeypatch.setattr(pit.CONFIG, "birdeye_api_key", None)
    result = pit.fetch_birdeye_ohlcv("solana", "SomeMint", 0, 100)
    assert result["ok"] is False
    assert "BIRDEYE_API_KEY" in result["reason"]


def test_birdeye_ohlcv_rejects_robinhood_chain(monkeypatch):
    monkeypatch.setattr(pit.CONFIG, "birdeye_api_key", "fake-key")
    result = pit.fetch_birdeye_ohlcv("robinhood_chain", "0xSomeAddr", 0, 100)
    assert result["ok"] is False
    assert "not a Birdeye-supported network" in result["reason"]


def test_summarize_launch_window_computes_peak_and_drawdown():
    candles = [
        {"o": 1.0, "h": 1.5, "l": 0.9, "c": 1.2, "v": 1000},
        {"o": 1.2, "h": 3.0, "l": 1.1, "c": 2.5, "v": 5000},
        {"o": 2.5, "h": 2.6, "l": 0.5, "c": 0.6, "v": 3000},
    ]
    summary = pit.summarize_launch_window(candles)
    assert summary["ok"] is True
    assert summary["first_price"] == 1.0
    assert summary["peak_price"] == 3.0
    assert summary["last_price_in_window"] == 0.6
    # (0.6 - 3.0) / 3.0 * 100 = -80.0
    assert summary["drawdown_from_peak_pct"] == -80.0
    assert summary["total_volume"] == 9000
    assert summary["num_candles"] == 3


def test_summarize_launch_window_empty_candles():
    result = pit.summarize_launch_window([])
    assert result["ok"] is False

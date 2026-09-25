"""Tests for executor/rhc_pool_discovery.py -- real v4 Initialize-event
pool discovery for Robinhood Chain. Added Sept 25 2026.

All rpc_call() calls are mocked (monkeypatch), following the same
convention used throughout tests/test_layer1_deployer.py and
tests/test_layer0_scoring.py -- no real network access, deterministic
inputs/outputs, and every fixture log is built from the real ABI encoding
rules documented in rhc_pool_discovery.py's own module docstring (not
invented byte layouts).
"""
import pytest

import executor.rhc_pool_discovery as rpd


TOKEN = "0x1111111111111111111111111111111111111111"[:42]  # keep 20 bytes
QUOTE = rpd.NATIVE_CURRENCY


def _topic(address: str) -> str:
    return rpd._address_to_topic(address)


def _encode_initialize_data(fee: int, tick_spacing: int, hooks: str,
                             sqrt_price_x96: int, tick: int) -> str:
    """Builds a real Initialize-event data blob the way Solidity would
    ABI-encode it -- five right-aligned 32-byte words -- so decode tests
    exercise the exact inverse of _decode_initialize_data's real layout."""
    def word(v: int) -> str:
        if v < 0:
            v += 2 ** 256
        return format(v, "064x")

    hooks_word = hooks.lower().replace("0x", "").rjust(64, "0")
    return "0x" + word(fee) + word(tick_spacing) + hooks_word + word(sqrt_price_x96) + word(tick)


# ---------------------------------------------------------------------------
# _topic_to_address / _address_to_topic
# ---------------------------------------------------------------------------

def test_address_to_topic_and_back_round_trips():
    addr = "0x1234567890123456789012345678901234567890"
    topic = rpd._address_to_topic(addr)
    assert topic == "0x" + "0" * 24 + "1234567890123456789012345678901234567890"
    assert rpd._topic_to_address(topic) == addr


# ---------------------------------------------------------------------------
# _decode_initialize_data
# ---------------------------------------------------------------------------

def test_decode_initialize_data_positive_tick_spacing_and_tick():
    data = _encode_initialize_data(3000, 60, "0x" + "0" * 40, 79228162514264337593543950336, 12345)
    decoded = rpd._decode_initialize_data(data)
    assert decoded["fee"] == 3000
    assert decoded["tick_spacing"] == 60
    assert decoded["hooks"] == "0x" + "0" * 40
    assert decoded["sqrt_price_x96"] == 79228162514264337593543950336
    assert decoded["tick"] == 12345


def test_decode_initialize_data_negative_tick_and_tick_spacing():
    # int24 two's-complement: tick=-100 and tick_spacing=-60 must decode
    # back to real negative Python ints, not their unsigned encodings.
    data = _encode_initialize_data(500, -60, "0x" + "1" * 40, 1, -100)
    decoded = rpd._decode_initialize_data(data)
    assert decoded["tick_spacing"] == -60
    assert decoded["tick"] == -100


def test_decode_initialize_data_too_short_raises():
    with pytest.raises(ValueError):
        rpd._decode_initialize_data("0x" + "00" * 32)  # only 1 word, need 5


# ---------------------------------------------------------------------------
# find_v4_pool
# ---------------------------------------------------------------------------

def _make_log(currency0: str, currency1: str, fee: int, tick_spacing: int,
              hooks: str, block: int, pool_id: str = "0x" + "ab" * 32) -> dict:
    return {
        "topics": [rpd.INITIALIZE_EVENT_TOPIC0, pool_id, _topic(currency0), _topic(currency1)],
        "data": _encode_initialize_data(fee, tick_spacing, hooks, 2 ** 96, 0),
        "blockNumber": hex(block),
    }


def test_find_v4_pool_returns_real_match_on_first_chunk(monkeypatch):
    log = _make_log(TOKEN, QUOTE, 3000, 60, "0x" + "0" * 40, block=1_000_000)

    def fake_rpc_call(chain, method, params, timeout=15):
        assert chain == "robinhood_chain"
        if method == "eth_getLogs":
            return {"ok": True, "result": [log]}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)

    result = rpd.find_v4_pool(TOKEN, QUOTE, latest_block=1_000_500)
    assert result["ok"] is True
    assert result["block"] == 1_000_000
    assert result["pool_key"]["fee"] == 3000
    assert result["pool_key"]["tick_spacing"] == 60
    assert {result["pool_key"]["currency0"].lower(), result["pool_key"]["currency1"].lower()} == \
        {TOKEN.lower(), QUOTE.lower()}


def test_find_v4_pool_fetches_chain_tip_when_latest_block_omitted(monkeypatch):
    calls = []

    def fake_rpc_call(chain, method, params, timeout=15):
        calls.append(method)
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2_000_000)}
        if method == "eth_getLogs":
            return {"ok": True, "result": []}
        raise AssertionError

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE)
    assert calls[0] == "eth_blockNumber"
    assert result["ok"] is False


def test_find_v4_pool_chain_tip_fetch_failure_is_reported(monkeypatch):
    def fake_rpc_call(chain, method, params, timeout=15):
        return {"ok": False, "reason": "all endpoints down"}

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE)
    assert result["ok"] is False
    assert "chain tip" in result["reason"]


def test_find_v4_pool_skips_logs_for_a_different_pair(monkeypatch):
    other_token = "0x" + "9" * 40
    log = _make_log(other_token, QUOTE, 500, 10, "0x" + "0" * 40, block=999)

    def fake_rpc_call(chain, method, params, timeout=15):
        return {"ok": True, "result": [log]}

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE, latest_block=1_000)
    assert result["ok"] is False
    assert "no v4 pool found" in result["reason"]


def test_find_v4_pool_scans_backward_across_chunks_until_match(monkeypatch):
    # Real match sits in the SECOND (older) chunk -- confirms the backward
    # from/to block stepping actually happens rather than only checking
    # the first window.
    match_log = _make_log(TOKEN, QUOTE, 100, 1, "0x" + "0" * 40, block=1)
    calls = {"n": 0}

    def fake_rpc_call(chain, method, params, timeout=15):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "result": []}
        return {"ok": True, "result": [match_log]}

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE, latest_block=rpd._LOG_SCAN_CHUNK_BLOCKS + 10)
    assert result["ok"] is True
    assert calls["n"] == 2


def test_find_v4_pool_eth_get_logs_failure_short_circuits(monkeypatch):
    def fake_rpc_call(chain, method, params, timeout=15):
        return {"ok": False, "reason": "rate limited"}

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE, latest_block=1_000)
    assert result["ok"] is False
    assert "eth_getLogs failed" in result["reason"]


def test_find_v4_pool_gives_up_after_max_chunks_with_no_match(monkeypatch):
    calls = {"n": 0}

    def fake_rpc_call(chain, method, params, timeout=15):
        calls["n"] += 1
        return {"ok": True, "result": []}

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE, latest_block=rpd._LOG_SCAN_CHUNK_BLOCKS * 100)
    assert result["ok"] is False
    assert calls["n"] == rpd._LOG_SCAN_MAX_CHUNKS


def test_find_v4_pool_ignores_logs_with_too_few_topics(monkeypatch):
    malformed = {
        "topics": [rpd.INITIALIZE_EVENT_TOPIC0, "0x" + "ab" * 32],  # missing currency topics
        "data": _encode_initialize_data(1, 1, "0x" + "0" * 40, 1, 0),
        "blockNumber": "0x1",
    }

    def fake_rpc_call(chain, method, params, timeout=15):
        return {"ok": True, "result": [malformed]}

    monkeypatch.setattr(rpd, "rpc_call", fake_rpc_call)
    result = rpd.find_v4_pool(TOKEN, QUOTE, latest_block=1_000)
    assert result["ok"] is False

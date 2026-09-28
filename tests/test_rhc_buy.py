"""
Covers the real Robinhood Chain (RHC) auto-buy path built Sept 25, 2026:
_rhc_native_price_usd (deriving native RHC ether's USD price from a real
DexScreener pair) and execute_buy_robinhood_chain (chaining
rhc_pool_discovery.find_v4_pool -> rhc_v4_swap.build_v4_exact_in_single_calldata
-> sign/send/confirm), plus the ExecutorConfig.ready_for_chain fix that
used to permanently block this path even with a real key configured.

Everything here is mocked -- no live RPC, no real key, no real money --
same discipline as tests/test_swap_executor_fills.py.
"""
import pytest
from eth_abi import encode as abi_encode

import state
import executor.swap_executor as swap_executor
import executor.rhc_pool_discovery as rhc_pool_discovery
from executor.config import EXECUTOR_CONFIG, ExecutorConfig
from executor.rhc_pool_discovery import INITIALIZE_EVENT_TOPIC0, NATIVE_CURRENCY

# Real slippage-protection fix, Sept 28 2026: execute_buy_robinhood_chain now
# makes one extra eth_call (to UNISWAP_V4_QUOTER_RHC) before building the
# swap, to get a real amount_out_minimum floor instead of hardcoding 0. Every
# fake_rpc_call fixture below that exercises the buy path needs to answer it.
QUOTER_RESULT_HEX = "0x" + abi_encode(["uint256", "uint256"], [5 * 10**18, 100000]).hex()


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))


TOKEN = "0x" + "ab" * 20


def _topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def _encode_initialize_data(fee, tick_spacing, hooks, sqrt_price_x96, tick) -> str:
    def word(v):
        if v < 0:
            v += 2 ** 256
        return format(v, "064x")
    hooks_word = hooks.lower().replace("0x", "").rjust(64, "0")
    return "0x" + word(fee) + word(tick_spacing) + hooks_word + word(sqrt_price_x96) + word(tick)


def _make_initialize_log(currency0, currency1, fee, tick_spacing, hooks, block):
    return {
        "topics": [INITIALIZE_EVENT_TOPIC0, "0x" + "ab" * 32, _topic(currency0), _topic(currency1)],
        "data": _encode_initialize_data(fee, tick_spacing, hooks, 2 ** 96, 0),
        "blockNumber": hex(block),
    }


def test_ready_for_chain_rhc_no_longer_requires_rhc_rpc_url(monkeypatch):
    # FIXED Sept 25 2026 -- this used to also require rhc_rpc_url, which
    # has no default and nothing sets, so ready_for_chain("robinhood_chain")
    # used to always be False no matter what. A real key + execution
    # enabled must now be enough, matching solana/bsc.
    cfg = ExecutorConfig()
    cfg.execution_enabled = True
    cfg.rhc_private_key = "0x" + "11" * 32
    cfg.rhc_rpc_url = None
    assert cfg.ready_for_chain("robinhood_chain") is True


def test_rhc_native_price_usd_derives_from_native_quoted_pair(monkeypatch):
    def fake_get_json(url, params=None, **kwargs):
        assert "dexscreener.com/token-pairs/v1/robinhood/" in url
        return {"ok": True, "json": [
            {
                "quoteToken": {"address": NATIVE_CURRENCY},
                "priceUsd": "2.50",
                "priceNative": "0.001",
                "liquidity": {"usd": 100000},
            },
            {
                # a non-native-quoted pair with higher liquidity -- must be
                # ignored, since dividing by ITS priceNative would not give
                # native RHC ether's USD price at all.
                "quoteToken": {"address": "0x" + "cc" * 20},
                "priceUsd": "9.99",
                "priceNative": "3.0",
                "liquidity": {"usd": 999999},
            },
        ]}

    monkeypatch.setattr(swap_executor, "get_json", fake_get_json)
    price = swap_executor._rhc_native_price_usd(TOKEN)
    assert price == pytest.approx(2.50 / 0.001)  # $2500 per native RHC ether


def test_rhc_native_price_usd_returns_none_with_no_native_quoted_pair(monkeypatch):
    monkeypatch.setattr(swap_executor, "get_json", lambda url, params=None, **kw: {"ok": True, "json": [
        {"quoteToken": {"address": "0x" + "cc" * 20}, "priceUsd": "1", "priceNative": "1", "liquidity": {"usd": 1}},
    ]})
    assert swap_executor._rhc_native_price_usd(TOKEN) is None


def test_execute_buy_robinhood_chain_end_to_end(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())

    def fake_get_json(url, params=None, **kwargs):
        return {"ok": True, "json": [
            {"quoteToken": {"address": NATIVE_CURRENCY}, "priceUsd": "2.0", "priceNative": "1.0",
             "liquidity": {"usd": 50000}},
        ]}
    monkeypatch.setattr(swap_executor, "get_json", fake_get_json)

    log = _make_initialize_log(NATIVE_CURRENCY, TOKEN, 3000, 60, "0x" + "00" * 20, block=1000)

    def fake_rpc_call(chain, method, params):
        assert chain == "robinhood_chain"
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2000)}
        if method == "eth_getLogs":
            return {"ok": True, "result": [log]}
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x1"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}
        if method == "eth_call":
            return {"ok": True, "result": QUOTER_RESULT_HEX}
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xrhcbuyhash1"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)
    monkeypatch.setattr(rhc_pool_discovery, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_buy_robinhood_chain(TOKEN, usd_amount=10.0)

    assert result.ok, result.reason
    assert result.tx_signature == "0xrhcbuyhash1"
    assert result.filled_usd == pytest.approx(10.0)

    trade_log = state.get_trade_log(limit=5)
    assert len(trade_log) == 1
    assert trade_log[0]["side"] == "buy" and trade_log[0]["ok"] is True


def test_execute_buy_robinhood_chain_refuses_without_execution_enabled(monkeypatch):
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", False)
    result = swap_executor.execute_buy_robinhood_chain(TOKEN, usd_amount=10.0)
    assert result.ok is False
    assert "EXECUTION_ENABLED" in result.reason


def test_execute_buy_robinhood_chain_refuses_when_no_price_available(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    monkeypatch.setattr(swap_executor, "get_json", lambda url, params=None, **kw: {"ok": False, "status_code": 500})

    result = swap_executor.execute_buy_robinhood_chain(TOKEN, usd_amount=10.0)
    assert result.ok is False
    assert "price" in result.reason.lower()


def test_execute_buy_robinhood_chain_sets_a_real_slippage_floor(monkeypatch):
    """Real bug, fixed Sept 28 2026: amount_out_minimum used to be
    hardcoded to 0 on every RHC buy -- a sandwich or a thin pool could let
    the swap succeed on-chain returning almost nothing, with no revert.
    Confirms the calldata builder is actually called with a real,
    quote-derived amount_out_minimum, not still 0 -- by spying on
    build_v4_exact_in_single_calldata itself rather than decoding the
    signed raw transaction back apart (fragile across eth_account/web3
    versions, and not what this bug is actually about)."""
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    monkeypatch.setattr(swap_executor, "get_json", lambda url, params=None, **kw: {"ok": True, "json": [
        {"quoteToken": {"address": NATIVE_CURRENCY}, "priceUsd": "2.0", "priceNative": "1.0",
         "liquidity": {"usd": 50000}},
    ]})
    log = _make_initialize_log(NATIVE_CURRENCY, TOKEN, 3000, 60, "0x" + "00" * 20, block=1000)

    def fake_rpc_call(chain, method, params):
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2000)}
        if method == "eth_getLogs":
            return {"ok": True, "result": [log]}
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x1"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}
        if method == "eth_call":
            return {"ok": True, "result": QUOTER_RESULT_HEX}  # quotes 5e18 raw tokens out
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xrhcbuyhash2"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)
    monkeypatch.setattr(rhc_pool_discovery, "rpc_call", fake_rpc_call)

    captured = {}
    real_builder = swap_executor.build_v4_exact_in_single_calldata

    def spying_builder(pool_key, zero_for_one, amount_in, amount_out_minimum, deadline, hook_data=None):
        captured["amount_out_minimum"] = amount_out_minimum
        return real_builder(pool_key, zero_for_one, amount_in, amount_out_minimum, deadline, hook_data)

    monkeypatch.setattr(swap_executor, "build_v4_exact_in_single_calldata", spying_builder)

    result = swap_executor.execute_buy_robinhood_chain(TOKEN, usd_amount=10.0)
    assert result.ok, result.reason

    quoted_out = 5 * 10**18
    expected_floor = int(quoted_out * (1 - swap_executor.RHC_BUY_SLIPPAGE_TOLERANCE))
    assert captured["amount_out_minimum"] == expected_floor
    assert captured["amount_out_minimum"] > 0  # the actual bug: this used to always be 0


def test_execute_buy_robinhood_chain_refuses_when_no_quote_available(monkeypatch):
    """A pool is found and priced, but the Quoter call itself fails --
    must refuse rather than fall back to amount_out_minimum=0 (no downside
    to not buying, same reasoning as the 'no live price' refusal)."""
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    monkeypatch.setattr(swap_executor, "get_json", lambda url, params=None, **kw: {"ok": True, "json": [
        {"quoteToken": {"address": NATIVE_CURRENCY}, "priceUsd": "2.0", "priceNative": "1.0",
         "liquidity": {"usd": 50000}},
    ]})
    log = _make_initialize_log(NATIVE_CURRENCY, TOKEN, 3000, 60, "0x" + "00" * 20, block=1000)

    def fake_rpc_call(chain, method, params):
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2000)}
        if method == "eth_getLogs":
            return {"ok": True, "result": [log]}
        if method == "eth_call":
            return {"ok": False, "reason": "rpc timeout"}  # the Quoter call fails
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)
    monkeypatch.setattr(rhc_pool_discovery, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_buy_robinhood_chain(TOKEN, usd_amount=10.0)
    assert result.ok is False
    assert "quote" in result.reason.lower()


def test_execute_buy_robinhood_chain_refuses_when_no_pool_found(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    monkeypatch.setattr(swap_executor, "get_json", lambda url, params=None, **kw: {"ok": True, "json": [
        {"quoteToken": {"address": NATIVE_CURRENCY}, "priceUsd": "2.0", "priceNative": "1.0",
         "liquidity": {"usd": 50000}},
    ]})

    def fake_rpc_call(chain, method, params):
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2000)}
        if method == "eth_getLogs":
            return {"ok": True, "result": []}  # no pool ever found
        raise AssertionError(method)

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)
    monkeypatch.setattr(rhc_pool_discovery, "rpc_call", fake_rpc_call)
    result = swap_executor.execute_buy_robinhood_chain(TOKEN, usd_amount=10.0)
    assert result.ok is False
    assert "pool" in result.reason.lower()

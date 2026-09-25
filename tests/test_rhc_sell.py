"""
Covers the real Robinhood Chain (RHC) auto-sell path built Sept 25, 2026:
_sell_robinhood_chain (ERC20 -> Permit2 -> Universal Router approval
chain, then a real v4 swap via the same rhc_pool_discovery/rhc_v4_swap
pieces the buy path uses), plus the _sign_and_send_bsc_tx ->
_sign_and_send_evm_tx generalization that made reusing it here possible.

Everything here is mocked -- no live RPC, no real key, no real money --
same discipline as tests/test_rhc_buy.py and test_swap_executor_fills.py.
"""
import pytest
from web3 import Web3

import state
import executor.swap_executor as swap_executor
from executor.config import EXECUTOR_CONFIG
from executor.rhc_pool_discovery import INITIALIZE_EVENT_TOPIC0, NATIVE_CURRENCY


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))


TOKEN = Web3.to_checksum_address("0x" + "ab" * 20)

DECIMALS_SELECTOR = "0x" + Web3.keccak(text="decimals()").hex()[:8]
ERC20_ALLOWANCE_SELECTOR = "0x" + Web3.keccak(text="allowance(address,address)").hex()[:8]
PERMIT2_ALLOWANCE_SELECTOR = "0x" + Web3.keccak(text="allowance(address,address,address)").hex()[:8]


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


def _encode_uint160_uint48_uint48(amount, expiration, nonce) -> str:
    w3 = Web3()
    return "0x" + w3.codec.encode(["uint160", "uint48", "uint48"], [amount, expiration, nonce]).hex()


def _make_harness(monkeypatch, account, erc20_allowance=0, permit2_allowance=0, decimals=18,
                   dexscreener_price_usd=2.0, dexscreener_price_native=1.0):
    """Builds a fake_rpc_call + fake_get_json pair wired for a real-looking
    sell scenario, and returns (sent_tx_targets, balance_calls) lists the
    test can assert on afterward."""
    log = _make_initialize_log(NATIVE_CURRENCY, TOKEN, 3000, 60, "0x" + "00" * 20, block=1000)
    sent = []  # records ("send", n) per eth_sendRawTransaction call, in order
    balance_calls = {"n": 0}

    def fake_get_json(url, params=None, **kwargs):
        return {"ok": True, "json": [
            {"quoteToken": {"address": NATIVE_CURRENCY}, "priceUsd": str(dexscreener_price_usd),
             "priceNative": str(dexscreener_price_native), "liquidity": {"usd": 50000}},
        ]}
    monkeypatch.setattr(swap_executor, "get_json", fake_get_json)

    def fake_rpc_call(chain, method, params):
        assert chain == "robinhood_chain"
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2000)}
        if method == "eth_getLogs":
            return {"ok": True, "result": [log]}
        if method == "eth_call":
            to = params[0]["to"]
            data = params[0]["data"]
            if data.startswith(DECIMALS_SELECTOR):
                return {"ok": True, "result": "0x" + format(decimals, "064x")}
            if data.startswith(ERC20_ALLOWANCE_SELECTOR):
                return {"ok": True, "result": "0x" + format(erc20_allowance, "064x")}
            if data.startswith(PERMIT2_ALLOWANCE_SELECTOR):
                return {"ok": True, "result": _encode_uint160_uint48_uint48(permit2_allowance, 0, 0)}
            raise AssertionError(f"unexpected eth_call to {to}: {data[:10]}")
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x1"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}  # 1 gwei
        if method == "eth_sendRawTransaction":
            sent.append(len(sent))
            return {"ok": True, "result": f"0xsellhash{len(sent)}"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "gasUsed": "0x5208", "logs": []}}
        if method == "eth_getBalance":
            balance_calls["n"] += 1
            # 1st call = pre-swap balance, 2nd = post-swap balance -- +0.5
            # native RHC ether received, real balance-diff test data.
            return {"ok": True, "result": hex(10 * 10 ** 18) if balance_calls["n"] == 1 else hex(int(10.5 * 10 ** 18))}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)
    import executor.rhc_pool_discovery as rhc_pool_discovery
    monkeypatch.setattr(rhc_pool_discovery, "rpc_call", fake_rpc_call)
    return sent, balance_calls


def test_sell_sends_both_approvals_then_swaps_when_neither_is_set(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    sent, _ = _make_harness(monkeypatch, account, erc20_allowance=0, permit2_allowance=0)

    result = swap_executor.execute_sell("robinhood_chain", TOKEN, amount_tokens=100.0, reason="test sell")

    assert result.ok, result.reason
    # ERC20 approve, Permit2 approve, swap -- 3 real transactions sent.
    assert len(sent) == 3
    assert result.tx_signature == "0xsellhash3"  # the LAST send is the actual swap


def test_sell_skips_approvals_already_sufficient(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    huge = 2 ** 150
    sent, _ = _make_harness(monkeypatch, account, erc20_allowance=huge, permit2_allowance=huge)

    result = swap_executor.execute_sell("robinhood_chain", TOKEN, amount_tokens=100.0, reason="test sell")

    assert result.ok, result.reason
    assert len(sent) == 1  # only the swap itself
    assert result.tx_signature == "0xsellhash1"


def test_sell_computes_filled_usd_from_real_native_balance_diff(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    huge = 2 ** 150
    _make_harness(monkeypatch, account, erc20_allowance=huge, permit2_allowance=huge,
                   dexscreener_price_usd=3.0, dexscreener_price_native=1.0)

    result = swap_executor.execute_sell("robinhood_chain", TOKEN, amount_tokens=100.0, reason="test sell")

    assert result.ok, result.reason
    # balance rose by 0.5 native RHC ether (10.0 -> 10.5), plus real gas
    # spent (21000 gas * 1 gwei) added back in -- at $3.00/native, that's
    # ~$1.50 plus a tiny (~$0.0000000063) gas correction, not a round number.
    expected_native = 0.5 + (21000 * 1e9) / 1e18
    assert result.filled_usd == pytest.approx(expected_native * 3.0, rel=1e-6)

    log = state.get_trade_log(limit=5)
    assert len(log) == 1
    assert log[0]["side"] == "sell" and log[0]["ok"] is True


def test_sell_refuses_when_no_pool_found(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())
    huge = 2 ** 150
    sent, _ = _make_harness(monkeypatch, account, erc20_allowance=huge, permit2_allowance=huge)

    import executor.rhc_pool_discovery as rhc_pool_discovery

    def fake_rpc_call_no_pool(chain, method, params):
        if method == "eth_blockNumber":
            return {"ok": True, "result": hex(2000)}
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith(DECIMALS_SELECTOR):
                return {"ok": True, "result": "0x" + format(18, "064x")}
            if data.startswith(ERC20_ALLOWANCE_SELECTOR):
                return {"ok": True, "result": "0x" + format(huge, "064x")}
            if data.startswith(PERMIT2_ALLOWANCE_SELECTOR):
                return {"ok": True, "result": _encode_uint160_uint48_uint48(huge, 0, 0)}
        if method == "eth_getLogs":
            return {"ok": True, "result": []}  # no pool ever found
        raise AssertionError(method)

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call_no_pool)
    monkeypatch.setattr(rhc_pool_discovery, "rpc_call", fake_rpc_call_no_pool)

    result = swap_executor.execute_sell("robinhood_chain", TOKEN, amount_tokens=100.0, reason="test sell")
    assert result.ok is False
    assert "pool" in result.reason.lower()


def test_sell_refuses_when_erc20_approve_fails_to_confirm(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "rhc_private_key", account.key.hex())

    def fake_get_json(url, params=None, **kw):
        return {"ok": True, "json": [
            {"quoteToken": {"address": NATIVE_CURRENCY}, "priceUsd": "2.0", "priceNative": "1.0",
             "liquidity": {"usd": 50000}},
        ]}
    monkeypatch.setattr(swap_executor, "get_json", fake_get_json)

    def fake_rpc_call(chain, method, params):
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith(DECIMALS_SELECTOR):
                return {"ok": True, "result": "0x" + format(18, "064x")}
            if data.startswith(ERC20_ALLOWANCE_SELECTOR):
                return {"ok": True, "result": "0x" + format(0, "064x")}  # needs approval
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x1"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xapprovehash1"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": None}  # never confirms
        raise AssertionError(method)

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_sell("robinhood_chain", TOKEN, amount_tokens=100.0, reason="test sell")
    assert result.ok is False
    assert "approve" in result.reason.lower()
    assert "swap NOT" in result.reason or "not attempted" in result.reason.lower()

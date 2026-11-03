"""
Covers the Base chain execution path built 3 Nov 2026 (Ali): execute_buy_base,
_sell_base, _eth_price_usd, and the wiring that makes Base a real,
executable chain for the first time (compound_scalper._BUY_FN,
entrypoint._BUY_FUNCTIONS, executor.triggers.EXECUTABLE_CHAINS,
executor.config.ExecutorConfig.ready_for_chain, executor.sellability.check_sellable).

Before this, Base was deliberately paper-only -- triggers.py's own Sept 30 2026
comment documents why (a real bug: Base alerts fired and committed budget with
no buy function to spend it, so the budget stayed stuck forever). This file's
job is to prove the newly-added buy path actually behaves like the already-proven
BSC path it was modeled on, using the SAME mocking discipline as
tests/test_swap_executor_fills.py and tests/test_rhc_buy.py: everything here is
mocked -- no live RPC, no real key, no real money.
"""
import pytest

import state
import executor.swap_executor as swap_executor
from executor.config import EXECUTOR_CONFIG


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))


TOKEN = "0x" + "cd" * 20


def test_ready_for_chain_base_requires_key_and_execution_enabled():
    from executor.config import ExecutorConfig
    c = ExecutorConfig(execution_enabled=False, base_private_key="x")
    assert c.ready_for_chain("base") is False
    c2 = ExecutorConfig(execution_enabled=True, base_private_key=None)
    assert c2.ready_for_chain("base") is False
    c3 = ExecutorConfig(execution_enabled=True, base_private_key="0x" + "11" * 32)
    assert c3.ready_for_chain("base") is True


def test_base_is_now_a_real_buy_dispatch_target():
    """The Sept 30 2026 bug (Base alerts fired with no buy function to spend
    the committed budget) is only actually fixed if base is wired into both
    real dispatchers, not just swap_executor itself."""
    from executor import compound_scalper, entrypoint, triggers
    assert compound_scalper._BUY_FN.get("base") is swap_executor.execute_buy_base
    assert entrypoint._BUY_FUNCTIONS.get("base") is swap_executor.execute_buy_base
    assert "base" in triggers.EXECUTABLE_CHAINS


def test_check_sellable_now_runs_goplus_for_base(monkeypatch):
    from executor import sellability
    calls = []

    def fake_goplus(chain, token):
        calls.append(chain)
        return {"ok": True, "data": {}}

    ok, why = sellability.check_sellable("base", TOKEN, goplus_fn=fake_goplus)
    assert calls == ["base"]  # previously base was excluded entirely -- this proves it now runs
    assert ok


def test_execute_buy_base_refuses_without_execution_enabled(monkeypatch):
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", False)
    result = swap_executor.execute_buy_base(TOKEN, usd_amount=5.0)
    assert not result.ok
    assert "inert" in result.reason.lower() or "not" in result.reason.lower()


def test_execute_buy_base_refuses_when_no_price_available(monkeypatch):
    from eth_account import Account
    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "base_private_key", account.key.hex())
    monkeypatch.setattr(swap_executor, "check_sellable", lambda *a, **k: (True, "ok"), raising=False)

    from executor import sellability
    monkeypatch.setattr(sellability, "check_sellable", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(swap_executor, "_eth_price_usd", lambda: None)

    result = swap_executor.execute_buy_base(TOKEN, usd_amount=5.0)
    assert not result.ok
    assert "price" in result.reason.lower()


def test_execute_buy_base_end_to_end(monkeypatch):
    """Mirrors test_swap_executor_fills's BSC sell test and test_rhc_buy's RHC
    buy test: full mocked happy path through nonce -> gas -> sign -> send ->
    confirm, proving the wiring is correct end to end, not just that each
    piece exists."""
    from web3 import Web3
    from eth_account import Account

    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "base_private_key", account.key.hex())

    from executor import sellability
    monkeypatch.setattr(sellability, "check_sellable", lambda *a, **k: (True, "no red flags"))
    monkeypatch.setattr(swap_executor, "_eth_price_usd", lambda: 2500.0)  # fake ETH/USD

    w3 = Web3()

    def encode_uint256_array(values):
        return w3.codec.encode(["uint256[]"], [values]).hex()

    def fake_rpc_call(chain, method, params):
        assert chain == "base"
        if method == "eth_call":
            calldata = params[0]["data"]
            if calldata.startswith("0x" + Web3.keccak(text="getAmountsOut(uint256,address[])").hex()[:8]):
                # amounts[0]=amountIn (echoed), amounts[-1]=token out
                return {"ok": True, "result": "0x" + encode_uint256_array([10**15, 500 * 10**9])}
            raise AssertionError(f"unexpected eth_call calldata: {calldata}")
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x5"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}  # 1 gwei
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xbasebuyhash1"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_buy_base(TOKEN, usd_amount=5.0)
    assert result.ok, result.reason
    assert result.tx_signature == "0xbasebuyhash1"
    assert result.filled_usd == 5.0


def test_execute_sell_dispatches_to_sell_base(monkeypatch):
    """execute_sell's chain-router must actually reach _sell_base for chain=='base'
    -- this is the exact class of bug that left Base's Sept 30 budget stuck
    forever (right chain name, no matching branch)."""
    called = {}

    def fake_sell_base(token_address, amount_tokens):
        called["args"] = (token_address, amount_tokens)
        return swap_executor.ExecutionResult(True, "ok", tx_signature="0xsellhash")

    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "base_private_key", "0x" + "22" * 32)
    monkeypatch.setattr(swap_executor, "_sell_base", fake_sell_base)

    result = swap_executor.execute_sell("base", TOKEN, amount_tokens=123.0, reason="test")
    assert result.ok
    assert called["args"] == (TOKEN, 123.0)


def test_base_router_and_weth_addresses_are_valid_checksummed_evm_addresses():
    """Sanity check against the exact class of bug the BSC router address had
    (Sept 25 2026: missing trailing hex char -> invalid address, every BSC
    buy would have failed). Confirms these 40-hex-char addresses are at
    least well-formed, even though full on-chain correctness can't be
    verified from this build environment."""
    from web3 import Web3
    for addr in (swap_executor.UNISWAP_V2_ROUTER_BASE, swap_executor.WETH_BASE, swap_executor.USDC_BASE):
        assert Web3.is_address(addr), f"not a valid EVM address: {addr}"
    assert swap_executor.BASE_CHAIN_ID == 8453

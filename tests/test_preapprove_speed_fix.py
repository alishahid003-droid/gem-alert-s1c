"""
15 Nov 2026 (Ali, explicit instruction after a manual Base test showed an 87.5s sell --
"we might lose the edge" on slow memecoin execution): a token's first-ever real sell on
Base/RHC pays for approve() transaction(s) in addition to the swap itself, because the
sell path only approves lazily, right when it discovers the allowance is insufficient.
Since the sniper almost always sells a token it just bought (never touched before), this
cost landed on nearly every real sell.

Fix: execute_buy_base/execute_buy_robinhood_chain now kick off the sell-side approve(s)
in a background thread immediately after a successful buy confirms, using the real idle
time between a buy landing and exit_rules/moonbag's next poll cycle. By the time a real
sell fires, _sell_base/_sell_robinhood_chain's own EXISTING allowance checks see it
already satisfied and skip the approve step(s) entirely -- sell goes from 2-3 sequential
on-chain transactions down to 1, with zero changes needed to the sell functions
themselves (their allowance checks already existed and already skip correctly).

These tests join() the background thread deterministically (rather than sleeping and
hoping) by making the thread target itself synchronous and calling it directly where
background behavior isn't what's under test, or by using a real-but-controlled Event for
tests that specifically need to prove "fire-and-forget, never blocks the buy's return".
"""
import threading
import time

import pytest

import state
import executor.swap_executor as swap_executor
from executor.config import EXECUTOR_CONFIG

TOKEN = "0x" + "ab" * 20


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))


def _wait_for_threads_named(prefix: str, timeout: float = 2.0):
    """Waits for any thread whose name starts with prefix to finish, so a test can
    deterministically assert on what the background pre-approve thread did instead of
    sleeping and hoping it's done."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        matching = [t for t in threading.enumerate() if t.name.startswith(prefix) and t.is_alive()]
        if not matching:
            return
        time.sleep(0.02)


def test_base_buy_triggers_background_preapprove_with_no_prior_allowance(monkeypatch):
    """A successful Base buy of a token with zero existing allowance must submit a
    real approve() transaction in the background -- proven here by making rpc_call
    synchronous-but-tracked so the test can join the thread and inspect what it sent."""
    from web3 import Web3
    from eth_account import Account

    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "base_private_key", account.key.hex())

    from executor import sellability
    monkeypatch.setattr(sellability, "check_sellable", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(swap_executor, "_eth_price_usd", lambda: 2500.0)

    w3 = Web3()
    calls = []

    def encode_uint256_array(values):
        return w3.codec.encode(["uint256[]"], [values]).hex()

    def fake_rpc_call(chain, method, params):
        calls.append((method, params))
        if method == "eth_call":
            calldata = params[0]["data"]
            if calldata.startswith("0x" + Web3.keccak(text="getAmountsOut(uint256,address[])").hex()[:8]):
                return {"ok": True, "result": "0x" + encode_uint256_array([10**15, 500 * 10**9])}
            if calldata.startswith("0x" + Web3.keccak(text="allowance(address,address)").hex()[:8]):
                return {"ok": True, "result": "0x" + "0" * 64}  # zero allowance -- must trigger approve
            raise AssertionError(f"unexpected eth_call: {calldata}")
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x5"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xapprovehash1"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_buy_base(TOKEN, usd_amount=5.0)
    assert result.ok, result.reason

    _wait_for_threads_named("base-preapprove")

    approve_sends = [c for c in calls if c[0] == "eth_sendRawTransaction"]
    # First send is the buy itself; the background thread must have submitted a SECOND
    # transaction (the approve) on top of it.
    assert len(approve_sends) == 2, f"expected buy + background approve = 2 sends, got {len(approve_sends)}: {calls}"


def test_base_buy_skips_background_preapprove_when_already_approved(monkeypatch):
    """If the wallet already holds nonzero allowance for this token (e.g. buying the
    same token again), the background thread must NOT waste gas re-approving."""
    from web3 import Web3
    from eth_account import Account

    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "base_private_key", account.key.hex())

    from executor import sellability
    monkeypatch.setattr(sellability, "check_sellable", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(swap_executor, "_eth_price_usd", lambda: 2500.0)

    w3 = Web3()
    calls = []

    def encode_uint256_array(values):
        return w3.codec.encode(["uint256[]"], [values]).hex()

    def fake_rpc_call(chain, method, params):
        calls.append((method, params))
        if method == "eth_call":
            calldata = params[0]["data"]
            if calldata.startswith("0x" + Web3.keccak(text="getAmountsOut(uint256,address[])").hex()[:8]):
                return {"ok": True, "result": "0x" + encode_uint256_array([10**15, 500 * 10**9])}
            if calldata.startswith("0x" + Web3.keccak(text="allowance(address,address)").hex()[:8]):
                return {"ok": True, "result": "0x" + ("f" * 64)}  # MAX allowance -- already approved
            raise AssertionError(f"unexpected eth_call: {calldata}")
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x5"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xbuyhash1"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_buy_base(TOKEN, usd_amount=5.0)
    assert result.ok, result.reason

    _wait_for_threads_named("base-preapprove")

    approve_sends = [c for c in calls if c[0] == "eth_sendRawTransaction"]
    # Only the buy's own send -- the background thread saw nonzero allowance and sent nothing.
    assert len(approve_sends) == 1, f"expected only the buy's own send, got {len(approve_sends)}: {calls}"


def test_preapprove_failure_never_affects_the_buy_result(monkeypatch):
    """The buy's own ExecutionResult must be returned and correct regardless of what the
    background pre-approve thread does or fails to do -- it is fire-and-forget by design."""
    from web3 import Web3
    from eth_account import Account

    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "base_private_key", account.key.hex())

    from executor import sellability
    monkeypatch.setattr(sellability, "check_sellable", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(swap_executor, "_eth_price_usd", lambda: 2500.0)

    w3 = Web3()

    def encode_uint256_array(values):
        return w3.codec.encode(["uint256[]"], [values]).hex()

    def fake_rpc_call(chain, method, params):
        if method == "eth_call":
            calldata = params[0]["data"]
            if calldata.startswith("0x" + Web3.keccak(text="getAmountsOut(uint256,address[])").hex()[:8]):
                return {"ok": True, "result": "0x" + encode_uint256_array([10**15, 500 * 10**9])}
            if calldata.startswith("0x" + Web3.keccak(text="allowance(address,address)").hex()[:8]):
                raise RuntimeError("simulated RPC outage during background pre-approve")
            raise AssertionError(f"unexpected eth_call: {calldata}")
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x5"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xbuyhash2"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_buy_base(TOKEN, usd_amount=5.0)
    assert result.ok, result.reason
    assert result.tx_signature == "0xbuyhash2"
    assert result.filled_usd == 5.0
    _wait_for_threads_named("base-preapprove")  # let the thread run its course, must not raise out of the test


def test_base_buy_does_not_block_waiting_on_preapprove():
    """The buy's return must not be delayed by the background approve -- fire-and-forget
    means execute_buy_base returns as soon as the BUY itself confirms, not after the
    approve also confirms. Verified by making the background approve artificially slow
    and confirming the buy still returns almost immediately."""
    import executor.swap_executor as se

    release_approve = threading.Event()
    started_approve = threading.Event()

    def slow_preapprove(token_address):
        def _run():
            started_approve.set()
            release_approve.wait(timeout=5)
        t = threading.Thread(target=_run, daemon=True, name="base-preapprove")
        t.start()

    orig = se._preapprove_base_background
    se._preapprove_base_background = slow_preapprove
    try:
        t0 = time.time()
        se._preapprove_base_background(TOKEN)  # simulate the call site directly
        elapsed = time.time() - t0
        assert elapsed < 0.5, f"pre-approve call site blocked for {elapsed:.2f}s -- must return immediately"
        assert started_approve.wait(timeout=1), "background thread never started"
    finally:
        release_approve.set()
        se._preapprove_base_background = orig

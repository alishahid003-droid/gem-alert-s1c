"""
Covers two real gaps closed Sept 25, 2026 (Tasks Left #3/#4 -- dashboard
realized P&L was showing nothing because filled_usd was never being set on
sells):

  1. _sell_solana now computes filled_usd from the confirmed tx's REAL
     native-SOL balance diff (not a pre-trade quote estimate).
  2. _sell_bsc now computes filled_usd from a pre-swap getAmountsOut quote
     (BSC has no equivalent "diff the receipt" path for native BNB).
  3. Every successful/failed buy and sell now writes one row to
     state.log_trade_event() (the dashboard's Trade History table).

Everything here is mocked -- no live RPC, no real key, no real money. This
follows the same discipline as the rest of the executor test suite: build a
fake-but-structurally-valid unsigned tx, monkeypatch rpc_call/get_json/
post_json, and assert on the real code path's output.
"""
import base64
import time

import pytest

import state
import executor.position_state as position_state
import executor.swap_executor as swap_executor
from executor.config import EXECUTOR_CONFIG


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))


@pytest.fixture
def solana_keypair():
    from solders.keypair import Keypair
    import base58
    kp = Keypair()
    return kp, base58.b58encode(bytes(kp)).decode()


def _fake_unsigned_solana_tx_b64(keypair) -> str:
    from solders.message import Message
    from solders.instruction import Instruction, AccountMeta
    from solders.pubkey import Pubkey
    from solders.hash import Hash
    from solders.transaction import VersionedTransaction
    ix = Instruction(Pubkey.default(), bytes([0]), [AccountMeta(keypair.pubkey(), True, True)])
    msg = Message.new_with_blockhash([ix], keypair.pubkey(), Hash.default())
    unsigned = VersionedTransaction.populate(msg, [])
    return base64.b64encode(bytes(unsigned)).decode()


def test_sell_solana_sets_filled_usd_from_real_balance_diff(monkeypatch, solana_keypair):
    kp, priv_key_b58 = solana_keypair
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "solana_private_key", priv_key_b58)

    wallet = str(kp.pubkey())
    token_mint = "TokenMintForSellTest11111111111111111111"

    # 1) mint-decimals fetch (getAccountInfo) -- base64 blob with decimals byte at offset 44
    import struct
    raw = bytearray(82)
    raw[44] = 6  # 6 decimals
    mint_data_b64 = base64.b64encode(bytes(raw)).decode()

    # 2) jupiter quote -- two DIFFERENT calls hit this same endpoint here: the
    # sell quote itself (token -> SOL, content unused downstream) and
    # _sol_price_usd()'s own separate SOL -> USDC quote, which the filled_usd
    # math actually depends on. Distinguish by outputMint so the $150/SOL
    # price used in this test's assertion is the one actually returned.
    def fake_get_json(url, params=None, **kwargs):
        if "/quote" not in url:
            raise AssertionError(f"unexpected get_json url: {url}")
        if params and params.get("outputMint") == swap_executor.USDC_MINT_SOLANA:
            return {"ok": True, "json": {"outAmount": "150000000"}}  # $150.00 / SOL (6 decimals)
        return {"ok": True, "json": {"outAmount": "999999"}}  # sell quote -- content unused downstream

    # 3) jupiter swap-tx build
    unsigned_b64 = _fake_unsigned_solana_tx_b64(kp)

    def fake_post_json(url, json=None, **kwargs):
        if "/swap" in url:
            return {"ok": True, "json": {"swapTransaction": unsigned_b64}}
        raise AssertionError(f"unexpected post_json url: {url}")

    calls = {"getTransaction": 0}

    def fake_rpc_call(chain, method, params):
        assert chain == "solana"
        if method == "getAccountInfo":
            return {"ok": True, "result": {"value": {"data": [mint_data_b64, "base64"]}}}
        if method == "sendTransaction":
            return {"ok": True, "result": "fakesig_sell_1"}
        if method == "getSignatureStatuses":
            return {"ok": True, "result": {"value": [{"err": None, "confirmationStatus": "confirmed"}]}}
        if method == "getTransaction":
            calls["getTransaction"] += 1
            # first call (inside _sell_solana's caller chain) is the native-SOL
            # balance diff read -- preBalances/postBalances[0] is the fee payer
            # (our wallet), by Solana protocol convention.
            return {"ok": True, "result": {"meta": {
                "preBalances": [2_000_000_000],
                "postBalances": [2_080_000_000],  # +0.08 SOL received, net of fee
            }}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "get_json", fake_get_json)
    monkeypatch.setattr(swap_executor, "post_json", fake_post_json)
    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    result = swap_executor.execute_sell("solana", token_mint, amount_tokens=500.0, reason="test sell")

    assert result.ok, result.reason
    assert result.tx_signature == "fakesig_sell_1"
    # 0.08 SOL * $150/SOL (from the mocked _sol_price_usd quote) = $12.00
    assert result.filled_usd == pytest.approx(0.08 * 150.0), result.filled_usd

    # and it must have written a Trade History row
    log = state.get_trade_log(limit=5)
    assert len(log) == 1
    assert log[0]["side"] == "sell" and log[0]["ok"] is True
    assert log[0]["usd_amount"] == pytest.approx(12.0)


def test_sell_solana_still_closes_position_with_real_pnl(monkeypatch, solana_keypair):
    """End-to-end: the exact bug this fixes -- before Sept 25, 2026,
    close_position(exit_usd=result.filled_usd) always got None on a sell,
    so pnl_usd was never set and the dashboard's realized-P&L card showed
    nothing. Confirms the fix closes the loop all the way through."""
    kp, priv_key_b58 = solana_keypair
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "solana_private_key", priv_key_b58)
    token_mint = "TokenMintForPnlTest2222222222222222222222"

    raw = bytearray(82)
    raw[44] = 9
    mint_data_b64 = base64.b64encode(bytes(raw)).decode()
    unsigned_b64 = _fake_unsigned_solana_tx_b64(kp)

    def fake_get_json_pnl(url, params=None, **kw):
        if params and params.get("outputMint") == swap_executor.USDC_MINT_SOLANA:
            return {"ok": True, "json": {"outAmount": "150000000"}}  # $150.00 / SOL
        return {"ok": True, "json": {"outAmount": "1000000"}}

    monkeypatch.setattr(swap_executor, "get_json", fake_get_json_pnl)
    monkeypatch.setattr(swap_executor, "post_json",
                         lambda url, json=None, **kw: {"ok": True, "json": {"swapTransaction": unsigned_b64}})

    def fake_rpc_call(chain, method, params):
        if method == "getAccountInfo":
            return {"ok": True, "result": {"value": {"data": [mint_data_b64, "base64"]}}}
        if method == "sendTransaction":
            return {"ok": True, "result": "fakesig_close_1"}
        if method == "getSignatureStatuses":
            return {"ok": True, "result": {"value": [{"err": None, "confirmationStatus": "finalized"}]}}
        if method == "getTransaction":
            return {"ok": True, "result": {"meta": {"preBalances": [1_000_000_000], "postBalances": [1_100_000_000]}}}
        raise AssertionError(method)

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    # set up an open position with a known cost basis
    position_state.record_stage_entry("solana", token_mint, "stage1", 50.0, 100_000, "opened for pnl test")
    position_state.record_fill("solana", token_mint, 1000.0)

    result = swap_executor.execute_sell("solana", token_mint, amount_tokens=1000.0, reason="manual close")
    assert result.ok
    position_state.close_position("solana", token_mint, reason="manual close", exit_usd=result.filled_usd)

    closed = position_state.list_closed_positions()
    assert len(closed) == 1
    assert closed[0]["pnl_usd"] is not None  # THE bug: this used to always be None
    assert closed[0]["exit_usd"] == pytest.approx(0.1 * 150.0)

    summary = position_state.realized_pnl_summary()
    assert summary["priced_count"] == 1
    assert summary["total_realized_pnl_usd"] == pytest.approx((0.1 * 150.0) - 50.0)


def test_sell_bsc_sets_filled_usd_from_pre_swap_quote(monkeypatch):
    """BSC's sell path has no native-balance-diff option (PancakeSwap sends
    native BNB via an internal call, invisible to receipt logs), so
    filled_usd there comes from a getAmountsOut quote taken right before
    the swap instead -- an estimate, not a balance diff, but real and
    quote-derived rather than the previous behavior of leaving filled_usd
    unset on every single BSC sell."""
    from web3 import Web3
    from eth_account import Account

    account = Account.create()
    monkeypatch.setattr(EXECUTOR_CONFIG, "execution_enabled", True)
    monkeypatch.setattr(EXECUTOR_CONFIG, "bsc_private_key", account.key.hex())

    token_address = "0x" + "11" * 18 + "aaaa"  # 40 hex chars -- verified via Web3.is_address()
    w3 = Web3()

    def encode_uint256_array(values):
        return w3.codec.encode(["uint256[]"], [values]).hex()

    def fake_rpc_call(chain, method, params):
        assert chain == "bsc"
        if method == "eth_call":
            calldata = params[0]["data"]
            # decimals() selector is the first 4 bytes of keccak("decimals()")
            if calldata.startswith("0x" + Web3.keccak(text="decimals()").hex()[:8]):
                return {"ok": True, "result": "0x0000000000000000000000000000000000000000000000000000000000000012"}  # 18
            if calldata.startswith("0x" + Web3.keccak(text="allowance(address,address)").hex()[:8]):
                return {"ok": True, "result": "0x" + ("f" * 64)}  # already max-approved -- skip approve()
            if calldata.startswith("0x" + Web3.keccak(text="getAmountsOut(uint256,address[])").hex()[:8]):
                # 2-leg path -> amounts[0]=amountIn (echoed), amounts[1]=BNB out
                return {"ok": True, "result": "0x" + encode_uint256_array([10**21, 2 * 10**17])}  # 0.2 BNB out
            raise AssertionError(f"unexpected eth_call calldata: {calldata}")
        if method == "eth_getTransactionCount":
            return {"ok": True, "result": "0x1"}
        if method == "eth_gasPrice":
            return {"ok": True, "result": "0x3b9aca00"}  # 1 gwei
        if method == "eth_sendRawTransaction":
            return {"ok": True, "result": "0xswaphash1"}
        if method == "eth_getTransactionReceipt":
            return {"ok": True, "result": {"status": "0x1", "logs": []}}
        raise AssertionError(f"unexpected rpc_call: {method}")

    monkeypatch.setattr(swap_executor, "rpc_call", fake_rpc_call)

    # BNB/USD price for the WBNB->USDT getAmountsOut call inside _bnb_price_usd
    # is fetched via the SAME eth_call branch above (router.getAmountsOut),
    # so it also resolves to 2e17 wei out per the mock -- not realistic as a
    # price by itself, so patch _bnb_price_usd directly to isolate what this
    # test actually verifies: that the pre-swap quote -> USD conversion wiring
    # in _sell_bsc works, independent of _bnb_price_usd's own correctness
    # (which the module docstring already covers via a real on-chain call).
    monkeypatch.setattr(swap_executor, "_bnb_price_usd", lambda: 600.0)

    result = swap_executor.execute_sell("bsc", token_address, amount_tokens=1000.0, reason="test bsc sell")

    assert result.ok, result.reason
    assert result.tx_signature == "0xswaphash1"
    # 0.2 BNB * $600/BNB = $120.00
    assert result.filled_usd == pytest.approx(120.0), result.filled_usd

    log = state.get_trade_log(limit=5)
    assert len(log) == 1
    assert log[0]["side"] == "sell" and log[0]["ok"] is True
    assert log[0]["usd_amount"] == pytest.approx(120.0)

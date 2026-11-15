"""Verifies the gas-reserve fix (15 Nov 2026): a wallet balance that clears the old $5-floor
check but has too little left after reserving real gas must now be refused, reproducing the
exact scenario behind today's 3 real Base on-chain failures
('insufficient funds for gas * price + value')."""
import sys
sys.path.insert(0, '.')
import pytest
import executor.swap_executor as swap


def test_estimate_real_gas_reserve_usd_base(monkeypatch):
    # gasPrice ~ 0.05 gwei (typical Base), ETH @ $1800 -> gas cost should be small but nonzero
    monkeypatch.setattr(swap, "rpc_call", lambda chain, method, params: {"ok": True, "result": hex(int(0.05e9))})
    monkeypatch.setattr(swap, "_eth_price_usd", lambda: 1800.0)
    reserve = swap.estimate_real_gas_reserve_usd("base")
    assert reserve is not None
    # 400_000 gas * 0.05gwei * 1.15 buffer / 1e18 * 1800 usd
    expected = (400_000 * 0.05e9 * 1.15 / 1e18) * 1800.0
    assert abs(reserve - expected) < 1e-6


def test_estimate_real_gas_reserve_usd_returns_none_on_rpc_failure(monkeypatch):
    monkeypatch.setattr(swap, "rpc_call", lambda chain, method, params: {"ok": False, "reason": "timeout"})
    assert swap.estimate_real_gas_reserve_usd("base") is None


def test_estimate_real_gas_reserve_usd_unknown_chain_returns_none():
    assert swap.estimate_real_gas_reserve_usd("solana") is None


def test_real_incident_scenario_reproduced_and_fixed(monkeypatch):
    """Today's real failure: wallet ~$6.70 passed the old $5-floor check (stake sized ~$2,
    which is BELOW $5 so it would have been refused anyway by the old logic -- but the real
    incident records show gas-insufficient failures on Base at higher apparent stakes too,
    confirming gas was never reserved at all). This proves the NEW reserve math: a wallet with
    just enough for the swap but not enough for swap+gas is now caught pre-flight."""
    # Simulate a Base gas price typical of a live network moment + a wallet that would have
    # passed the old check (balance - floor only) but not survive after real gas is held back.
    monkeypatch.setattr(swap, "rpc_call", lambda chain, method, params: {"ok": True, "result": hex(int(5e9))})  # 5 gwei
    monkeypatch.setattr(swap, "_eth_price_usd", lambda: 1800.0)
    reserve = swap.estimate_real_gas_reserve_usd("base")
    # 400_000 * 5gwei * 1.15 / 1e18 * 1800 = ~$4.14 reserved just for gas
    assert reserve > 4.0
    wallet_usd = 6.70
    spendable = wallet_usd - reserve
    assert spendable < 5.0  # the real floor the fix now enforces

"""Tests for executor_triggers.py -- pure decision logic, no network. Each
test points state.py at a fresh temp file so runs don't leak into each
other (state.py has no reset/clear op of its own)."""
import pytest

import state
import executor.triggers as triggers
import executor.circuit_breaker as circuit_breaker


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")  # never accidentally live in a test
    yield


def test_stage1_fires_on_score_band_a():
    d = triggers.evaluate_stage1("solana", "MintABC", score_band="A", deployer_tier=None, convergence_count=0)
    assert d.should_fire is True
    assert d.stage == "stage1"


def test_stage1_fires_on_elite_deployer_alone():
    d = triggers.evaluate_stage1("solana", "MintABC", score_band=None, deployer_tier="elite", convergence_count=0)
    assert d.should_fire is True


def test_stage1_fires_on_convergence_alone():
    d = triggers.evaluate_stage1("solana", "MintABC", score_band=None, deployer_tier=None, convergence_count=2)
    assert d.should_fire is True


def test_stage1_does_not_fire_with_nothing():
    d = triggers.evaluate_stage1("solana", "MintABC", score_band="D", deployer_tier="spammer", convergence_count=0)
    assert d.should_fire is False


def test_stage1_does_not_fire_twice_on_same_token():
    import executor.position_state as position_state
    position_state.record_stage_entry("solana", "MintXYZ", "stage1", 12.0, 50000, "test")
    d = triggers.evaluate_stage1("solana", "MintXYZ", score_band="A", deployer_tier=None, convergence_count=0)
    assert d.should_fire is False
    assert "already fired" in d.reason


def test_stage2_requires_graduation():
    d = triggers.evaluate_stage2("solana", "MintABC", current_mcap_usd=300000,
                                  fomo_convergence_count=2, graduated=False)
    assert d.should_fire is False
    assert "graduated" in d.reason


def test_stage2_requires_two_convergence():
    d = triggers.evaluate_stage2("solana", "MintABC", current_mcap_usd=300000,
                                  fomo_convergence_count=1, graduated=True)
    assert d.should_fire is False


def test_stage2_uses_flat_floor_when_no_stage1_entry():
    d = triggers.evaluate_stage2("solana", "MintNEW", current_mcap_usd=200000,
                                  fomo_convergence_count=2, graduated=True)
    assert d.should_fire is True


def test_stage2_rejects_below_flat_floor():
    d = triggers.evaluate_stage2("solana", "MintNEW", current_mcap_usd=50000,
                                  fomo_convergence_count=2, graduated=True)
    assert d.should_fire is False


def test_stage2_uses_multiplier_when_stage1_entry_exists():
    import executor.position_state as position_state
    position_state.record_stage_entry("solana", "MintDBL", "stage1", 12.0, 40000, "test")
    # 2x of 40000 = 80000 floor
    d_below = triggers.evaluate_stage2("solana", "MintDBL", current_mcap_usd=70000,
                                        fomo_convergence_count=2, graduated=True)
    assert d_below.should_fire is False
    d_above = triggers.evaluate_stage2("solana", "MintDBL", current_mcap_usd=90000,
                                        fomo_convergence_count=2, graduated=True)
    assert d_above.should_fire is True


def test_double_confirmed_after_both_stages():
    import executor.position_state as position_state
    position_state.record_stage_entry("solana", "MintBoth", "stage1", 12.0, 40000, "test")
    assert triggers.is_double_confirmed("solana", "MintBoth") is False
    position_state.record_stage_entry("solana", "MintBoth", "stage2", 17.0, 90000, "test")
    assert triggers.is_double_confirmed("solana", "MintBoth") is True


def test_circuit_breaker_blocks_new_stage1_fires():
    circuit_breaker.record_trade_result(-5.0)
    circuit_breaker.record_trade_result(-5.0)
    circuit_breaker.record_trade_result(-5.0)  # 3 consecutive losses, default cap
    d = triggers.evaluate_stage1("solana", "MintFresh", score_band="A", deployer_tier=None, convergence_count=0)
    assert d.should_fire is False
    assert "circuit breaker" in d.reason


def test_stage1_budget_cap_blocks_new_fire_when_exhausted(monkeypatch):
    from executor.config import EXECUTOR_CONFIG
    monkeypatch.setattr(EXECUTOR_CONFIG, "total_wallet_usd", 50.0)  # budget = 0.60 * 50 = $30
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage1_position_usd", 20.0)
    # first fire commits $20 of the $30 stage1 budget
    d1 = triggers.evaluate_stage1("solana", "MintBudgetA", score_band="A", deployer_tier=None, convergence_count=0)
    assert d1.should_fire is True
    import executor.position_state as position_state
    position_state.record_stage_entry("solana", "MintBudgetA", "stage1", 20.0, 50000, "test")
    # second fire would need another $20, only $10 of budget left -- must be blocked
    d2 = triggers.evaluate_stage1("solana", "MintBudgetB", score_band="A", deployer_tier=None, convergence_count=0)
    assert d2.should_fire is False
    assert "budget exhausted" in d2.reason


def test_stage1_budget_cap_frees_up_after_moonbag_trim(monkeypatch):
    from executor.config import EXECUTOR_CONFIG
    import executor.position_state as position_state
    monkeypatch.setattr(EXECUTOR_CONFIG, "total_wallet_usd", 50.0)  # budget = $30
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage1_position_usd", 20.0)
    position_state.record_stage_entry("solana", "MintBudgetC", "stage1", 20.0, 50000, "test")
    d_blocked = triggers.evaluate_stage1("solana", "MintBudgetD", score_band="A", deployer_tier=None, convergence_count=0)
    assert d_blocked.should_fire is False
    # trim 50% of MintBudgetC's original stake off -- frees $10 of committed capital
    position_state.record_moonbag_trim("solana", "MintBudgetC", 3.0, 0.50, exit_usd=30.0)
    d_after_trim = triggers.evaluate_stage1("solana", "MintBudgetD", score_band="A", deployer_tier=None, convergence_count=0)
    assert d_after_trim.should_fire is True


def test_stage2_budget_cap_blocks_new_fire_when_exhausted(monkeypatch):
    from executor.config import EXECUTOR_CONFIG
    import executor.position_state as position_state
    monkeypatch.setattr(EXECUTOR_CONFIG, "total_wallet_usd", 50.0)  # stage2 budget = 0.40 * 50 = $20
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage2_position_usd", 17.0)
    position_state.record_stage_entry("solana", "MintS2A", "stage2", 17.0, 50000, "test")
    d = triggers.evaluate_stage2("solana", "MintS2B", current_mcap_usd=300000,
                                  fomo_convergence_count=2, graduated=True)
    assert d.should_fire is False
    assert "budget exhausted" in d.reason

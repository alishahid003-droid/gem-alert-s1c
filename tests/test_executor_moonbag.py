import pytest

import state
import executor.moonbag as moonbag
import executor.position_state as position_state
from executor.config import EXECUTOR_CONFIG


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")  # never accidentally live in a test
    yield


def _open_position(entry_mcap=100000):
    position_state.record_stage_entry("solana", "MintMoon", "stage1", 12.0, entry_mcap, "test")


def test_no_fire_when_moonbag_disabled(monkeypatch):
    monkeypatch.setattr(EXECUTOR_CONFIG, "moonbag_enabled", False)
    _open_position()
    d = moonbag.evaluate_trim("solana", "MintMoon", current_mcap_usd=500000)
    assert d.should_fire is False
    assert "disabled" in d.reason


def test_no_fire_with_no_open_position():
    d = moonbag.evaluate_trim("solana", "MintNothing", current_mcap_usd=500000)
    assert d.should_fire is False
    assert "no open position" in d.reason


def test_no_fire_without_entry_mcap():
    position_state.record_stage_entry("solana", "MintNoMcap", "stage1", 12.0, None, "test")
    d = moonbag.evaluate_trim("solana", "MintNoMcap", current_mcap_usd=500000)
    assert d.should_fire is False
    assert "no entry_mcap" in d.reason


def test_no_fire_below_first_tier():
    _open_position(entry_mcap=100000)
    d = moonbag.evaluate_trim("solana", "MintMoon", current_mcap_usd=200000)  # 2x, ladder starts at 3x
    assert d.should_fire is False
    assert d.achieved_multiple == pytest.approx(2.0)


def test_fires_first_tier_at_3x():
    _open_position(entry_mcap=100000)
    d = moonbag.evaluate_trim("solana", "MintMoon", current_mcap_usd=350000)  # 3.5x
    assert d.should_fire is True
    assert d.tier_multiple == 3.0
    assert d.pct_of_original == 0.35


def test_does_not_refire_same_tier_twice():
    _open_position(entry_mcap=100000)
    position_state.record_moonbag_trim("solana", "MintMoon", 3.0, 0.35, exit_usd=15.0)
    d = moonbag.evaluate_trim("solana", "MintMoon", current_mcap_usd=350000)  # still only 3.5x
    assert d.should_fire is False
    assert "no untrimmed tier" in d.reason


def test_fires_next_tier_after_first_already_trimmed():
    _open_position(entry_mcap=100000)
    position_state.record_moonbag_trim("solana", "MintMoon", 3.0, 0.35, exit_usd=15.0)
    d = moonbag.evaluate_trim("solana", "MintMoon", current_mcap_usd=1200000)  # 12x
    assert d.should_fire is True
    assert d.tier_multiple == 10.0
    assert d.pct_of_original == 0.25


def test_fires_lowest_untrimmed_tier_even_if_price_jumped_past_several():
    _open_position(entry_mcap=100000)
    d = moonbag.evaluate_trim("solana", "MintMoon", current_mcap_usd=6000000)  # 60x in one jump
    assert d.should_fire is True
    assert d.tier_multiple == 3.0  # lowest untrimmed rung fires first, one at a time


def test_remaining_pct_decreases_as_tiers_trim():
    _open_position(entry_mcap=100000)
    assert position_state.remaining_pct("solana", "MintMoon") == 1.0
    position_state.record_moonbag_trim("solana", "MintMoon", 3.0, 0.35, exit_usd=15.0)
    assert position_state.remaining_pct("solana", "MintMoon") == pytest.approx(0.65)
    position_state.record_moonbag_trim("solana", "MintMoon", 10.0, 0.25, exit_usd=20.0)
    assert position_state.remaining_pct("solana", "MintMoon") == pytest.approx(0.40)


def test_all_three_tiers_leave_a_20pct_moonbag():
    total_trimmed = sum(pct for _, pct in moonbag.DEFAULT_TRIM_LADDER)
    assert total_trimmed == pytest.approx(0.80)


def test_check_and_trim_does_not_record_when_execution_disabled():
    # EXECUTION_ENABLED=false in this test env -> execute_sell always
    # refuses -> check_and_trim must not record a trim that never happened.
    _open_position(entry_mcap=100000)
    result = moonbag.check_and_trim("solana", "MintMoon", current_mcap_usd=350000)
    assert result is not None
    assert result["sell_result"].ok is False
    assert position_state.remaining_pct("solana", "MintMoon") == 1.0  # nothing actually trimmed


def test_check_and_trim_returns_none_when_no_tier_reached():
    _open_position(entry_mcap=100000)
    result = moonbag.check_and_trim("solana", "MintMoon", current_mcap_usd=150000)  # 1.5x
    assert result is None

import pytest

import state
import executor.moonbag as moonbag
import executor.position_state as position_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    yield


def test_score_weights_elite_deployer():
    assert moonbag.compute_moonshot_score(deployer_tier="elite") == 2
    assert moonbag.compute_moonshot_score(deployer_tier="good") == 1
    assert moonbag.compute_moonshot_score(deployer_tier=None) == 0


def test_score_weights_convergence():
    assert moonbag.compute_moonshot_score(convergence_count=3) == 2
    assert moonbag.compute_moonshot_score(convergence_count=2) == 1
    assert moonbag.compute_moonshot_score(convergence_count=1) == 0


def test_score_double_confirmed_is_the_heaviest_single_weight():
    assert moonbag.compute_moonshot_score(double_confirmed=True) == 3
    assert moonbag.compute_moonshot_score(double_confirmed=True) > moonbag.compute_moonshot_score(deployer_tier="elite")


def test_score_insider_ratio_healthy_vs_heavy():
    assert moonbag.compute_moonshot_score(insider_ratio=0.10) == 1
    assert moonbag.compute_moonshot_score(insider_ratio=0.60) == -2
    assert moonbag.compute_moonshot_score(insider_ratio=0.30) == 0  # in between -- no adjustment either way


def test_score_news_catalyst():
    assert moonbag.compute_moonshot_score(has_news_catalyst=True) == 1


def test_single_strong_signal_alone_does_not_cross_threshold():
    # elite deployer alone (score 2) should NOT be enough on its own --
    # the whole point is corroboration across multiple independent signals
    score = moonbag.compute_moonshot_score(deployer_tier="elite")
    assert moonbag.ladder_name_for_score(score) == "default"


def test_stacked_signals_cross_threshold_to_high_conviction():
    # elite deployer (2) + 3-wallet convergence (2) = 4, hits the threshold
    score = moonbag.compute_moonshot_score(deployer_tier="elite", convergence_count=3)
    assert score == 4
    assert moonbag.ladder_name_for_score(score) == "high_conviction"


def test_double_confirmation_alone_crosses_threshold():
    score = moonbag.compute_moonshot_score(double_confirmed=True, deployer_tier="good")
    assert score == 4
    assert moonbag.ladder_name_for_score(score) == "high_conviction"


def test_heavy_insider_ratio_can_pull_a_borderline_score_back_below_threshold():
    # elite (2) + 2-wallet convergence (1) + heavy insider (-2) = 1 -- stays default
    score = moonbag.compute_moonshot_score(deployer_tier="elite", convergence_count=2, insider_ratio=0.6)
    assert score == 1
    assert moonbag.ladder_name_for_score(score) == "default"


def test_assess_conviction_persists_and_is_used_by_evaluate_trim():
    position_state.record_stage_entry("solana", "MintHighConv", "stage1", 20.0, 100000, "test")
    result = moonbag.assess_conviction("solana", "MintHighConv", deployer_tier="elite", convergence_count=3)
    assert result["ladder"] == "high_conviction"
    assert position_state.get_trim_ladder("solana", "MintHighConv") == "high_conviction"

    trim = moonbag.evaluate_trim("solana", "MintHighConv", current_mcap_usd=350000)  # 3.5x
    assert trim.should_fire is True
    assert trim.pct_of_original == 0.15  # high_conviction ladder's 3x rung, not default's 0.35


def test_no_conviction_call_falls_back_to_default_ladder():
    position_state.record_stage_entry("solana", "MintPlain", "stage1", 20.0, 100000, "test")
    # never called assess_conviction on this one
    trim = moonbag.evaluate_trim("solana", "MintPlain", current_mcap_usd=350000)
    assert trim.should_fire is True
    assert trim.pct_of_original == 0.35  # default ladder's 3x rung


def test_low_conviction_call_uses_default_ladder():
    position_state.record_stage_entry("solana", "MintLowConv", "stage1", 20.0, 100000, "test")
    moonbag.assess_conviction("solana", "MintLowConv", deployer_tier="good")  # score 1, below threshold
    trim = moonbag.evaluate_trim("solana", "MintLowConv", current_mcap_usd=350000)
    assert trim.pct_of_original == 0.35


def test_high_conviction_ladder_leaves_bigger_moonbag_than_default():
    total_default = sum(pct for _, pct in moonbag.DEFAULT_TRIM_LADDER)
    total_high_conviction = sum(pct for _, pct in moonbag.HIGH_CONVICTION_LADDER)
    assert total_high_conviction < total_default
    assert (1 - total_high_conviction) > (1 - total_default)  # bigger moonbag remainder

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
    """Updated Sept 28 2026: a high_conviction position now rides the real
    hard-dollar-target ladder (see compute_hard_target_ladder), not the
    generic 3x/10x/50x HIGH_CONVICTION_LADDER -- so a mere 3.5x no longer
    fires anything; the position must actually reach one of the real
    dollar milestones (350x on a $20 entry, for the default $7,000 level)."""
    position_state.record_stage_entry("solana", "MintHighConv", "stage1", 20.0, 100000, "test")
    result = moonbag.assess_conviction("solana", "MintHighConv", deployer_tier="elite", convergence_count=3)
    assert result["ladder"] == "high_conviction"
    assert position_state.get_trim_ladder("solana", "MintHighConv") == "high_conviction"

    no_fire_yet = moonbag.evaluate_trim("solana", "MintHighConv", current_mcap_usd=350000)  # 3.5x -- nowhere close
    assert no_fire_yet.should_fire is False

    # $20 entry -> first hard-target rung is 7000/20 = 350x.
    trim = moonbag.evaluate_trim("solana", "MintHighConv", current_mcap_usd=100000 * 350)
    assert trim.should_fire is True
    assert trim.pct_of_original == pytest.approx(0.10)  # EXECUTOR_CONFIG.hard_target_trim_pct default
    assert trim.tier_multiple == pytest.approx(350.0)


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


# Hard dollar-target ladder, added Sept 28 2026 (Ali: "hard target these 3
# levels...shouldn't go out quickly rather try and then gain maximum").

def test_compute_hard_target_ladder_scales_multiples_to_entry_size():
    from executor.config import EXECUTOR_CONFIG
    rungs = moonbag.compute_hard_target_ladder(entry_usd=20.0)
    assert len(rungs) == 3
    levels = sorted(EXECUTOR_CONFIG.hard_target_usd_levels)
    for (multiple, pct), level in zip(rungs, levels):
        assert multiple == pytest.approx(level / 20.0)
        assert pct == pytest.approx(EXECUTOR_CONFIG.hard_target_trim_pct)

    # A bigger entry needs smaller multiples to reach the same dollar levels.
    bigger_rungs = moonbag.compute_hard_target_ladder(entry_usd=50.0)
    assert bigger_rungs[0][0] < rungs[0][0]


def test_compute_hard_target_ladder_leaves_a_real_moonbag_past_the_top_level():
    rungs = moonbag.compute_hard_target_ladder(entry_usd=20.0)
    trimmed_total = sum(pct for _, pct in rungs)
    assert 0 < trimmed_total < 1.0  # never trims 100% -- something always keeps riding uncapped


def test_compute_hard_target_ladder_refuses_to_guess_on_bad_entry_usd():
    assert moonbag.compute_hard_target_ladder(entry_usd=0) == []
    assert moonbag.compute_hard_target_ladder(entry_usd=-5) == []
    assert moonbag.compute_hard_target_ladder(entry_usd=None) == []


def test_high_conviction_position_gets_its_own_hard_target_rungs_not_the_shared_table():
    position_state.record_stage_entry("solana", "MintRungs", "stage1", 20.0, 100000, "test")
    moonbag.assess_conviction("solana", "MintRungs", deployer_tier="elite", convergence_count=3)
    pos = position_state.get_position("solana", "MintRungs")
    # Compare as tuples -- the local JSON state backend round-trips tuples
    # as lists, which is fine for evaluate_trim's iteration but not for a
    # direct equality check here.
    assert [tuple(r) for r in pos["trim_ladder_rungs"]] == moonbag.compute_hard_target_ladder(20.0)
    assert [tuple(r) for r in pos["trim_ladder_rungs"]] != moonbag.HIGH_CONVICTION_LADDER


def test_stage2_upgrade_recomputes_rungs_off_the_new_combined_entry_size():
    """A Stage 2 confirmation that adds more capital to an existing Stage 1
    position should recompute the hard-target rungs off the COMBINED
    total_usd, not silently keep using the smaller Stage 1-only rungs."""
    position_state.record_stage_entry("solana", "MintUpgrade", "stage1", 20.0, 40000, "test")
    moonbag.assess_conviction("solana", "MintUpgrade", deployer_tier="good")  # score 1 -- stays default for now
    position_state.record_stage_entry("solana", "MintUpgrade", "stage2", 17.0, 90000, "test")
    # double_confirmed (3) + deployer_tier="good" (1) = 4, crosses the threshold
    # (double_confirmed alone is only 3 -- see test_double_confirmation_alone_crosses_threshold).
    moonbag.assess_conviction("solana", "MintUpgrade", double_confirmed=True, deployer_tier="good")

    pos = position_state.get_position("solana", "MintUpgrade")
    assert pos["trim_ladder"] == "high_conviction"
    assert pos["total_usd"] == pytest.approx(37.0)
    assert [tuple(r) for r in pos["trim_ladder_rungs"]] == moonbag.compute_hard_target_ladder(37.0)

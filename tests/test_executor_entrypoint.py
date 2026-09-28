import pytest

import state
import executor.entrypoint as entrypoint
import executor.position_state as position_state
import executor.moonbag as moonbag


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    yield


def test_stage1_fires_records_position_and_scores_conviction():
    result = entrypoint.handle_stage1_candidate(
        "solana", "MintA", score_band="A", deployer_tier="elite",
        convergence_count=3, entry_mcap=50000,
    )
    assert result["fired"] is True
    assert result["stage"] == "stage1"
    assert result["conviction_score"] == 4  # elite (2) + 3-wallet convergence (2)
    assert result["trim_ladder"] == "high_conviction"

    pos = position_state.get_position("solana", "MintA")
    assert pos is not None
    assert pos["stages"]["stage1"]["entry_mcap"] == 50000
    assert position_state.get_trim_ladder("solana", "MintA") == "high_conviction"


def test_stage1_does_not_fire_records_nothing():
    result = entrypoint.handle_stage1_candidate(
        "solana", "MintB", score_band="D", deployer_tier="spammer",
        convergence_count=0, entry_mcap=50000,
    )
    assert result["fired"] is False
    assert position_state.get_position("solana", "MintB") is None


def test_stage1_weak_signal_gets_default_ladder():
    result = entrypoint.handle_stage1_candidate(
        "solana", "MintC", score_band="A", deployer_tier=None,
        convergence_count=0, entry_mcap=50000,
    )
    assert result["fired"] is True
    assert result["conviction_score"] < 4
    assert result["trim_ladder"] == "default"


def test_stage2_fires_records_position():
    result = entrypoint.handle_stage2_candidate(
        "solana", "MintD", current_mcap_usd=200000,
        fomo_convergence_count=2, graduated=True,
    )
    assert result["fired"] is True
    assert result["stage"] == "stage2"
    pos = position_state.get_position("solana", "MintD")
    assert pos["stages"]["stage2"]["entry_mcap"] == 200000


def test_stage2_confirming_a_stage1_position_upgrades_to_double_confirmed():
    s1 = entrypoint.handle_stage1_candidate(
        "solana", "MintE", score_band=None, deployer_tier="good",
        convergence_count=0, entry_mcap=40000,
    )
    assert s1["trim_ladder"] == "default"  # good deployer alone (score 1) -- not enough on its own

    s2 = entrypoint.handle_stage2_candidate(
        "solana", "MintE", current_mcap_usd=90000,  # >= 2x stage1 entry mcap floor
        fomo_convergence_count=2, graduated=True,
    )
    assert s2["fired"] is True
    assert s2["double_confirmed"] is True
    assert s2["trim_ladder"] == "high_conviction"  # double-confirmation (+3) pushed it over the threshold
    assert position_state.get_trim_ladder("solana", "MintE") == "high_conviction"


def test_second_scoring_call_never_downgrades_an_existing_high_conviction_ladder():
    # Lock in high_conviction directly first (simulating a strong Stage 1 fire).
    position_state.record_stage_entry("solana", "MintF", "stage1", 20.0, 50000, "test")
    moonbag.assess_conviction("solana", "MintF", deployer_tier="elite", convergence_count=3)
    assert position_state.get_trim_ladder("solana", "MintF") == "high_conviction"

    # A later re-score with weak signals must NOT pull it back down.
    weaker = moonbag.assess_conviction("solana", "MintF", deployer_tier=None, convergence_count=0)
    assert weaker["ladder"] == "high_conviction"
    assert position_state.get_trim_ladder("solana", "MintF") == "high_conviction"


def test_stage1_already_fired_blocks_second_entrypoint_call():
    entrypoint.handle_stage1_candidate(
        "solana", "MintG", score_band="A", deployer_tier=None,
        convergence_count=0, entry_mcap=50000,
    )
    result = entrypoint.handle_stage2_candidate(  # unrelated call shouldn't matter here
        "solana", "MintH", current_mcap_usd=200000, fomo_convergence_count=2, graduated=True,
    )
    second_stage1 = entrypoint.handle_stage1_candidate(
        "solana", "MintG", score_band="A", deployer_tier=None,
        convergence_count=0, entry_mcap=51000,
    )
    assert second_stage1["fired"] is False
    assert "already fired" in second_stage1["reason"]


def test_failed_buy_does_not_permanently_eat_stage_budget():
    """Real bug, fixed Sept 28 2026: record_stage_entry commits usd_amount
    to stage_committed_usd BEFORE the buy is attempted. With
    EXECUTION_ENABLED=false (this fixture's default), every real buy_fn
    refuses outright -- so before the fix, that committed amount stayed
    counted forever with zero tokens ever received, permanently shrinking
    the real trading budget on every failed attempt."""
    result = entrypoint.handle_stage1_candidate(
        "solana", "MintX", score_band="A", deployer_tier=None,
        convergence_count=0, entry_mcap=50000,
    )
    assert result["fired"] is True
    assert result["buy"]["ok"] is False  # EXECUTION_ENABLED=false -- buy refuses by design

    # The failed buy must not still count against the budget.
    assert position_state.stage_committed_usd("stage1") == 0.0

    pos = position_state.get_position("solana", "MintX")
    assert pos["stages"]["stage1"]["buy_status"] == "failed"
    assert pos["total_usd"] == 0.0

    # But the stage must still be considered "fired" -- no retry storm on a
    # persistently-failing token (this is the same invariant
    # test_stage1_already_fired_blocks_second_entrypoint_call checks).
    assert position_state.has_stage("solana", "MintX", "stage1") is True
    retry = entrypoint.handle_stage1_candidate(
        "solana", "MintX", score_band="A", deployer_tier=None,
        convergence_count=0, entry_mcap=50000,
    )
    assert retry["fired"] is False


def test_successful_buy_with_unparseable_fill_amount_is_tagged_not_silently_zero():
    """Real bug, fixed Sept 28 2026: a buy whose on-chain tx succeeded but
    whose fill-amount couldn't be parsed (or a 'sent but could not confirm'
    result that still carries a tx_signature) used to look IDENTICAL to a
    position nothing was ever bought for -- amount_tokens stays unset
    either way. record_fill now tags this fill_status='unconfirmed_amount'
    with the tx_signature on record, distinguishing "we hold an unknown
    real quantity" from "we hold none"."""
    position_state.record_stage_entry("solana", "MintY", "stage1", 20.0, 50000, "test")
    position_state.record_fill("solana", "MintY", None, tx_signature="5xRealSig...")
    pos = position_state.get_position("solana", "MintY")
    assert pos["fill_status"] == "unconfirmed_amount"
    assert pos["unconfirmed_fills"][0]["tx_signature"] == "5xRealSig..."
    assert "amount_tokens" not in pos  # still never guessed at a quantity

    # A later real, parseable fill on the same position still records normally.
    position_state.record_fill("solana", "MintY", 12345.0, tx_signature="6xRealSig...")
    pos = position_state.get_position("solana", "MintY")
    assert pos["amount_tokens"] == 12345.0
    assert pos["fill_status"] == "confirmed"

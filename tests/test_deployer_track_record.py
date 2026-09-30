"""
Tests for the self-built deployer track record (Sept 30 2026, Ali: "build
that and also do that vice versa on if any of my trade made loss or the
developer rugged to add that in that list to blacklist this developer and
avoid coins launched from him").

Three things are covered, matching the three places that changed:
  1. state.py's record/get/reputation functions -- pure bookkeeping logic.
  2. executor/position_state.py's close_position -- writes a real outcome
     to state.py automatically when a position closes with a deployer
     wallet and a priced P&L.
  3. scheduler.py's _handle_scored -- a blacklisted deployer hard-blocks a
     Stage 1 fire outright; a trusted deployer is passed through as
     MadeOnSol's "good" tier for the existing OR condition.
"""
import pytest

import state
import scheduler
import executor.position_state as position_state
from layers.layer0_scoring import ScoreResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


# --- state.py: reputation bookkeeping -------------------------------------

def test_reputation_is_neutral_with_no_history():
    rep = state.get_deployer_reputation("WALLET_UNSEEN")
    assert rep["tier"] == "neutral"
    assert rep["sample_size"] == 0


def test_single_rug_blacklists_immediately():
    state.record_deployer_outcome("WALLET_RUG", "tokenA", "solana", "rug", -15.0)
    rep = state.get_deployer_reputation("WALLET_RUG")
    assert rep["tier"] == "blacklisted"
    assert rep["rugs"] == 1
    assert state.is_deployer_blacklisted("WALLET_RUG") is True
    assert state.is_deployer_trusted("WALLET_RUG") is False


def test_one_win_alone_is_not_enough_to_trust():
    state.record_deployer_outcome("WALLET_ONEWIN", "tokenA", "solana", "win", 25.0)
    rep = state.get_deployer_reputation("WALLET_ONEWIN")
    assert rep["tier"] == "neutral"
    assert state.is_deployer_trusted("WALLET_ONEWIN") is False


def test_two_real_wins_net_positive_and_zero_rugs_is_trusted():
    state.record_deployer_outcome("WALLET_GOOD", "tokenA", "solana", "win", 25.0)
    state.record_deployer_outcome("WALLET_GOOD", "tokenB", "solana", "win", 10.0)
    rep = state.get_deployer_reputation("WALLET_GOOD")
    assert rep["tier"] == "trusted"
    assert rep["wins"] == 2
    assert rep["net_pnl_usd"] == 35.0
    assert state.is_deployer_trusted("WALLET_GOOD") is True


def test_a_single_rug_overrides_an_otherwise_winning_record():
    state.record_deployer_outcome("WALLET_MIXED", "tokenA", "solana", "win", 25.0)
    state.record_deployer_outcome("WALLET_MIXED", "tokenB", "solana", "win", 10.0)
    state.record_deployer_outcome("WALLET_MIXED", "tokenC", "solana", "rug", -50.0)
    rep = state.get_deployer_reputation("WALLET_MIXED")
    assert rep["tier"] == "blacklisted"
    assert state.is_deployer_trusted("WALLET_MIXED") is False


def test_losses_without_a_rug_stay_neutral_not_blacklisted():
    state.record_deployer_outcome("WALLET_UNLUCKY", "tokenA", "solana", "loss", -5.0)
    state.record_deployer_outcome("WALLET_UNLUCKY", "tokenB", "solana", "loss", -3.0)
    rep = state.get_deployer_reputation("WALLET_UNLUCKY")
    assert rep["tier"] == "neutral"
    assert state.is_deployer_blacklisted("WALLET_UNLUCKY") is False


def test_none_wallet_is_never_blacklisted_or_trusted():
    assert state.is_deployer_blacklisted(None) is False
    assert state.is_deployer_trusted(None) is False


# --- position_state.py: close_position auto-records the outcome ----------

def test_close_position_win_records_deployer_outcome(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "fetch_solana_token_deployer", lambda mint: "WALLET_FROM_CLOSE")
    position_state.record_stage_entry("solana", "tok1", "stage1", 20.0, 50000.0, "test entry")
    position_state.close_position("solana", "tok1", reason="take_profit", exit_usd=40.0)

    rep = state.get_deployer_reputation("WALLET_FROM_CLOSE")
    assert rep["wins"] == 1
    assert rep["sample_size"] == 1
    pos = position_state.get_position("solana", "tok1")
    assert pos["deployer_wallet"] == "WALLET_FROM_CLOSE"


def test_close_position_defensive_sell_records_a_rug(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "fetch_solana_token_deployer", lambda mint: "WALLET_RUGGER")
    position_state.record_stage_entry("solana", "tok2", "stage1", 20.0, 50000.0, "test entry")
    position_state.close_position("solana", "tok2", reason="defensive_sell: exit risk detected",
                                   exit_usd=2.0)

    rep = state.get_deployer_reputation("WALLET_RUGGER")
    assert rep["rugs"] == 1
    assert rep["tier"] == "blacklisted"


def test_close_position_loss_without_defensive_sell_records_a_loss_not_a_rug(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "fetch_solana_token_deployer", lambda mint: "WALLET_LOSER")
    position_state.record_stage_entry("solana", "tok3", "stage1", 20.0, 50000.0, "test entry")
    position_state.close_position("solana", "tok3", reason="campaign_milestone_stop", exit_usd=15.0)

    rep = state.get_deployer_reputation("WALLET_LOSER")
    assert rep["losses"] == 1
    assert rep["rugs"] == 0
    assert rep["tier"] == "neutral"


def test_close_position_never_raises_when_deployer_lookup_fails(monkeypatch):
    import layers.layer0_scoring as l0
    def boom(mint):
        raise Exception("network unreachable")
    monkeypatch.setattr(l0, "fetch_solana_token_deployer", boom)
    position_state.record_stage_entry("solana", "tok4", "stage1", 20.0, 50000.0, "test entry")
    # must not raise -- deployer-reputation write is best-effort
    pos = position_state.close_position("solana", "tok4", reason="take_profit", exit_usd=30.0)
    assert pos["status"] == "closed"
    assert pos["pnl_usd"] == 10.0


def test_close_position_on_non_solana_chain_never_attempts_deployer_lookup(monkeypatch):
    import layers.layer0_scoring as l0
    def boom(mint):
        raise AssertionError("should never be called for a non-Solana chain")
    monkeypatch.setattr(l0, "fetch_solana_token_deployer", boom)
    position_state.record_stage_entry("bsc", "tok5", "stage1", 20.0, 50000.0, "test entry")
    pos = position_state.close_position("bsc", "tok5", reason="take_profit", exit_usd=30.0)
    assert pos["status"] == "closed"


# --- scheduler.py: blacklist hard-blocks Stage 1, trust upgrades the OR --

def _scored(address, band, score=90):
    return {"chain": "ignored", "address": address,
            "score": ScoreResult(score=score, band=band, liquidity_flag="deep", reasons=[])}


def test_blacklisted_deployer_blocks_stage1_even_on_band_a(monkeypatch):
    monkeypatch.setattr(scheduler, "fetch_solana_token_deployer", lambda mint: "WALLET_BLACKLISTED")
    monkeypatch.setattr(scheduler, "fetch_solana_dev_holding_pct", lambda mint, wallet: None)
    state.record_deployer_outcome("WALLET_BLACKLISTED", "some-other-token", "solana", "rug", -20.0)

    scored = _scored("token-from-bad-deployer", "A")  # band A would otherwise fire on its own
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)

    assert not position_state.has_stage("solana", "token-from-bad-deployer", "stage1")


def test_non_blacklisted_deployer_band_a_still_fires_normally(monkeypatch):
    monkeypatch.setattr(scheduler, "fetch_solana_token_deployer", lambda mint: "WALLET_CLEAN")
    monkeypatch.setattr(scheduler, "fetch_solana_dev_holding_pct", lambda mint, wallet: None)

    scored = _scored("token-from-clean-deployer", "A")
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)

    assert position_state.has_stage("solana", "token-from-clean-deployer", "stage1")


def test_trusted_deployer_fires_stage1_on_band_c_alone(monkeypatch):
    # Band C alone never fires Stage 1 (see test_band_c_does_not_fire_stage1
    # in test_scheduler_handle_scored.py) -- a trusted deployer should open
    # the same OR path MadeOnSol's own elite/good tier already uses.
    monkeypatch.setattr(scheduler, "fetch_solana_token_deployer", lambda mint: "WALLET_TRUSTED")
    monkeypatch.setattr(scheduler, "fetch_solana_dev_holding_pct", lambda mint, wallet: None)
    state.record_deployer_outcome("WALLET_TRUSTED", "prior-win-1", "solana", "win", 40.0)
    state.record_deployer_outcome("WALLET_TRUSTED", "prior-win-2", "solana", "win", 12.0)

    scored = _scored("token-from-trusted-deployer", "C")
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)

    assert position_state.has_stage("solana", "token-from-trusted-deployer", "stage1")


def test_deployer_track_record_tag_shown_on_alert_when_blacklisted(monkeypatch):
    monkeypatch.setattr(scheduler, "fetch_solana_token_deployer", lambda mint: "WALLET_TAGGED_BAD")
    monkeypatch.setattr(scheduler, "fetch_solana_dev_holding_pct", lambda mint, wallet: None)
    state.record_deployer_outcome("WALLET_TAGGED_BAD", "prior-rug", "solana", "rug", -30.0)

    captured = {}
    def fake_send_alert(alert):
        captured["alert"] = alert
        return {"sent": False}
    monkeypatch.setattr(scheduler, "send_alert", fake_send_alert)

    # band D so it goes through the momentum-override path and still reaches
    # the tag-attachment code even though Stage 1 itself was blocked
    scored = _scored("token-tagged-bad-deployer", "D")
    import layers.layer8_momentum_override as l8
    monkeypatch.setattr(scheduler, "detect_momentum_override",
                         lambda band, mc_hist: l8.MomentumResult(True, "forced for test"))
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)

    assert "alert" in captured
    assert "blacklisted" in captured["alert"].tags.get("Deployer track record", "")

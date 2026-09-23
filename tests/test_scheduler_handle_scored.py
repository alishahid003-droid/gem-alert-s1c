"""
Tests for scheduler._handle_scored's execution wiring (Ali, Sept 23
2026: "check it for Solana Robinhood and BSC all three"). _handle_scored is
the ONLY place structural scoring results from EITHER the Mobula Pulse path
(BSC) or the MadeOnSol deep-score path (Solana/RHC) flow through, so testing
it directly covers all three chains at once -- it doesn't care which path
called it, only the chain string passed in.
"""
import pytest

import state
import scheduler
from layers.layer0_scoring import ScoreResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _scored(address, band, score=90):
    return {"chain": "ignored", "address": address,
            "score": ScoreResult(score=score, band=band, liquidity_flag="deep", reasons=[])}


@pytest.mark.parametrize("chain", ["solana", "robinhood_chain", "bsc"])
def test_band_a_fires_stage1on_all_three_chains(chain):
    scored = _scored("token-" + chain, "A")
    sent = scheduler._handle_scored(scored, chain, source="test", mc=50000.0)
    # _handle_scored's return value is whether an ALERT was sent (send_alert
    # has no real Telegram creds in tests, so it returns False here -- that's
    # expected and orthogonal to what this test checks). The real assertion is
    # that stage1 fired and recorded a position, verified via position_state.
    import executor.position_state as position_state
    assert position_state.has_stage(chain, "token-" + chain, "stage1")


def test_band_c_does_not_fire_stage1():
    scored = _scored("token-c", "C")
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    import executor.position_state as position_state
    assert not position_state.has_stage("solana", "token-c", "stage1")


def test_same_token_does_not_fire_stage1_twice():
    scored = _scored("token-dup", "A")
    scheduler._handle_scored(scored, "bsc", source="test", mc=50000.0)
    import executor.position_state as position_state
    committed_after_first = position_state.stage_committed_usd("stage1")
    # second call, same token -- must not commit a second position
    scheduler._handle_scored(scored, "bsc", source="test", mc=50000.0)
    assert position_state.stage_committed_usd("stage1") == committed_after_first


def test_missing_mint_does_not_crash():
    scored = {"chain": "bsc", "address": None,
               "score": ScoreResult(score=90, band="A", liquidity_flag="deep", reasons=[])}
    # just must not raise -- mint-less scored items are skipped entirely
    scheduler._handle_scored(scored, "bsc", source="test", mc=50000.0)

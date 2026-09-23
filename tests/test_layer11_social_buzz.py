"""
Tests for Layer 11 -- social/community buzz proxy (Ali, Sept 23 2026: asked
for a free Twitter/X buzz signal; real research found none free exists, see
layers/layer11_social_buzz.py's docstring; DexScreener boosts used instead
as a disclosed proxy). Covers both the layer's own pure logic and its
wiring into scheduler._handle_scored.
"""
import pytest

import state
import scheduler
from layers.layer0_scoring import ScoreResult
from layers.layer11_social_buzz import check_buzz, BuzzResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


# --- pure logic: check_buzz ---

def test_no_board_yields_none():
    assert check_buzz(None, "bsc", "0xabc").tag == "none"


def test_unmapped_chain_yields_none():
    board = {"ok": True, "latest": [], "top": [{"chainId": "arc", "tokenAddress": "0xabc", "totalAmount": 999}]}
    assert check_buzz(board, "arc", "0xabc").tag == "none"


def test_robinhood_chain_mapped_and_matches(monkeypatch=None):
    # Confirmed live Sept 23, 2026 (RBD/RobinDog's own pair) -- DexScreener's
    # real slug for Robinhood Chain is "robinhood", not a placeholder.
    board = {"ok": True, "latest": [{"chainId": "robinhood", "tokenAddress": "0xRBD", "amount": 100, "totalAmount": 100}], "top": []}
    result = check_buzz(board, "robinhood_chain", "0xRBD")
    assert result.tag == "boosted"


def test_token_not_on_board_yields_none():
    board = {"ok": True, "latest": [], "top": [{"chainId": "bsc", "tokenAddress": "0xdead", "totalAmount": 999}]}
    assert check_buzz(board, "bsc", "0xabc").tag == "none"


def test_small_boost_tags_boosted_not_surging():
    board = {"ok": True, "latest": [{"chainId": "bsc", "tokenAddress": "0xABC", "amount": 50, "totalAmount": 50}], "top": []}
    result = check_buzz(board, "bsc", "0xabc")  # case-insensitive match
    assert result.tag == "boosted"
    assert result.total_amount == 50


def test_large_boost_tags_surging():
    board = {"ok": True, "latest": [], "top": [{"chainId": "solana", "tokenAddress": "SoLmint111", "amount": 500, "totalAmount": 750}]}
    result = check_buzz(board, "solana", "SoLmint111")
    assert result.tag == "surging"
    assert result.total_amount == 750


# --- wiring: _handle_scored applies the Buzz tag ---

def _band_a_scored(mint="tok-buzz", raw=None):
    return {
        "address": mint,
        "score": ScoreResult(score=90, band="A", liquidity_flag="deep", reasons=[]),
        "raw": raw or {},
    }


def test_buzz_tag_applied_when_boosted(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    monkeypatch.setattr(scheduler, "handle_stage1_candidate",
                         lambda *a, **kw: {"fired": False, "reason": "test"})
    board = {"ok": True, "latest": [{"chainId": "bsc", "tokenAddress": "TOK-BUZZ", "amount": 500, "totalAmount": 500}], "top": []}
    scheduler._handle_scored(_band_a_scored(mint="tok-buzz"), "bsc", source="mobula", board=board)
    assert len(sent) == 1
    assert sent[0].tags.get("Buzz") == "surging"


def test_no_buzz_tag_when_not_on_board(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    monkeypatch.setattr(scheduler, "handle_stage1_candidate",
                         lambda *a, **kw: {"fired": False, "reason": "test"})
    board = {"ok": True, "latest": [], "top": []}
    scheduler._handle_scored(_band_a_scored(mint="tok-quiet"), "bsc", source="mobula", board=board)
    assert len(sent) == 1
    assert "Buzz" not in sent[0].tags


def test_no_board_no_crash(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    monkeypatch.setattr(scheduler, "handle_stage1_candidate",
                         lambda *a, **kw: {"fired": False, "reason": "test"})
    scheduler._handle_scored(_band_a_scored(mint="tok-noboard"), "bsc", source="mobula", board=None)
    assert len(sent) == 1
    assert "Buzz" not in sent[0].tags

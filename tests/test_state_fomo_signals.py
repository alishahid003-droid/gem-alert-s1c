"""Tests for state.py's Layer 13 (Fomo copy-trading/thesis) storage."""
import time

import pytest

import state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def test_record_and_get_fomo_signal_roundtrip():
    state.record_fomo_signal(kind="buy", trader="Unipcs", tier="Tier 1", token_symbol="TEST",
                              token_address="Mint111", chain="solana", detail="Unipcs bought TEST",
                              score=80, band="A")
    feed = state.get_fomo_signal_feed()
    assert len(feed) == 1
    assert feed[0]["trader"] == "Unipcs"
    assert feed[0]["score"] == 80
    assert feed[0]["band"] == "A"


def test_fomo_signal_feed_is_newest_first():
    now = time.time()
    state.record_fomo_signal(kind="buy", trader="A", tier="Tier 1", token_symbol="X",
                              token_address="M1", chain="solana", detail="", ts=now - 100)
    state.record_fomo_signal(kind="buy", trader="B", tier="Tier 1", token_symbol="Y",
                              token_address="M2", chain="solana", detail="", ts=now - 10)
    feed = state.get_fomo_signal_feed()
    assert feed[0]["trader"] == "B"
    assert feed[1]["trader"] == "A"


def test_fomo_signal_feed_ages_out_past_window():
    old_ts = time.time() - state.FOMO_SIGNAL_MAX_AGE_SECONDS - 60
    state.record_fomo_signal(kind="buy", trader="Old", tier="Tier 1", token_symbol="X",
                              token_address="M1", chain="solana", detail="", ts=old_ts)
    feed = state.get_fomo_signal_feed()
    assert feed == []


def test_fomo_signal_unscored_is_none_not_zero():
    state.record_fomo_signal(kind="thesis", trader="A", tier="Tier 1", token_symbol="X",
                              token_address="M1", chain="robinhood", detail="",
                              score=None, band=None, thesis_text="banner looks lit",
                              thesis_link="https://dexscreener.com/x")
    sig = state.get_fomo_signal_feed()[0]
    assert sig["score"] is None
    assert sig["band"] is None
    assert sig["thesis_text"] == "banner looks lit"
    assert sig["thesis_link"] == "https://dexscreener.com/x"


def test_record_fomo_candidate_and_get_roundtrip():
    state.record_fomo_candidate(handle="new_whale", display_name="New Whale", balance_usd=7500.0,
                                 pnl_usd=20000, volume_usd=100000)
    candidates = state.get_fomo_candidates()
    assert len(candidates) == 1
    assert candidates[0]["handle"] == "new_whale"
    assert candidates[0]["balance_usd"] == 7500.0


def test_record_fomo_candidate_dedupes_by_handle():
    state.record_fomo_candidate(handle="whale", display_name="Whale", balance_usd=6000.0)
    state.record_fomo_candidate(handle="whale", display_name="Whale", balance_usd=9000.0)
    candidates = state.get_fomo_candidates()
    assert len(candidates) == 1
    assert candidates[0]["balance_usd"] == 9000.0


def test_fomo_candidate_ages_out_past_window():
    old_ts = time.time() - state.FOMO_CANDIDATE_MAX_AGE_SECONDS - 60
    state.record_fomo_candidate(handle="old_whale", display_name="Old Whale", balance_usd=6000.0, ts=old_ts)
    assert state.get_fomo_candidates() == []


def test_fomo_alerts_since_cursor_roundtrip():
    assert state.get_fomo_alerts_since() is None
    state.set_fomo_alerts_since("2026-09-30T12:00:00+00:00")
    assert state.get_fomo_alerts_since() == "2026-09-30T12:00:00+00:00"


def test_fomo_alerts_since_ignores_empty_value():
    state.set_fomo_alerts_since("2026-09-30T12:00:00+00:00")
    state.set_fomo_alerts_since(None)
    assert state.get_fomo_alerts_since() == "2026-09-30T12:00:00+00:00"

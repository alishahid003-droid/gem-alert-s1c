"""Tests for state.py's Layer 12 caller-signal storage."""
import time

import pytest

import state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def test_record_and_get_caller_signal_roundtrip():
    state.record_caller_signal("token-a", "Real Caller Channel")
    sig = state.get_caller_signal("token-a")
    assert sig is not None
    assert sig["channel"] == "Real Caller Channel"


def test_get_caller_signal_returns_none_for_unmentioned_token():
    assert state.get_caller_signal("never-mentioned") is None


def test_get_caller_signal_ages_out_past_window():
    old_ts = time.time() - state.CALLER_SIGNAL_MAX_AGE_SECONDS - 60
    state.record_caller_signal("token-old", "Some Channel", ts=old_ts)
    assert state.get_caller_signal("token-old") is None


def test_get_caller_signal_returns_most_recent_of_multiple_mentions():
    now = time.time()
    state.record_caller_signal("token-b", "Channel A", ts=now - 100)
    state.record_caller_signal("token-b", "Channel B", ts=now - 10)
    sig = state.get_caller_signal("token-b")
    assert sig["channel"] == "Channel B"


def test_caller_update_offset_roundtrip():
    assert state.get_caller_update_offset() is None
    state.set_caller_update_offset(12345)
    assert state.get_caller_update_offset() == 12345


def test_set_caller_update_offset_ignores_none():
    state.set_caller_update_offset(100)
    state.set_caller_update_offset(None)
    assert state.get_caller_update_offset() == 100

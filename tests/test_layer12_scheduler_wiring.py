"""
Tests for scheduler's Layer 12 wiring: poll_layer12_caller_channels (the
fetch -> record -> advance-cursor cycle) and _handle_scored's "Caller" tag
(the pure state lookup that rides a caller mention onto a real alert).
"""
import time

import pytest

import state
import scheduler
from layers.layer0_scoring import ScoreResult
from layers.layer12_caller_channels import CallerPost


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _scored(address, band, score=90):
    return {"chain": "ignored", "address": address,
            "score": ScoreResult(score=score, band=band, liquidity_flag="deep", reasons=[])}


# --- poll_layer12_caller_channels ---

def test_poll_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(scheduler.CONFIG, "telegram_caller_bot_token", None)
    result = scheduler.poll_layer12_caller_channels()
    assert result["ok"] is False


def test_poll_records_signals_and_advances_cursor(monkeypatch):
    monkeypatch.setattr(scheduler.CONFIG, "layer12_ready", lambda: True)
    fake_result = {
        "ok": True,
        "posts": [CallerPost(channel_id=-1001, channel_name="Real Channel",
                              text="new call token-xyz-address-here-2sztT8K9Xu3cfp6WTEEB44Hdi",
                              date=1790000000)],
        "next_offset": 999,
    }
    monkeypatch.setattr(scheduler, "fetch_caller_channel_posts", lambda offset=None: fake_result)
    result = scheduler.poll_layer12_caller_channels()
    assert result["ok"] is True
    assert result["posts_checked"] == 1
    assert state.get_caller_update_offset() == 999


def test_poll_advances_cursor_even_with_zero_matching_posts(monkeypatch):
    monkeypatch.setattr(scheduler.CONFIG, "layer12_ready", lambda: True)
    monkeypatch.setattr(scheduler, "fetch_caller_channel_posts",
                         lambda offset=None: {"ok": True, "posts": [], "next_offset": 42})
    result = scheduler.poll_layer12_caller_channels()
    assert result["ok"] is True
    assert state.get_caller_update_offset() == 42


def test_poll_handles_fetch_failure_gracefully(monkeypatch):
    monkeypatch.setattr(scheduler.CONFIG, "layer12_ready", lambda: True)
    monkeypatch.setattr(scheduler, "fetch_caller_channel_posts",
                         lambda offset=None: {"ok": False, "reason": "not configured"})
    result = scheduler.poll_layer12_caller_channels()
    assert result["ok"] is False


# --- _handle_scored's "Caller" tag ---

def test_handle_scored_adds_caller_tag_when_recently_mentioned(monkeypatch):
    state.record_caller_signal("token-called", "Real Caller Channel")
    captured = {}
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: captured.update(alert=alert) or {"sent": True})
    scored = _scored("token-called", "A", score=90)
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    assert "alert" in captured
    assert "Caller" in captured["alert"].tags
    assert "Real Caller Channel" in captured["alert"].tags["Caller"]


def test_handle_scored_no_caller_tag_when_never_mentioned(monkeypatch):
    captured = {}
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: captured.update(alert=alert) or {"sent": True})
    scored = _scored("token-uncalled", "A", score=90)
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    assert "alert" in captured
    assert "Caller" not in captured["alert"].tags


def test_handle_scored_no_caller_tag_when_mention_aged_out(monkeypatch):
    old_ts = time.time() - state.CALLER_SIGNAL_MAX_AGE_SECONDS - 60
    state.record_caller_signal("token-stale-call", "Old Channel", ts=old_ts)
    captured = {}
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: captured.update(alert=alert) or {"sent": True})
    scored = _scored("token-stale-call", "A", score=90)
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    assert "Caller" not in captured["alert"].tags

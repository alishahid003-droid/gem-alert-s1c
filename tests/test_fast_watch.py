"""Checklist 5.1 fast watcher: one tick works, and ownership hands off cleanly."""
import pytest

import state
import scheduler
import worker_fast_watch as fw


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def test_no_heartbeat_github_owns():
    assert scheduler.fast_watch_owns_management() is False


def test_tick_stamps_heartbeat_and_takes_ownership(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "_run_position_management_cycle", lambda: calls.append("pos") or {})
    monkeypatch.setattr(scheduler, "_run_compound_scalper_cycle", lambda: calls.append("scalp") or {})
    monkeypatch.setattr(fw.paper_ledger, "manage", lambda fn: calls.append("paper") or {"open": 0})
    monkeypatch.setattr(scheduler, "_run_revival_watch_cycle", lambda board: calls.append("revival") or {})
    monkeypatch.setattr(fw, "fetch_boost_board", lambda: {"ok": False})
    fw.tick(0)
    assert calls == ["pos", "scalp", "paper", "revival"]
    assert scheduler.fast_watch_owns_management() is True
    calls.clear()
    fw.tick(1)                                   # revival only every 3rd tick
    assert "revival" not in calls


def test_stale_heartbeat_hands_back_to_github():
    import time
    state.record_runner_heartbeat("fast-watch", "local-pc", "x", ts=time.time() - 600)
    assert scheduler.fast_watch_owns_management() is False

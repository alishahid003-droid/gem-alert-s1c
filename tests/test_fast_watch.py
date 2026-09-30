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
    monkeypatch.setattr(fw.paper_ledger, "manage", lambda fn, **kw: calls.append("paper") or {"open": 0})
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


def test_idle_mode_alternates_paper_and_revival(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "_run_position_management_cycle", lambda: calls.append("pos") or {})
    monkeypatch.setattr(scheduler, "_run_compound_scalper_cycle", lambda: calls.append("scalp") or {})
    monkeypatch.setattr(fw.paper_ledger, "manage", lambda fn, **kw: calls.append("paper") or {"open": 0})
    monkeypatch.setattr(scheduler, "_run_revival_watch_cycle", lambda board: calls.append("revival") or {})
    monkeypatch.setattr(fw, "fetch_boost_board", lambda: {"ok": False})
    fw.tick(0, fast=False)
    assert calls == ["pos", "scalp", "paper"]
    calls.clear()
    fw.tick(1, fast=False)
    assert calls == ["pos", "scalp", "revival"]
    assert scheduler.fast_watch_owns_management() is True   # heartbeat every tick


def test_busy_only_with_real_money(monkeypatch):
    import executor.position_state as ps
    import executor.compound_scalper as cs
    monkeypatch.setattr(ps, "list_open_positions", lambda: [])
    monkeypatch.setattr(cs, "status", lambda: {"open_position": None})
    assert fw.busy() is False
    monkeypatch.setattr(cs, "status", lambda: {"open_position": {"token": "x"}})
    assert fw.busy() is True
    monkeypatch.setattr(cs, "status", lambda: {"open_position": None})
    monkeypatch.setattr(ps, "list_open_positions", lambda: [{"amount_tokens": 5}])
    assert fw.busy() is True


def test_state_command_counter_counts():
    before = state.COMMAND_COUNTER["n"]
    state.set_value("k", 1)
    state.get_value("k")
    state.get_values(["k"])
    assert state.COMMAND_COUNTER["n"] - before == 3

"""
Tests for the GitHub Actions gating added Sept 24 2026: a free MadeOnSol key
gets rate-limited by GitHub Actions' rotating runner IPs ("Too many IP
addresses for one free key... 8 IP addresses today" -- confirmed live), so
Layer 1, Layer 8, and Layer 2+9 (the only layers that call MadeOnSol) now
skip entirely when scheduler.IS_GITHUB_ACTIONS is True, and instead run from
run_poll_madeonsol(), meant to be scheduled on Ali's own PC where the IP is
stable. These tests check the gating logic itself, not live MadeOnSol calls.
"""
import pytest

import state
import scheduler


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setattr(scheduler.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(scheduler.CONFIG, "upstash_redis_rest_token", None)
    yield


def test_run_poll_fast_skips_layer1_on_github_actions(monkeypatch, capsys):
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", True)
    called = []
    monkeypatch.setattr(scheduler, "_run_layer1_cycle", lambda *a, **k: called.append(1) or (0, 0))
    monkeypatch.setattr(scheduler, "fetch_boost_board", lambda: {"ok": False})
    scheduler.state.set_value("pumpfun_manual_seed_done", True)  # skip one-off seed
    scheduler.run_poll_fast()
    assert called == []
    captured = capsys.readouterr()
    assert "[layer1] SKIPPED on GitHub Actions" in captured.out


def test_run_poll_fast_runs_layer1_when_not_on_github_actions(monkeypatch):
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", False)
    called = []
    monkeypatch.setattr(scheduler, "_run_layer1_cycle", lambda *a, **k: called.append(1) or (0, 0))
    monkeypatch.setattr(scheduler, "fetch_boost_board", lambda: {"ok": False})
    scheduler.state.set_value("pumpfun_manual_seed_done", True)  # skip one-off seed
    scheduler.run_poll_fast()
    assert called == [1]


def test_run_poll_slow_skips_layer8_and_fomo_on_github_actions(monkeypatch, capsys):
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", True)
    l8_called = []
    fomo_called = []
    monkeypatch.setattr(scheduler, "_run_layer8_cycle", lambda *a, **k: l8_called.append(1) or (0, 0))
    monkeypatch.setattr(scheduler, "_run_fomo_cycle", lambda *a, **k: fomo_called.append(1) or (0, 0))
    monkeypatch.setattr(scheduler, "fetch_boost_board", lambda: {"ok": False})
    scheduler.run_poll_slow()
    assert l8_called == []
    assert fomo_called == []
    captured = capsys.readouterr()
    assert "[layer0/8] SKIPPED on GitHub Actions" in captured.out
    assert "[layer2+9] SKIPPED on GitHub Actions" in captured.out


def test_run_poll_slow_runs_layer8_and_fomo_when_not_on_github_actions(monkeypatch):
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", False)
    l8_called = []
    fomo_called = []
    monkeypatch.setattr(scheduler, "_run_layer8_cycle", lambda *a, **k: l8_called.append(1) or (0, 0))
    monkeypatch.setattr(scheduler, "_run_fomo_cycle", lambda *a, **k: fomo_called.append(1) or (0, 0))
    monkeypatch.setattr(scheduler, "fetch_boost_board", lambda: {"ok": False})
    scheduler.run_poll_slow()
    assert l8_called == [1]
    assert fomo_called == [1]


def test_run_poll_madeonsol_runs_all_three_regardless_of_github_actions_flag(monkeypatch):
    # This is the local-PC entrypoint -- it must always run the MadeOnSol
    # layers, whatever IS_GITHUB_ACTIONS says (Ali runs this by hand on his
    # own machine, never as part of the GitHub Actions cron).
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", True)
    calls = []
    monkeypatch.setattr(scheduler, "_run_layer1_cycle", lambda *a, **k: calls.append("layer1") or (0, 0))
    monkeypatch.setattr(scheduler, "_run_layer8_cycle", lambda *a, **k: calls.append("layer8") or (0, 0))
    monkeypatch.setattr(scheduler, "_run_fomo_cycle", lambda *a, **k: calls.append("fomo") or (0, 0))
    monkeypatch.setattr(scheduler, "fetch_boost_board", lambda: {"ok": False})
    scheduler.run_poll_madeonsol()
    assert calls == ["layer1", "layer8", "fomo"]

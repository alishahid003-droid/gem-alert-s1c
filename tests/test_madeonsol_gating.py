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


def test_run_layer8_cycle_requeues_pending_tokens_when_budget_insufficient(monkeypatch):
    # Real production scenario, Sept 25 2026: MadeOnSol's BASIC-tier key has
    # a confirmed real 200/day cap. When the day's budget can't cover a
    # full 3-call score_solana_mint attempt, _run_layer8_cycle must defer
    # that token to a later cycle (state.queue_rescan) instead of burning a
    # doomed call -- see scheduler.py's budget-check comment.
    monkeypatch.setattr(scheduler.CONFIG, "madeonsol_api_key", "msk_test")
    state.queue_rescan("MINTA", "solana", False)
    state.queue_rescan("MINTB", "solana", False)

    # Leave only 2 slots of real budget -- not enough for even one 3-call attempt.
    state.record_madeonsol_calls(state.MADEONSOL_DAILY_BUDGET - 2)

    scored_calls = []
    monkeypatch.setattr(scheduler, "score_solana_mint",
                         lambda *a, **kw: scored_calls.append(a) or {"error": "should not be called"})

    alerts_sent, madeonsol_calls = scheduler._run_layer8_cycle(board={})

    assert scored_calls == []  # never attempted -- budget was insufficient
    assert madeonsol_calls == 0
    assert alerts_sent == 0
    # both tokens re-queued, not dropped
    assert state.pending_rescan_count() == 2


def test_run_layer8_cycle_scores_normally_when_budget_is_healthy(monkeypatch):
    monkeypatch.setattr(scheduler.CONFIG, "madeonsol_api_key", "msk_test")
    state.queue_rescan("MINTA", "solana", False)

    scored_calls = []

    def fake_score(mint, chain, is_pregraduation):
        scored_calls.append(mint)
        return {"chain": chain, "address": mint, "score": None}

    monkeypatch.setattr(scheduler, "score_solana_mint", fake_score)
    monkeypatch.setattr(scheduler, "_handle_scored", lambda *a, **kw: False)

    alerts_sent, madeonsol_calls = scheduler._run_layer8_cycle(board={})

    assert scored_calls == ["MINTA"]
    assert madeonsol_calls == 3
    assert state.pending_rescan_count() == 0


def test_run_layer8_cycle_requeues_pending_tokens_when_budget_insufficient(monkeypatch):
    # Real production scenario, Sept 25 2026: MadeOnSol's BASIC-tier key has
    # a confirmed real 200/day cap. When the day's budget can't cover a
    # full 3-call score_solana_mint attempt, _run_layer8_cycle must defer
    # that token to a later cycle (state.queue_rescan) instead of burning a
    # doomed call -- see scheduler.py's budget-check comment.
    monkeypatch.setattr(scheduler.CONFIG, "madeonsol_api_key", "msk_test")
    state.queue_rescan("MINTA", "solana", False)
    state.queue_rescan("MINTB", "solana", False)

    # Leave only 2 slots of real budget -- not enough for even one 3-call attempt.
    state.record_madeonsol_calls(state.MADEONSOL_DAILY_BUDGET - 2)

    scored_calls = []
    monkeypatch.setattr(scheduler, "score_solana_mint",
                         lambda *a, **kw: scored_calls.append(a) or {"error": "should not be called"})

    alerts_sent, madeonsol_calls = scheduler._run_layer8_cycle(board={})

    assert scored_calls == []  # never attempted -- budget was insufficient
    assert madeonsol_calls == 0
    assert alerts_sent == 0
    # both tokens re-queued, not dropped
    assert state.pending_rescan_count() == 2


def test_run_layer8_cycle_scores_normally_when_budget_is_healthy(monkeypatch):
    monkeypatch.setattr(scheduler.CONFIG, "madeonsol_api_key", "msk_test")
    state.queue_rescan("MINTA", "solana", False)

    scored_calls = []

    def fake_score(mint, chain, is_pregraduation):
        scored_calls.append(mint)
        return {"chain": chain, "address": mint, "score": None}

    monkeypatch.setattr(scheduler, "score_solana_mint", fake_score)
    monkeypatch.setattr(scheduler, "_handle_scored", lambda *a, **kw: False)

    alerts_sent, madeonsol_calls = scheduler._run_layer8_cycle(board={})

    assert scored_calls == ["MINTA"]
    assert madeonsol_calls == 3
    assert state.pending_rescan_count() == 0

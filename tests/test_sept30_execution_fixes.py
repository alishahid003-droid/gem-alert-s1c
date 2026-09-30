"""Sept 30 2026 regression tests for the "would these alerts actually have
been bought?" audit:
  1. Base (no buy path) must be refused before anything is recorded.
  2. Bankroll tier max_concurrent is enforced (was defined, never checked).
  3. A score-only band A/B fire needs enough REAL data behind the score.
  4. entrypoint marks a stage failed when a chain has no buy function.
  5. Stage 2 runs on real convergence events, and a large untracked buy no
     longer crashes the Fomo cycle (KeyError on ev["count"]).
  6. Auto-buy verdicts are recorded/read back for the dashboard."""
import pytest

import state
import scheduler
import executor.triggers as triggers
import executor.entrypoint as entrypoint
import executor.position_state as position_state
from executor.config import EXECUTOR_CONFIG
from layers.layer0_scoring import RawSignals, score_token


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    yield


def test_base_chain_refused_before_recording():
    d = triggers.evaluate_stage1("base", "0xabc", score_band="A", deployer_tier=None, convergence_count=0)
    assert d.should_fire is False
    assert "no auto-buy path" in d.reason
    r = entrypoint.handle_stage1_candidate("base", "0xabc", "A", None, 0, 100000)
    assert r["fired"] is False
    assert position_state.get_position("base", "0xabc") is None


def test_base_chain_refused_for_stage2():
    d = triggers.evaluate_stage2("base", "0xabc", 500000, 3, True)
    assert d.should_fire is False
    assert "no auto-buy path" in d.reason


def test_max_concurrent_enforced(monkeypatch):
    monkeypatch.setattr(EXECUTOR_CONFIG, "bankroll_tier_info",
                        {**EXECUTOR_CONFIG.bankroll_tier_info, "max_concurrent": 1})
    position_state.record_stage_entry("solana", "MintOne", "stage1", 5.0, 50000, "test")
    d = triggers.evaluate_stage1("solana", "MintTwo", score_band="A", deployer_tier=None, convergence_count=0)
    assert d.should_fire is False
    assert "max concurrent" in d.reason


def test_failed_buys_do_not_count_toward_concurrency(monkeypatch):
    monkeypatch.setattr(EXECUTOR_CONFIG, "bankroll_tier_info",
                        {**EXECUTOR_CONFIG.bankroll_tier_info, "max_concurrent": 1})
    position_state.record_stage_entry("solana", "MintOne", "stage1", 5.0, 50000, "test")
    position_state.mark_stage_buy_failed("solana", "MintOne", "stage1", reason="disabled")
    d = triggers.evaluate_stage1("solana", "MintTwo", score_band="A", deployer_tier=None, convergence_count=0)
    assert d.should_fire is True


def test_low_coverage_score_only_fire_blocked():
    d = triggers.evaluate_stage1("bsc", "0xabc", score_band="B", deployer_tier=None,
                                  convergence_count=0, signal_coverage=0.35)
    assert d.should_fire is False
    assert "real data" in d.reason


def test_low_coverage_does_not_block_convergence_fire():
    d = triggers.evaluate_stage1("solana", "MintC", score_band="B", deployer_tier=None,
                                  convergence_count=2, signal_coverage=0.2)
    assert d.should_fire is True


def test_sufficient_coverage_fires():
    d = triggers.evaluate_stage1("solana", "MintD", score_band="B", deployer_tier=None,
                                  convergence_count=0, signal_coverage=0.7)
    assert d.should_fire is True


def test_score_token_reports_coverage():
    assert score_token(RawSignals()).signal_coverage == 0.0
    typical_evm = score_token(RawSignals(lp_locked_or_curve_healthy=False, mint_authority_revoked=True,
                                         freeze_authority_revoked=True, vol_to_liq_ratio=2.0,
                                         liquidity_usd=20000))
    assert typical_evm.band == "B"
    assert typical_evm.signal_coverage < triggers.STAGE1_MIN_SIGNAL_COVERAGE


def test_missing_buy_function_marks_stage_failed(monkeypatch):
    position_state.record_stage_entry("ton", "T1", "stage1", 5.0, 1000, "test")
    r = entrypoint._attempt_buy_and_record_fill("ton", "T1", 5.0, stage="stage1")
    assert r["attempted"] is False
    pos = position_state.get_position("ton", "T1")
    assert pos["stages"]["stage1"]["buy_status"] == "failed"


def test_autobuy_verdict_roundtrip():
    state.record_autobuy_verdict("MintV", "solana", {"fired": False, "stage": "stage1",
                                                      "reason": "budget exhausted"})
    got = state.get_autobuy_verdicts(["MintV", "Nope"])
    assert got["MintV"]["reason"] == "budget exhausted"
    assert got["Nope"] is None


def _fake_fomo_env(monkeypatch, layer2_result, stage2_calls):
    monkeypatch.setattr(scheduler, "fetch_kol_feed_both", lambda chain: {
        "ok": True, "mode": "test", "calls_made": 0, "buy_trades": [], "sell_trades": []})
    monkeypatch.setattr(scheduler, "poll_layer2", lambda chain, trades=None: layer2_result)
    monkeypatch.setattr(scheduler, "_alert", lambda alert, layer: {"sent": False})

    def fake_stage2(chain, token, **kw):
        stage2_calls.append((chain, token, kw))
        return {"fired": False, "stage": "stage2", "reason": "test"}
    monkeypatch.setattr(scheduler, "handle_stage2_candidate", fake_stage2)
    monkeypatch.setattr(scheduler, "_safe", lambda fn, *a, **kw: fn(*a, **kw)
                        if fn is not scheduler.poll_layer9 else {"ok": True, "events": []})


def test_large_untracked_buy_does_not_crash_and_does_not_trigger_stage2(monkeypatch):
    calls = []
    _fake_fomo_env(monkeypatch, {"ok": True, "events": [], "single_events": [], "mc_points": [],
                                 "large_untracked_events": [{"name": "x", "wallet": "w", "token": "TokU",
                                                             "sol_amount": 50.0}]}, calls)
    report = {"stage2": {"layer2_convergence": True}}
    scheduler._run_fomo_cycle(report)
    assert calls == []


def test_convergence_event_reaches_stage2(monkeypatch):
    calls = []
    _fake_fomo_env(monkeypatch, {"ok": True, "single_events": [], "large_untracked_events": [],
                                 "mc_points": [("TokC", 400000.0, 1.0)],
                                 "events": [{"token": "TokC", "count": 3}]}, calls)
    report = {"stage2": {"layer2_convergence": True}}
    scheduler._run_fomo_cycle(report)
    assert len(calls) == 2  # solana + robinhood_chain loops, same fake feed
    assert calls[0][1] == "TokC"
    assert calls[0][2]["fomo_convergence_count"] == 3
    assert calls[0][2]["current_mcap_usd"] == 400000.0

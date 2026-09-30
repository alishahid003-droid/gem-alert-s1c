"""
Tests for the soft-fail watch list (Ali, Sept 28 2026 -- the real gap he
flagged live: a Solana/RHC token that fails Layer 0's structural score (band
D) only gets re-checked via scheduler._maybe_queue_rescan, which triggers
ONLY on a fresh market-cap point -- and the only source of those is Layer 2's
tracked-KOL-wallet feed. A token no tracked wallet ever trades never gets a
second MC point, so it silently never re-queues, even if it quietly becomes
structurally clean within its first hours. This covers the three pieces that
close that gap: scheduler._handle_scored adding a D-band Solana token to the
watch list (state.py), free_recheck_solana_signals doing a zero-MadeOnSol-
budget re-check, and scheduler._run_soft_fail_watch_cycle deciding whether
that re-check earns a real re-score.
"""
import pytest

import state
import scheduler
from layers.layer0_scoring import ScoreResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _scored(address, band, score=40, reasons=None):
    return {"chain": "ignored", "address": address,
            "score": ScoreResult(score=score, band=band, liquidity_flag="thin", reasons=reasons or [])}


# --- _handle_scored wiring: band D, no momentum -> added to watch list ---

def test_band_d_solana_with_no_momentum_gets_added_to_watch_list():
    scored = _scored("token-d-solana", "D", score=35, reasons=["top10 holds 70%"])
    sent = scheduler._handle_scored(scored, "solana", source="test", mc=None,
                                     is_pregraduation=True)
    assert sent is False  # suppressed, per spec -- no alert
    watch = state.get_soft_fail_watch()
    assert len(watch) == 1
    assert watch[0]["token"] == "token-d-solana"
    assert watch[0]["is_pregraduation"] is True
    assert watch[0]["last_score"] == 35


def test_band_d_robinhood_chain_now_watched_for_revival():
    # Changed Sept 30 2026 (Layer 14, Ali's ask): revival is re-checked with
    # free DexScreener data, which covers every chain -- not SPL-only RPC.
    scored = _scored("token-d-rhc", "D", score=35)
    scheduler._handle_scored(scored, "robinhood_chain", source="test", mc=None,
                              is_pregraduation=False)
    assert [w["chain"] for w in state.get_soft_fail_watch()] == ["robinhood_chain"]


def test_band_d_bsc_now_watched_for_revival():
    scored = _scored("token-d-bsc", "D", score=35)
    scheduler._handle_scored(scored, "bsc", source="test", mc=None)
    assert [w["token"] for w in state.get_soft_fail_watch()] == ["token-d-bsc"]


def test_band_d_with_momentum_override_does_not_add_to_watch_list(monkeypatch):
    # Momentum override already alerts on this token -- no need to also
    # watch it for a quiet turnaround, it's already being surfaced.
    import time as time_mod
    now = time_mod.time()
    # Real trigger shape per detect_momentum_override: min MC in the last
    # 30 min <= LOW_BASE_MC_USD (10k) and the latest point >= SIX_FIGURES_MC_USD (100k).
    state.record_mc_point("token-momentum", 5_000.0, ts=now - 600)
    state.record_mc_point("token-momentum", 150_000.0, ts=now)
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    scored = _scored("token-momentum", "D", score=20)
    sent = scheduler._handle_scored(scored, "solana", source="test", mc=None, is_pregraduation=True)
    assert sent is True
    assert state.get_soft_fail_watch() == []


def test_clearing_band_d_removes_token_from_watch_list(monkeypatch):
    state.watch_add("token-recovered", "solana", True, 35, ["top10 holds 70%"])
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    scored = _scored("token-recovered", "B", score=68)
    scheduler._handle_scored(scored, "solana", source="test", mc=None, is_pregraduation=True)
    assert state.get_soft_fail_watch() == []


def test_missing_mint_does_not_crash_watch_wiring():
    scored = {"chain": "solana", "address": None,
              "score": ScoreResult(score=20, band="D", liquidity_flag="thin", reasons=[])}
    scheduler._handle_scored(scored, "solana", source="test", mc=None, is_pregraduation=True)
    assert state.get_soft_fail_watch() == []


# --- _run_soft_fail_watch_cycle: free-signal gate before a real re-score ---

def test_watch_cycle_queues_rescan_when_top10_concentration_improved(monkeypatch):
    state.watch_add("token-improved", "solana", True, 35, ["top10 holds 70%"])
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals",
                         lambda mint: {"top10_holder_pct": 0.20, "holder_growth_rate_per_hr": None})
    queued = scheduler._run_soft_fail_watch_cycle()
    assert queued == 1
    assert state.pending_rescan_count() == 1
    # queued for a real re-score -- removed from the watch list, the
    # re-score itself is now the authoritative next check
    assert state.get_soft_fail_watch() == []


def test_watch_cycle_queues_rescan_when_holder_growth_improved(monkeypatch):
    state.watch_add("token-growing", "solana", True, 35, ["holder growth unknown"])
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals",
                         lambda mint: {"top10_holder_pct": 0.55, "holder_growth_rate_per_hr": 25.0})
    queued = scheduler._run_soft_fail_watch_cycle()
    assert queued == 1
    assert state.pending_rescan_count() == 1


def test_watch_cycle_does_not_queue_when_signals_still_bad(monkeypatch):
    state.watch_add("token-still-bad", "solana", True, 35, [])
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals",
                         lambda mint: {"top10_holder_pct": 0.55, "holder_growth_rate_per_hr": 2.0})
    queued = scheduler._run_soft_fail_watch_cycle()
    assert queued == 0
    assert state.pending_rescan_count() == 0
    # still on the watch list, but its check counter moved
    watch = state.get_soft_fail_watch()
    assert len(watch) == 1
    assert watch[0]["free_checks_done"] == 1


def test_watch_cycle_handles_rpc_failure_gracefully(monkeypatch):
    state.watch_add("token-rpc-down", "solana", True, 35, [])
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals",
                         lambda mint: {"top10_holder_pct": None, "holder_growth_rate_per_hr": None})
    queued = scheduler._run_soft_fail_watch_cycle()
    assert queued == 0
    assert state.get_soft_fail_watch()[0]["free_checks_done"] == 1


def test_watch_cycle_skips_robinhood_chain_entries(monkeypatch):
    # Shouldn't normally happen (see test_band_d_robinhood_chain_not_added_
    # to_watch_list), but defends the sweep itself against ever spending an
    # SPL-specific RPC call on a non-Solana chain if one somehow got in.
    watch = state.get_value(state.SOFT_FAIL_WATCH_KEY) or []
    watch.append({"token": "rhc-token", "chain": "robinhood_chain", "is_pregraduation": False,
                  "first_seen_ts": __import__("time").time(), "last_checked_ts": 0,
                  "last_score": 35, "last_reasons": [], "free_checks_done": 0})
    state.set_value(state.SOFT_FAIL_WATCH_KEY, watch)

    def boom(mint):
        raise AssertionError("should never call free_recheck_solana_signals for a non-solana chain")
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals", boom)

    queued = scheduler._run_soft_fail_watch_cycle()
    assert queued == 0


def test_watch_cycle_respects_per_cycle_cap(monkeypatch):
    import time as time_mod
    now = time_mod.time()
    for i in range(scheduler.SOFT_FAIL_RECHECK_MAX_PER_CYCLE + 5):
        state.watch_add(f"token-{i}", "solana", True, 35, [], ts=now - i)
    calls = []
    def fake_recheck(mint):
        calls.append(mint)
        return {"top10_holder_pct": 0.55, "holder_growth_rate_per_hr": None}  # never clears -> stays watched
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals", fake_recheck)
    scheduler._run_soft_fail_watch_cycle()
    assert len(calls) == scheduler.SOFT_FAIL_RECHECK_MAX_PER_CYCLE


def test_watch_cycle_empty_list_is_a_noop(monkeypatch):
    def boom(mint):
        raise AssertionError("should not be called on an empty watch list")
    monkeypatch.setattr(scheduler, "free_recheck_solana_signals", boom)
    assert scheduler._run_soft_fail_watch_cycle() == 0

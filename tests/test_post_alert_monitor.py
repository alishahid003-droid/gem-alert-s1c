"""
Tests for the post-alert monitoring pass (Ali, Sept 28 2026 -- the real gap
he flagged live tonight: RICH OFF GTA 6 was scored -8.54% off peak at scan
time and slipped past the launch-window collapse override (only fires at
<=-60%), but a token that hasn't collapsed YET at scoring time can still
collapse a few minutes later, and nothing re-checked it -- "a flagged token
that rugs 10 minutes later still shows as a live alert with no correction"
(checklist item, previously unbuilt). This covers the three pieces that
close that gap: scheduler._handle_scored queuing a real, delivered alert
into the post-alert monitor (state.py), and
scheduler._run_post_alert_monitor_cycle deciding, once the 15-60 min window
has real Birdeye price history, whether to send a DOWNGRADE follow-up.
"""
import time

import pytest

import state
import scheduler
from layers.layer0_scoring import ScoreResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _scored(address, band, score=90, reasons=None):
    return {"chain": "ignored", "address": address,
            "score": ScoreResult(score=score, band=band, liquidity_flag="deep", reasons=reasons or [])}


# --- _handle_scored wiring: a real, delivered alert gets queued for follow-up ---

def test_band_a_solana_alert_queues_post_alert_monitor(monkeypatch):
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    scored = _scored("token-a-solana", "A", score=90)
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    queue = state.get_post_alert_monitor()
    assert len(queue) == 1
    assert queue[0]["token"] == "token-a-solana"
    assert queue[0]["chain"] == "solana"
    assert queue[0]["band"] == "A"


def test_robinhood_chain_alert_not_queued_no_birdeye_mapping(monkeypatch):
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    scored = _scored("token-a-rhc", "A", score=90)
    scheduler._handle_scored(scored, "robinhood_chain", source="test", mc=50000.0)
    assert state.get_post_alert_monitor() == []


def test_alert_not_actually_sent_is_not_queued(monkeypatch):
    # send_alert failing (e.g. Telegram not configured in tests) means
    # nothing was actually delivered -- nothing to follow up on.
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": False, "reason": "no creds"})
    scored = _scored("token-undelivered", "A", score=90)
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    assert state.get_post_alert_monitor() == []


def test_high_risk_momentum_alert_also_queued(monkeypatch):
    now = time.time()
    state.record_mc_point("token-momentum", 5_000.0, ts=now - 600)
    state.record_mc_point("token-momentum", 150_000.0, ts=now)
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    scored = _scored("token-momentum", "D", score=20)
    scheduler._handle_scored(scored, "solana", source="test", mc=None, is_pregraduation=True)
    queue = state.get_post_alert_monitor()
    assert len(queue) == 1
    assert queue[0]["token"] == "token-momentum"


def test_repeat_alert_same_token_updates_not_duplicates(monkeypatch):
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    scored = _scored("token-repeat", "A", score=70)
    scheduler._handle_scored(scored, "solana", source="test", mc=50000.0)
    scored2 = _scored("token-repeat", "A", score=85)
    scheduler._handle_scored(scored2, "solana", source="test", mc=60000.0)
    queue = state.get_post_alert_monitor()
    assert len(queue) == 1
    assert queue[0]["score"] == 85


# --- _run_post_alert_monitor_cycle: the 15-60 min follow-up check itself ---

def test_cycle_skips_entries_younger_than_15_minutes(monkeypatch):
    state.post_alert_monitor_add("token-fresh", "solana", "headline", "A", 90, ts=time.time() - 300)

    def boom(*a, **kw):
        raise AssertionError("should not fetch Birdeye for an entry younger than 15 min")
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv", boom)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0
    # still queued -- not yet due for its check
    assert len(state.get_post_alert_monitor()) == 1


def test_cycle_sends_downgrade_on_severe_crater(monkeypatch):
    alert_ts = time.time() - 20 * 60
    state.post_alert_monitor_add("token-crashed", "solana", "Layer 0 structural score", "A", 90, ts=alert_ts)
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv",
                         lambda chain, addr, t_from, t_to, interval="15m": {
                             "ok": True, "candles": [
                                 {"o": 1.0, "h": 1.2, "c": 1.1},
                                 {"o": 1.1, "h": 1.1, "c": 0.3},  # -75% off the $1.2 peak
                             ]})
    captured = {}
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: captured.setdefault("alert", alert) or {"sent": True})
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 1
    assert "alert" in captured
    assert "DOWNGRADE" in captured["alert"].headline
    assert "Exit-risk" in captured["alert"].tags
    # one-time pass -- checked, done, removed either way
    assert state.get_post_alert_monitor() == []


def test_cycle_does_not_downgrade_when_price_held_up(monkeypatch):
    alert_ts = time.time() - 20 * 60
    state.post_alert_monitor_add("token-held", "solana", "headline", "A", 90, ts=alert_ts)
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv",
                         lambda chain, addr, t_from, t_to, interval="15m": {
                             "ok": True, "candles": [
                                 {"o": 1.0, "h": 1.2, "c": 1.15},
                                 {"o": 1.15, "h": 1.3, "c": 1.25},
                             ]})

    def boom(alert):
        raise AssertionError("should not send a downgrade when price held up")
    monkeypatch.setattr(scheduler, "send_alert", boom)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0
    assert state.get_post_alert_monitor() == []


def test_cycle_handles_birdeye_failure_gracefully(monkeypatch):
    alert_ts = time.time() - 20 * 60
    state.post_alert_monitor_add("token-birdeye-down", "solana", "headline", "A", 90, ts=alert_ts)
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv",
                         lambda chain, addr, t_from, t_to, interval="15m": {"ok": False, "reason": "network unreachable"})

    def boom(alert):
        raise AssertionError("should not send anything on a fetch failure")
    monkeypatch.setattr(scheduler, "send_alert", boom)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0
    # one-time pass -- removed even though the check itself failed; the
    # window it cared about has now passed either way
    assert state.get_post_alert_monitor() == []


def test_cycle_skips_robinhood_chain_entries_defensively(monkeypatch):
    # Shouldn't normally happen (see test_robinhood_chain_alert_not_queued_
    # no_birdeye_mapping), but defends the sweep itself against ever
    # spending a Birdeye call on a chain it has no mapping for, if one
    # somehow got in.
    queue = state.get_value(state.POST_ALERT_MONITOR_KEY) or []
    queue.append({"token": "rhc-token", "chain": "robinhood_chain", "headline": "h", "band": "A",
                  "score": 90, "alert_ts": time.time() - 20 * 60})
    state.set_value(state.POST_ALERT_MONITOR_KEY, queue)

    def boom(*a, **kw):
        raise AssertionError("should never call fetch_birdeye_ohlcv for a non-Birdeye chain")
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv", boom)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0


def test_cycle_respects_per_cycle_cap(monkeypatch):
    now = time.time()
    for i in range(scheduler.POST_ALERT_MONITOR_MAX_PER_CYCLE + 5):
        state.post_alert_monitor_add(f"token-{i}", "solana", "headline", "A", 90, ts=now - 20 * 60 - i)
    calls = []

    def fake_fetch(chain, addr, t_from, t_to, interval="15m"):
        calls.append(addr)
        return {"ok": True, "candles": [{"o": 1.0, "h": 1.0, "c": 0.95}]}
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv", fake_fetch)
    scheduler._run_post_alert_monitor_cycle()
    assert len(calls) == scheduler.POST_ALERT_MONITOR_MAX_PER_CYCLE


def test_cycle_empty_queue_is_a_noop(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("should not be called on an empty queue")
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv", boom)
    assert scheduler._run_post_alert_monitor_cycle() == 0

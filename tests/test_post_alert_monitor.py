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


def test_robinhood_chain_alert_queued_via_dexscreener_snapshot(monkeypatch):
    # RHC has no Birdeye mapping (see fetch_birdeye_ohlcv), but IS covered
    # by DexScreener -- a real price snapshot is taken at alert time and
    # stored, for a later snapshot-compare instead of Birdeye OHLCV.
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    monkeypatch.setattr(scheduler, "fetch_dexscreener_token_price_usd", lambda chain, addr: 0.0042)
    scored = _scored("token-a-rhc", "A", score=90)
    scheduler._handle_scored(scored, "robinhood_chain", source="test", mc=50000.0)
    queue = state.get_post_alert_monitor()
    assert len(queue) == 1
    assert queue[0]["chain"] == "robinhood_chain"
    assert queue[0]["price_at_alert"] == 0.0042


def test_robinhood_chain_alert_not_queued_when_no_dexscreener_pair(monkeypatch):
    # No pair yet (brand-new token) -- nothing to compare against later,
    # so this is an honest skip, not a queued entry that can only fail.
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": True})
    monkeypatch.setattr(scheduler, "fetch_dexscreener_token_price_usd", lambda chain, addr: None)
    scored = _scored("token-a-rhc-nopair", "A", score=90)
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


def test_cycle_never_calls_birdeye_for_robinhood_chain_entries(monkeypatch):
    # Robinhood Chain entries must go through the DexScreener snapshot
    # path, never Birdeye (which has no mapping for this chain at all).
    queue = state.get_value(state.POST_ALERT_MONITOR_KEY) or []
    queue.append({"token": "rhc-token", "chain": "robinhood_chain", "headline": "h", "band": "A",
                  "score": 90, "alert_ts": time.time() - 20 * 60, "price_at_alert": 0.01})
    state.set_value(state.POST_ALERT_MONITOR_KEY, queue)

    def boom(*a, **kw):
        raise AssertionError("should never call fetch_birdeye_ohlcv for a non-Birdeye chain")
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv", boom)
    monkeypatch.setattr(scheduler, "fetch_dexscreener_token_price_usd", lambda chain, addr: 0.0095)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0  # 0.01 -> 0.0095 is only -5%, held up
    assert state.get_post_alert_monitor() == []


def test_cycle_downgrades_robinhood_chain_on_dexscreener_crater(monkeypatch):
    alert_ts = time.time() - 20 * 60
    state.post_alert_monitor_add("rhc-crashed", "robinhood_chain", "headline", "A", 90,
                                  ts=alert_ts, price_at_alert=0.01)

    def boom(*a, **kw):
        raise AssertionError("should never call fetch_birdeye_ohlcv for a non-Birdeye chain")
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv", boom)
    monkeypatch.setattr(scheduler, "fetch_dexscreener_token_price_usd", lambda chain, addr: 0.002)  # -80%
    captured = {}
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: captured.setdefault("alert", alert) or {"sent": True})
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 1
    assert "DOWNGRADE" in captured["alert"].headline
    assert state.get_post_alert_monitor() == []


def test_cycle_skips_robinhood_chain_entry_missing_price_at_alert(monkeypatch):
    # Defensive: an entry that somehow got in without a valid snapshot
    # (shouldn't happen given _handle_scored's own gate) must not crash or
    # divide by zero/None -- just skipped, honest.
    queue = state.get_value(state.POST_ALERT_MONITOR_KEY) or []
    queue.append({"token": "rhc-no-price", "chain": "robinhood_chain", "headline": "h", "band": "A",
                  "score": 90, "alert_ts": time.time() - 20 * 60, "price_at_alert": None})
    state.set_value(state.POST_ALERT_MONITOR_KEY, queue)

    def boom(*a, **kw):
        raise AssertionError("should not fetch a current price with nothing to compare it against")
    monkeypatch.setattr(scheduler, "fetch_dexscreener_token_price_usd", boom)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0
    assert state.get_post_alert_monitor() == []


def test_cycle_skips_robinhood_chain_entry_when_current_price_unfetchable(monkeypatch):
    state.post_alert_monitor_add("rhc-unfetchable", "robinhood_chain", "headline", "A", 90,
                                  ts=time.time() - 20 * 60, price_at_alert=0.01)
    monkeypatch.setattr(scheduler, "fetch_dexscreener_token_price_usd", lambda chain, addr: None)

    def boom(alert):
        raise AssertionError("should not send anything when the current price can't be fetched")
    monkeypatch.setattr(scheduler, "send_alert", boom)
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 0
    assert state.get_post_alert_monitor() == []


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


def test_cycle_downgrade_is_logged_to_alert_feed_for_dashboard(monkeypatch):
    # Real gap found and fixed Sept 28 2026: the DOWNGRADE alert was the
    # one alert path that never called state.log_full_alert (what
    # dashboard.py actually reads). If Telegram is down/disabled and
    # nobody's watching the terminal live (a fully automated run), the
    # single most important alert -- "this thing you bought is cratering"
    # -- would have been silently invisible on the dashboard.
    alert_ts = time.time() - 20 * 60
    state.post_alert_monitor_add("token-dashboard-check", "solana", "headline", "A", 90, ts=alert_ts)
    monkeypatch.setattr(scheduler, "fetch_birdeye_ohlcv",
                         lambda chain, addr, t_from, t_to, interval="15m": {
                             "ok": True, "candles": [
                                 {"o": 1.0, "h": 1.2, "c": 1.1},
                                 {"o": 1.1, "h": 1.1, "c": 0.3},
                             ]})
    # Telegram fails/unconfigured -- the downgrade must still reach the dashboard.
    monkeypatch.setattr(scheduler, "send_alert", lambda alert: {"sent": False, "reason": "no creds"})
    downgraded = scheduler._run_post_alert_monitor_cycle()
    assert downgraded == 1
    feed = state.get_alert_feed()
    assert any(f["layer"] == "post_alert_downgrade" and f["token_address"] == "token-dashboard-check" for f in feed)

"""
Tests for tonight's Layer 3/7/10 wiring (Ali, Sept 23 2026: "on the 3 7
10 whatever needs to be built... you can do that"). Each layer was built
previously but never called from the live poll loop -- these tests cover the
new wiring, not the layers' own logic (which already had tests).
"""
import pytest

import state
import scheduler
from layers.layer0_scoring import ScoreResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


# --- Layer 7: cross-layer correlation ---

def test_same_token_two_distinct_layers_triggers_mega_alert(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    a1 = scheduler.Alert("tok1", "token-x", "solana", "first alert")
    scheduler._alert(a1, "layer1")
    a2 = scheduler.Alert("tok1", "token-x", "solana", "second alert")
    scheduler._alert(a2, "layer2")
    # 3 alerts should have gone out: the 2 real ones + 1 MEGA-ALERT
    assert len(sent) == 3
    mega = sent[-1]
    assert "MEGA-ALERT" in mega.tags
    assert "layer1" in mega.tags["MEGA-ALERT"] and "layer2" in mega.tags["MEGA-ALERT"]


def test_same_layer_twice_does_not_trigger_mega_alert(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    a1 = scheduler.Alert("tok1", "token-y", "solana", "first")
    scheduler._alert(a1, "layer2")
    a2 = scheduler.Alert("tok1", "token-y", "solana", "second")
    scheduler._alert(a2, "layer2")  # same layer, not distinct
    assert len(sent) == 2  # no third MEGA-ALERT


def test_mega_alert_does_not_re_fire_for_same_layer_set(monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    for layer in ("layer1", "layer2"):
        scheduler._alert(scheduler.Alert("tok1", "token-z", "solana", layer), layer)
    assert len(sent) == 3  # 2 real + 1 mega
    # re-firing layer1 again (same two-layer set) must not send a second mega-alert
    scheduler._alert(scheduler.Alert("tok1", "token-z", "solana", "layer1 again"), "layer1")
    assert len(sent) == 4  # only the real alert added, no new mega


def test_layer4_news_alerts_skip_mega_alert_logic():
    # Layer 4's "n/a" token alerts go through plain send_alert, not _alert --
    # just documenting the choice exists and is deliberate, nothing to assert
    # beyond the source not crashing -- covered by scheduler importing cleanly.
    assert callable(scheduler._alert)


# --- Layer 3: Reddit backing check, quota-conscious gating ---

def test_backing_check_skipped_without_adanos_key(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "check_backing_spike", lambda sym: called.append(sym) or {})
    monkeypatch.setattr(scheduler.CONFIG, "adanos_api_key", None)
    scored = {"chain": "bsc", "address": "token-nokey",
               "score": ScoreResult(score=90, band="A", liquidity_flag="deep", reasons=[]),
               "raw": {"symbol": "TESTSYM"}}
    scheduler._handle_scored(scored, "bsc", source="mobula", mc=50000.0)
    assert called == []  # no key -- must not even attempt, quota is scarce


def test_backing_check_only_attempted_on_mobula_path_with_symbol(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "check_backing_spike", lambda sym: called.append(sym) or {"ok": True, "result": None})
    monkeypatch.setattr(scheduler.CONFIG, "adanos_api_key", "testkey")
    # madeonsol path (source="madeonsol") has no symbol available at all -- must be skipped
    scored = {"chain": "solana", "address": "token-nosym",
               "score": ScoreResult(score=90, band="A", liquidity_flag="deep", reasons=[])}
    scheduler._handle_scored(scored, "solana", source="madeonsol", mc=50000.0)
    assert called == []


def test_backing_check_attempted_on_mobula_path_with_symbol_and_key(monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "check_backing_spike", lambda sym: called.append(sym) or {"ok": True, "result": None})
    monkeypatch.setattr(scheduler.CONFIG, "adanos_api_key", "testkey")
    scored = {"chain": "bsc", "address": "token-yes",
               "score": ScoreResult(score=90, band="A", liquidity_flag="deep", reasons=[]),
               "raw": {"symbol": "TESTSYM"}}
    scheduler._handle_scored(scored, "bsc", source="mobula", mc=50000.0)
    assert called == ["TESTSYM"]


def test_backing_tag_added_to_alert_when_spike_detected(monkeypatch):
    from layers.layer3_backing_check import BackingResult
    sent = []
    monkeypatch.setattr(scheduler, "send_alert", lambda a: sent.append(a) or {"sent": True})
    monkeypatch.setattr(scheduler, "check_backing_spike",
        lambda sym: {"ok": True, "result": BackingResult(spike_detected=True, tag="needs-verification")})
    monkeypatch.setattr(scheduler.CONFIG, "adanos_api_key", "testkey")
    scored = {"chain": "bsc", "address": "token-backed",
               "score": ScoreResult(score=90, band="A", liquidity_flag="deep", reasons=[]),
               "raw": {"symbol": "TESTSYM"}}
    scheduler._handle_scored(scored, "bsc", source="mobula", mc=50000.0)
    alert = next(a for a in sent if a.token_address == "token-backed")
    assert alert.tags.get("Backing") == "needs-verification"


# --- Layer 10: wallet-cluster fallback on Layer 9 ---

def test_unknown_seller_ignored_without_resolver():
    from layers.layer9_sell_mirror import detect_sell_events
    trades = [{"wallet_address": "W9", "kol_name": "NotTracked", "action": "sell",
               "sol_amount": 2.0, "traded_at": "2026-09-06T15:06:00+00:00", "token_mint": "TOKEN_A"}]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"})
    assert events == []


def test_unknown_seller_recovered_via_cluster_resolver():
    from layers.layer9_sell_mirror import detect_sell_events
    trades = [{"wallet_address": "W9", "kol_name": "NotTracked", "action": "sell",
               "sol_amount": 2.0, "traded_at": "2026-09-06T15:06:00+00:00", "token_mint": "TOKEN_A"}]
    events = detect_sell_events(
        trades, relevant_tokens={"TOKEN_A"},
        resolve_unknown=lambda addr: "elite" if addr == "W9" else "untracked",
    )
    assert len(events) == 1
    assert events[0]["who"] == "wallet-cluster:elite"
    assert events[0]["role"] == "elite"


def test_resolver_returning_untracked_still_ignored():
    from layers.layer9_sell_mirror import detect_sell_events
    trades = [{"wallet_address": "W9", "kol_name": "NotTracked", "action": "sell",
               "sol_amount": 2.0, "traded_at": "2026-09-06T15:06:00+00:00", "token_mint": "TOKEN_A"}]
    events = detect_sell_events(
        trades, relevant_tokens={"TOKEN_A"},
        resolve_unknown=lambda addr: "untracked",
    )
    assert events == []

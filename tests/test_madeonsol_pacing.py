"""Checklist 5.7 (Sept 30 2026): MadeOnSol budget paced across the UTC day."""
import pytest

import state
import layers.kol_feed as kol_feed
from config import CONFIG

HOUR = 3600
DAY0 = 1_790_726_400  # a UTC midnight


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def test_routine_paced_evenly_in_first_hour():
    now = DAY0 + 10 * 60  # 00:10 UTC -> 1/24 of the day released
    allowed = int(state.MADEONSOL_DAILY_BUDGET * state.MADEONSOL_ROUTINE_SHARE / 24)  # ~3
    for _ in range(allowed):
        assert state.madeonsol_can_spend(1, priority=False, now=now)
        state.record_madeonsol_routine_calls(1)
    assert state.madeonsol_can_spend(1, priority=False, now=now) is False


def test_priority_can_run_ahead_but_not_past_budget():
    now = DAY0 + 10 * 60
    state.record_madeonsol_calls(20)
    assert state.madeonsol_can_spend(3, priority=True, now=now)  # lead allowance ~23
    state.record_madeonsol_calls(state.MADEONSOL_DAILY_BUDGET - 21)
    assert state.madeonsol_can_spend(3, priority=True, now=DAY0 + 23 * HOUR) is False


def test_routine_does_not_starve_priority():
    now = DAY0 + 12 * HOUR
    for _ in range(200):
        if state.madeonsol_can_spend(1, priority=False, now=now):
            state.record_madeonsol_routine_calls(1)
    assert state.madeonsol_calls_today() <= state.MADEONSOL_DAILY_BUDGET * state.MADEONSOL_ROUTINE_SHARE
    assert state.madeonsol_can_spend(3, priority=True, now=now)


def test_kol_feed_counts_calls_and_respects_pace(monkeypatch):
    monkeypatch.setattr(CONFIG, "madeonsol_api_key", "k")
    monkeypatch.setattr(kol_feed, "fetch_kol_feed_raw", lambda chain, limit, action=None: {
        "ok": True, "raw": {"json": {"trades": [{"action": "buy"}, {"action": "sell"}]}}})
    before = state.madeonsol_calls_today()
    out = kol_feed.fetch_kol_feed_both("solana")
    assert out["ok"] and out["calls_made"] == 1
    assert state.madeonsol_calls_today() == before + 1
    monkeypatch.setattr(state, "madeonsol_can_spend", lambda n, priority=False, now=None: False)
    paced = kol_feed.fetch_kol_feed_both("solana")
    assert paced["ok"] is False and paced["mode"] == "paced"


def test_replay_archive_keeps_first_alert_per_coin(tmp_path, monkeypatch):
    import state
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "arch.json"))
    state.log_full_alert("layer0b", "base", "X", "0xA", "h", {"Score": "72/100 band B"})
    state.log_full_alert("layer0b", "base", "X", "0xA", "h", {"Score": "74/100 band B"})
    state.log_full_alert("layer0b", "base", "Y", "0xB", "h", {"Chain": "base"})     # no band: not archived
    arch = state.get_replay_archive()
    assert [a["token_address"] for a in arch] == ["0xA"] and "band B" in arch[0]["tags"]["Score"]


def test_fomo_buy_archive_dedupes(tmp_path, monkeypatch):
    import time as _t
    import state
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "fa.json"))
    now = _t.time()
    state.append_fomo_buy_archive([{"trader": "a", "chain": "solana", "token_address": "M", "ts": now}])
    state.append_fomo_buy_archive([{"trader": "a", "chain": "solana", "token_address": "M", "ts": now},
                                   {"trader": "b", "chain": "solana", "token_address": "M", "ts": now}])
    assert len(state.get_fomo_buy_archive()) == 2

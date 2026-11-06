"""6 Nov 2026 (Ali): "Can you build a separate layer, a separate paper test
layer... without touching the existing code of our 4 modules which are
running and the Solana separate modules which are running?... A separate
environment, separate thing, or a separate module altogether in the
system, which does not disturb the operation of my current system."

Tests executor.aggressive_lane -- the standalone 2x/3x-exposure paper lane.
Covers both (a) its own trading logic in isolation, using a fully mocked
price feed (no real network calls in tests), and (b) the isolation
guarantee itself: that running the lane never writes to any existing
real-book state key.
"""
import time

import pytest

import state as st
from executor import aggressive_lane as al


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "LOCAL_STATE_FILE", str(tmp_path / "state.json"))
    yield


def _alert(addr, chain="solana", score="band A", layer="layer0b", symbol="TEST"):
    return {"ts": time.time(), "layer": layer, "chain": chain, "token_symbol": symbol,
            "token_address": addr, "headline": "test alert", "tags": {"Score": score}}


def test_does_not_touch_any_real_book_state_key(monkeypatch):
    """The core isolation guarantee: running full cycles never writes to
    books_v1, milestone_ladder_v2, or paper_closed_positions -- the real
    system's own keys -- only to this lane's own 'aggressive_lane_v1' key."""
    st.set_value("books_v1", {"sprint": {"marker": "untouched"}})
    st.set_value("milestone_ladder_v2", {"marker": "untouched"})
    st.set_value("paper_closed_positions", [{"marker": "untouched"}])
    st.set_value(al.ALERT_FEED_KEY if hasattr(al, "ALERT_FEED_KEY") else "dashboard_alert_feed",
                 [_alert("addr1")])

    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": 50_000.0})

    for _ in range(3):
        al.run_cycle()

    assert st.get_value("books_v1") == {"sprint": {"marker": "untouched"}}
    assert st.get_value("milestone_ladder_v2") == {"marker": "untouched"}
    assert st.get_value("paper_closed_positions") == [{"marker": "untouched"}]


def test_opens_paper_position_sized_with_exposure_multiplier(monkeypatch):
    monkeypatch.setattr(al, "EXPOSURE_MULTIPLIER", 3.0)
    monkeypatch.setattr(al, "BASE_STAKE_PCT_OF_EQUITY", 0.10)
    monkeypatch.setattr(al, "SEED_USD", 100.0)
    st.set_value("dashboard_alert_feed", [_alert("addr1")])
    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": 10_000.0})

    st_result = al.run_cycle()

    assert st_result["open_positions"] == 1
    s = st.get_value(al.STATE_KEY)
    pos = s["open_positions"]["addr1"]
    # 10% of $100 equity = $10 base stake, x3 exposure = $30
    assert pos["stake_usd"] == pytest.approx(30.0)
    assert s["equity_usd"] == pytest.approx(70.0)


def test_ladder_leg_takes_partial_profit_and_leaves_remainder_open(monkeypatch):
    monkeypatch.setattr(al, "EXPOSURE_MULTIPLIER", 1.0)
    monkeypatch.setattr(al, "BASE_STAKE_PCT_OF_EQUITY", 0.50)
    monkeypatch.setattr(al, "SEED_USD", 100.0)
    st.set_value("dashboard_alert_feed", [_alert("addr1")])
    prices = {"addr1": 10_000.0}
    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": prices[addr]})

    al.run_cycle()  # opens $50 stake at mcap 10,000

    prices["addr1"] = 15_000.0  # 1.5x -> first ladder leg (sell 33% of remaining)
    al.run_cycle()

    s = st.get_value(al.STATE_KEY)
    pos = s["open_positions"]["addr1"]
    assert "1.5" in pos["ladder_fired"]
    assert pos["remaining_fraction"] == pytest.approx(0.67, abs=0.001)
    assert len(s["closed_trades"]) == 1
    assert s["closed_trades"][0]["realized_usd"] == pytest.approx(50.0 * 0.33 * 1.5)


def test_hard_stop_closes_full_remaining_position(monkeypatch):
    monkeypatch.setattr(al, "EXPOSURE_MULTIPLIER", 1.0)
    monkeypatch.setattr(al, "BASE_STAKE_PCT_OF_EQUITY", 0.20)
    monkeypatch.setattr(al, "SEED_USD", 100.0)
    monkeypatch.setattr(al, "LANE_STOP_LOSS_PCT", 0.35)
    st.set_value("dashboard_alert_feed", [_alert("addr1")])
    prices = {"addr1": 10_000.0}
    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": prices[addr]})

    al.run_cycle()  # opens $20 stake
    prices["addr1"] = 6_000.0  # -40% -> below the -35% stop
    al.run_cycle()

    s = st.get_value(al.STATE_KEY)
    assert "addr1" not in s["open_positions"]
    assert s["closed_trades"][-1]["why"] == "stop_loss"
    # equity should reflect the loss: started $100, staked $20, got back 0.6x of it
    assert s["equity_usd"] == pytest.approx(100.0 - 20.0 + 20.0 * 0.6)


def test_does_not_open_more_than_max_concurrent(monkeypatch):
    monkeypatch.setattr(al, "MAX_CONCURRENT_POSITIONS", 2)
    monkeypatch.setattr(al, "EXPOSURE_MULTIPLIER", 1.0)
    monkeypatch.setattr(al, "BASE_STAKE_PCT_OF_EQUITY", 0.10)
    monkeypatch.setattr(al, "SEED_USD", 100.0)
    st.set_value("dashboard_alert_feed", [_alert("a1"), _alert("a2"), _alert("a3")])
    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": 10_000.0})

    al.run_cycle()

    s = st.get_value(al.STATE_KEY)
    assert len(s["open_positions"]) == 2


def test_target_hit_stops_the_lane(monkeypatch):
    monkeypatch.setattr(al, "TARGET_USD", 150.0)
    s = al._default_state()
    s["equity_usd"] = 200.0
    st.set_value(al.STATE_KEY, s)

    result = al.run_cycle()

    assert result["status"] == "target_hit"


def test_ineligible_alerts_are_skipped(monkeypatch):
    st.set_value("dashboard_alert_feed", [_alert("addr1", score="band C", layer="layer0b")])
    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": 10_000.0})

    result = al.run_cycle()

    assert result["open_positions"] == 0


def test_band_b_is_eligible_after_6nov_widening(monkeypatch):
    """6 Nov 2026 (Ali, revised): band A/S almost never fires in the real
    feed (confirmed: 0 of the most recent 300 real alerts), so band B --
    the real top tier this system actually produces -- was added so the
    lane gets enough real signal volume to mean something over 48h."""
    st.set_value("dashboard_alert_feed", [_alert("addr1", score="71/100 (band b)", layer="layer0b")])
    monkeypatch.setattr(al, "fetch_dexscreener_snapshot", lambda chain, addr: {"mcap_usd": 10_000.0})

    result = al.run_cycle()

    assert result["open_positions"] == 1

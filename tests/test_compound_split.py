"""3 Nov 2026 (Ali): "from $100 to $500 once it reaches, it should distribute $200 to the three
modules ... make his own one from the remaining $300 ... those modules start operating with the
$200, which is split amongst the three ... same very logic that if someone's capital is sitting
idle ... it can pick up that spare capital as well."

Tests executor.books.check_compound_split() -- the one-time, automatic trigger that fires the
first time compound's total_value() crosses $500, banks $200 out of compound (via the same
CASHOUT_KEY mechanism check_cashout() uses), and deposits it as fresh seed money (via the new
DEPOSITS_KEY/deposited()) split evenly across sprint/core1/core2."""
import time

import pytest

import state as st
from executor import books, paper_ledger as pl


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "LOCAL_STATE_FILE", str(tmp_path / "state.json"))
    yield


def _seed_compound_closed_trade(pnl_usd: float, now: float):
    closed = [{"module": "compound", "pnl_usd": pnl_usd, "closed_ts": now,
               "opened_ts": now - 3600, "id": "sim1", "strategy": "early"}]
    st.set_value(pl.CLOSED_KEY, closed)


def test_does_not_fire_below_trigger():
    now = time.time()
    _seed_compound_closed_trade(399.0, now)   # compound at $499, just under $500
    assert books.total_value("compound") == 499.0
    result = books.check_compound_split(now)
    assert result is None
    for b in ("sprint", "core1", "core2"):
        assert books.total_value(b) == 100.0   # untouched


def test_fires_exactly_once_at_trigger_and_splits_evenly():
    now = time.time()
    _seed_compound_closed_trade(440.0, now)   # compound at $540
    result = books.check_compound_split(now)
    assert result is not None
    assert result["fired"] is True
    assert result["banked_from_compound"] == 200.0
    # three-way split of $200: one recipient gets the rounding remainder, sums to exactly $200.00
    assert round(sum(result["shares"].values()), 2) == 200.00
    assert set(result["shares"]) == {"sprint", "core1", "core2"}

    assert books.total_value("compound") == 340.0   # $540 - $200, the REAL remainder, not a fixed $300
    for b in ("sprint", "core1", "core2"):
        assert books.total_value(b) == pytest.approx(100.0 + result["shares"][b], abs=0.01)

    # never fires twice, even if called again with compound still well above $500
    assert books.check_compound_split(now) is None


def test_compound_remaining_reflects_real_overshoot_not_flat_300():
    """Ali's example used $500->$300 remaining, but the real rule is total_value - $200 -- a
    book that overshoots to $560 before the next check keeps $360, not a flat $300."""
    now = time.time()
    _seed_compound_closed_trade(460.0, now)   # compound at $560
    result = books.check_compound_split(now)
    assert result["compound_remaining"] == 360.0
    assert books.total_value("compound") == 360.0


def test_wired_into_can_open_for_all_four_real_books():
    """check_compound_split() must fire from can_open() on ANY of the 4 real books' cycle, not
    just when compound itself is checked -- the live poll loop may hit sprint/core1/core2 first."""
    now = time.time()
    _seed_compound_closed_trade(440.0, now)
    books.can_open("sprint")   # not compound -- must still trigger the split
    assert books.total_value("compound") == 340.0
    assert books.total_value("sprint") > 100.0

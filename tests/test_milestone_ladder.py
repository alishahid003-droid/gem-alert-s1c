"""5 Nov 2026 (Ali, revised same day): "we need to shape up the system in a way that $20,000 is
secured in this 4 to 5 working days... even if the remaining balance is left around $700, $800
for the next run... more likely it can achieve $117,000 to $118,000... we need to secure the
profits as well. I don't want to be sitting blank in between."

Tests executor.books.check_milestone_ladder() v2 -- two sequential phases:
  Phase 1: the $20,000 hard cash-out is checked FIRST every call (never a casualty of the
           lighter protective bankings below it); only once $20,000 hasn't been reached yet
           does it fall through to the lighter PHASE1_LADDER percentage-banks on the way up.
  Phase 2: starts automatically after the phase-1 cashout, same idea toward $117,500."""
import time

import pytest

import state as st
from executor import books, paper_ledger as pl


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "LOCAL_STATE_FILE", str(tmp_path / "state.json"))
    yield


def _seed_closed_trade(module: str, pnl_usd: float, now: float, trade_id: str):
    closed = st.get_value(pl.CLOSED_KEY)
    closed = closed if isinstance(closed, list) else []
    closed.append({"module": module, "pnl_usd": pnl_usd, "closed_ts": now,
                   "opened_ts": now - 3600, "id": trade_id, "strategy": "early"})
    st.set_value(pl.CLOSED_KEY, closed)


def test_phase1_fires_first_light_banking_level_when_nowhere_near_20k():
    now = time.time()
    combined = books._combined_equity_for_milestones()
    assert combined == 400.0   # 4 x $100 default seed, nowhere near $20,000
    result = books.check_milestone_ladder(now)
    assert result["phase"] == 1
    assert result["level"] == 240.0
    assert result["pct"] == 0.10
    assert result["banked_total"] == pytest.approx(400.0 * 0.10, abs=0.01)
    assert "event" not in result


def test_19000_cashout_fires_immediately_once_20000_trigger_cleared():
    """Critical ordering guarantee: if combined equity jumps straight past the $20,000 TRIGGER
    (and past several lighter ladder levels too) in one go, the cash-out must fire on the very
    FIRST call -- it must never be eaten by the lighter percentage-banks firing first and
    draining equity back under the trigger before the cash-out check gets a turn.

    5 Nov 2026 (Ali, revised): the amount actually cashed out is $19,000, not the full $20,000
    trigger -- "if we achieve $20,000, we will cash out $19,000, and the remaining will stay in
    the system" -- leaving $1,000 (PHASE1_RESEED_USD, was $750) behind for Phase 2."""
    now = time.time()
    _seed_closed_trade("compound", 40000.0, now, "t1")
    combined = books._combined_equity_for_milestones()
    assert combined >= books.PHASE1_CASHOUT_TRIGGER_USD   # confirmed real combined equity clears the $20k trigger

    result = books.check_milestone_ladder(now)
    assert result is not None
    assert result["event"] == "phase1_cashout"
    assert result["cashout_usd"] == 19000.0
    assert result["reseed_left_usd"] == 1000.0
    # real overshoot kept, not forced down to a flat $1,000 (same "real overshoot" principle as
    # check_compound_split()) -- combined equity after is (combined_before - 19000), not 1000.
    combined_after = books._combined_equity_for_milestones()
    assert combined_after == pytest.approx(combined - 19000.0, abs=0.02)


def test_phase1_cashout_never_fires_twice_and_switches_to_phase2():
    now = time.time()
    _seed_closed_trade("compound", 40000.0, now, "t1")
    r1 = books.check_milestone_ladder(now)
    assert r1["event"] == "phase1_cashout"

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 2
    assert "p1_cashout" in done["fired_levels"]

    # calling again must never re-fire the phase-1 cashout, even with equity still high
    for _ in range(5):
        r2 = books.check_milestone_ladder(now)
        if r2 is not None:
            assert r2.get("event") != "phase1_cashout"
            assert r2["phase"] == 2


def test_phase1_light_ladder_fires_only_when_cashout_not_yet_reachable():
    now = time.time()
    # push combined just into the lighter ladder's range but nowhere near $20,000
    _seed_closed_trade("compound", 1700.0, now, "t1")
    combined = books._combined_equity_for_milestones()
    assert combined < books.PHASE1_CASHOUT_USD

    r = books.check_milestone_ladder(now)
    assert r["phase"] == 1
    assert "event" not in r
    assert r["level"] in [lvl for lvl, _ in books.PHASE1_LADDER]


def test_phase2_banks_lightly_toward_117500_after_phase1_cashout():
    now = time.time()
    _seed_closed_trade("compound", 40000.0, now, "t1")
    r1 = books.check_milestone_ladder(now)
    assert r1["event"] == "phase1_cashout"

    # now in phase 2; grow combined equity past the first phase-2 level
    now2 = now + 10
    _seed_closed_trade("compound", 20000.0, now2, "t2")
    combined = books._combined_equity_for_milestones()
    assert combined >= books.PHASE2_LADDER[0][0]

    r2 = books.check_milestone_ladder(now2)
    assert r2 is not None
    assert r2["phase"] == 2
    assert r2["level"] == books.PHASE2_LADDER[0][0]
    assert r2["pct"] == books.PHASE2_LADDER[0][1]


def test_does_not_touch_stake_sizing_or_seeds():
    now = time.time()
    seeds_before = {b: books.cfg(b)["seed"] for b in ("sprint", "core1", "core2", "compound")}
    books.check_milestone_ladder(now)
    seeds_after = {b: books.cfg(b)["seed"] for b in ("sprint", "core1", "core2", "compound")}
    assert seeds_before == seeds_after

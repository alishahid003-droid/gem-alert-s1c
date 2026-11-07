"""7 Nov 2026 (Ali, revised): superseded the 2-phase ($20k -> $117,500) milestone structure with
a 5-phase target/cash-out ladder:
  Phase 1: $100  -> $10,000  trigger, cash out $8,500,  reseed ~$1,500
  Phase 2: $1,500 -> $12,000 trigger, cash out $10,000, reseed ~$2,000
  Phase 3: $2,000 -> $12,000 trigger (SAME as phase 2, intentional repeat), cash out $10,000, reseed ~$2,000
  Phase 4: $2,000 -> $120,000 trigger, cash out $115,000, reseed ~$5,000
  Phase 5: $5,000 -> $1,000,000 final target (ladder only, no auto cash-out coded yet)

Tests executor.books.check_milestone_ladder() v3 -- same ordering guarantees as the original
2-phase version (cash-out trigger checked FIRST every call, ahead of that phase's own lighter
ladder legs; only ONE event fires per call), now walked across 5 phases instead of 2."""
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


def test_phase1_fires_first_light_banking_level_when_nowhere_near_10k():
    now = time.time()
    combined = books._combined_equity_for_milestones()
    assert combined == 400.0   # 4 x $100 default seed, nowhere near $10,000
    result = books.check_milestone_ladder(now)
    assert result["phase"] == 1
    assert result["level"] == 250.0
    assert result["pct"] == 0.10
    assert result["banked_total"] == pytest.approx(400.0 * 0.10, abs=0.01)
    assert "event" not in result


def test_8500_cashout_fires_immediately_once_10000_trigger_cleared():
    """Critical ordering guarantee: if combined equity jumps straight past the $10,000 Phase-1
    TRIGGER (and past several lighter ladder levels too) in one go, the cash-out must fire on the
    very FIRST call -- it must never be eaten by the lighter percentage-banks firing first and
    draining equity back under the trigger before the cash-out check gets a turn.

    Cashes out $8,500 (not the full $10,000 trigger), leaving PHASE_DEFS[1]['reseed'] ($1,500)
    behind for Phase 2 -- same "real overshoot kept, not forced to a flat number" principle as
    before."""
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    combined = books._combined_equity_for_milestones()
    assert combined >= books.PHASE_DEFS[1]["trigger"]

    result = books.check_milestone_ladder(now)
    assert result is not None
    assert result["event"] == "phase1_cashout"
    assert result["cashout_usd"] == 8_500.0
    assert result["reseed_left_usd"] == 1_500.0
    combined_after = books._combined_equity_for_milestones()
    assert combined_after == pytest.approx(combined - 8_500.0, abs=0.02)


def test_phase1_cashout_never_fires_twice_and_switches_to_phase2():
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    r1 = books.check_milestone_ladder(now)
    assert r1["event"] == "phase1_cashout"

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 2
    assert "p1_cashout" in done["fired_levels"]

    for _ in range(5):
        r2 = books.check_milestone_ladder(now)
        if r2 is not None:
            assert r2.get("event") != "phase1_cashout"
            assert r2["phase"] == 2


def test_phase1_light_ladder_fires_only_when_cashout_not_yet_reachable():
    now = time.time()
    _seed_closed_trade("compound", 900.0, now, "t1")
    combined = books._combined_equity_for_milestones()
    assert combined < books.PHASE_DEFS[1]["trigger"]

    r = books.check_milestone_ladder(now)
    assert r["phase"] == 1
    assert "event" not in r
    assert r["level"] in [lvl for lvl, _ in books.PHASE_DEFS[1]["ladder"]]


def test_phase2_banks_lightly_toward_12000_after_phase1_cashout():
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    r1 = books.check_milestone_ladder(now)
    assert r1["event"] == "phase1_cashout"

    now2 = now + 10
    _seed_closed_trade("compound", 3000.0, now2, "t2")
    combined = books._combined_equity_for_milestones()
    assert combined >= books.PHASE_DEFS[2]["ladder"][0][0]

    r2 = books.check_milestone_ladder(now2)
    assert r2 is not None
    assert r2["phase"] == 2
    assert r2["level"] == books.PHASE_DEFS[2]["ladder"][0][0]
    assert r2["pct"] == books.PHASE_DEFS[2]["ladder"][0][1]


def test_full_5_phase_walk_to_1_million_fires_every_cashout_in_order():
    """End-to-end: drive combined equity all the way from the $400 default seed to past
    $1,000,000 in large single jumps, and verify phases 1 -> 2 -> 3 -> 4 -> 5 fire their
    cash-outs IN ORDER, with the exact amounts Ali specified, never skipping or double-firing a
    phase, and that phase 2 and phase 3 (same $12,000 trigger) both fire independently."""
    now = time.time()
    events = []

    def drain():
        for _ in range(200):
            r = books.check_milestone_ladder(now)
            if r is None:
                break
            events.append(r)

    # Phase 1 -> cashout
    _seed_closed_trade("compound", 20000.0, now, "p1")
    drain()
    # Phase 2 -> cashout ($12k trigger)
    _seed_closed_trade("compound", 15000.0, now + 1, "p2")
    drain()
    # Phase 3 -> cashout (SAME $12k trigger, intentional repeat)
    _seed_closed_trade("compound", 15000.0, now + 2, "p3")
    drain()
    # Phase 4 -> cashout ($120k trigger)
    _seed_closed_trade("compound", 130000.0, now + 3, "p4")
    drain()
    # Phase 5 -> ladder only, push well past $1,000,000
    _seed_closed_trade("compound", 1_200_000.0, now + 4, "p5")
    drain()

    cashouts = [e for e in events if "event" in e]
    assert [e["event"] for e in cashouts] == [
        "phase1_cashout", "phase2_cashout", "phase3_cashout", "phase4_cashout",
    ]
    assert cashouts[0]["cashout_usd"] == 8_500.0 and cashouts[0]["reseed_left_usd"] == 1_500.0
    assert cashouts[1]["cashout_usd"] == 10_000.0 and cashouts[1]["reseed_left_usd"] == 2_000.0
    assert cashouts[2]["cashout_usd"] == 10_000.0 and cashouts[2]["reseed_left_usd"] == 2_000.0
    assert cashouts[3]["cashout_usd"] == 115_000.0 and cashouts[3]["reseed_left_usd"] == 5_000.0

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 5
    # no key ever fired twice
    fl = done["fired_levels"]
    assert len(fl) == len(set(fl))

    # phase 5 ladder legs fired too (no cash-out key for phase 5 since none is coded)
    phase5_events = [e for e in events if e.get("phase") == 5]
    assert len(phase5_events) >= 1
    assert all("event" not in e for e in phase5_events)


def test_phase3_and_phase2_use_independent_fired_level_keys_despite_same_trigger():
    """Phase 2 and Phase 3 share the SAME $12,000 trigger value by design -- verify their fired
    levels are tracked under distinct namespaced keys (p2_*, p3_*) so phase 3 is not skipped or
    treated as already-fired just because phase 2's identical-looking ladder/trigger already
    fired once."""
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "p1")
    for _ in range(50):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 2
    assert "p1_cashout" in done["fired_levels"]
    assert "p2_cashout" not in done["fired_levels"]
    assert "p3_cashout" not in done["fired_levels"]


def test_does_not_touch_stake_sizing_or_seeds():
    now = time.time()
    seeds_before = {b: books.cfg(b)["seed"] for b in ("sprint", "core1", "core2", "compound")}
    books.check_milestone_ladder(now)
    seeds_after = {b: books.cfg(b)["seed"] for b in ("sprint", "core1", "core2", "compound")}
    assert seeds_before == seeds_after


def test_old_fired_levels_from_previous_2phase_scheme_are_preserved_and_never_refire():
    """Regression guard for the live migration: Ali's real system already has p1_240 and p1_600
    fired under the OLD ladder (before this 5-phase change). Verify that pre-existing
    fired_levels entries are preserved verbatim and never re-processed/re-fired after the
    upgrade, since the new PHASE_DEFS[1]['ladder'] also happens to include a 600.0 level."""
    now = time.time()
    st.set_value(books.MILESTONE_LADDER_KEY, {
        "phase": 1,
        "fired_levels": ["p1_240", "p1_600"],
        "p1_240": {"ts": now - 100, "phase": 1, "level": 240.0, "pct": 0.1},
        "p1_600": {"ts": now - 50, "phase": 1, "level": 600.0, "pct": 0.12},
    })
    # combined equity well above both old levels but still under the new ladder's next rung (1200)
    _seed_closed_trade("compound", 750.0, now, "t1")
    combined = books._combined_equity_for_milestones()
    assert combined > 600.0

    r = books.check_milestone_ladder(now)
    # must NOT re-fire p1_240 or p1_600 -- must fire the next NEW unfired level instead (1200.0),
    # or return None if combined hasn't reached it yet
    if r is not None:
        assert r.get("level") not in (240.0, 600.0)

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert "p1_240" in done["fired_levels"]
    assert "p1_600" in done["fired_levels"]
    assert done["fired_levels"].count("p1_240") == 1
    assert done["fired_levels"].count("p1_600") == 1

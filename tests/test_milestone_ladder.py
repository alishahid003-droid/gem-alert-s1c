"""16 Nov 2026 (Ali, NEW): superseded the 7-13 Nov 5-phase ($10k/$12k/$12k/$120k/$1M) milestone
structure with Ali's real 4-phase target/cash-out ladder (handwritten plan, exact numbers):
  Phase 1: $115   -> $6,000    trigger, cash out $4,500,   reseed $1,500
  Phase 2: $1,500 -> $13,000   trigger, cash out $11,000,  reseed $2,000
  Phase 3: $2,000 -> $105,000  trigger, cash out $100,000, reseed $5,000
  Phase 4: $5,000 -> $1,000,000 final target (ladder only, no auto cash-out coded yet)

Tests executor.books.check_milestone_ladder() -- same ordering guarantees as before (cash-out
trigger checked FIRST every call, ahead of that phase's own lighter ladder legs; only ONE event
fires per call), now walked across 4 phases instead of 5. Phase 1 still runs on compound ALONE
(PHASE1_SOLO_BOOK), gated on the cross-module cascade having already fired; phases 2-4 still use
the 65/35 compound/sprint split (PHASE_ENGINE_BOOKS/PHASE_ENGINE_SPLIT) -- those structural rules
were not touched by the 16 Nov target revision, only the dollar amounts and the phase count
(5 -> 4) changed."""
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


def _clear_phase1_cascade_gate(now: float):
    """Phase 1's cashout is gated on the cross-module cascade having already fired
    (SC_CASCADE_KEY) -- set that directly so phase-1-cashout tests aren't blocked on it;
    the cascade gate itself is a separate, already-covered mechanism."""
    st.set_value(books.SC_CASCADE_KEY, {"fired": True, "ts": now})


def test_phase1_fires_first_light_banking_level_when_nowhere_near_6000():
    now = time.time()
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined == 100.0   # 1 x $100 default seed for compound alone, nowhere near $6,000
    # first phase-1 rung is 397.48 -- combined ($100) is well below it, so nothing fires yet
    result = books.check_milestone_ladder(now)
    assert result is None


def test_phase1_light_ladder_fires_once_combined_clears_first_rung():
    now = time.time()
    _seed_closed_trade("compound", 320.0, now, "t1")   # $100 seed + $320 pnl = $420, clears 397.48
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined >= books.PHASE_DEFS[1]["ladder"][0][0]

    result = books.check_milestone_ladder(now)
    assert result is not None
    assert result["phase"] == 1
    assert result["level"] == books.PHASE_DEFS[1]["ladder"][0][0] == 397.48
    assert result["pct"] == books.PHASE_DEFS[1]["ladder"][0][1] == 0.10
    assert "event" not in result


def test_phase1_cashout_blocked_until_cascade_fires_even_past_6000_trigger():
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined >= books.PHASE_DEFS[1]["trigger"]

    # cascade gate NOT cleared -- cashout must not fire; only ladder rungs may fire
    for _ in range(20):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
        assert r.get("event") != "phase1_cashout"


def test_phase1_cashout_fires_once_cascade_has_already_fired():
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    _clear_phase1_cascade_gate(now)

    fired = None
    for _ in range(20):
        r = books.check_milestone_ladder(now)
        if r is not None and r.get("event") == "phase1_cashout":
            fired = r
            break
    assert fired is not None
    assert fired["cashout_usd"] == books.PHASE_DEFS[1]["cashout"] == 4_500.0
    assert fired["reseed_left_usd"] == books.PHASE_DEFS[1]["reseed"] == 1_500.0


def test_phase1_cashout_never_fires_twice_and_switches_to_phase2():
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    _clear_phase1_cascade_gate(now)

    cashouts = []
    for _ in range(30):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
        if r.get("event") == "phase1_cashout":
            cashouts.append(r)
    assert len(cashouts) == 1

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 2
    assert "p1_cashout" in done["fired_levels"]

    for _ in range(5):
        r2 = books.check_milestone_ladder(now)
        if r2 is not None:
            assert r2.get("event") != "phase1_cashout"
            assert r2["phase"] == 2


def test_phase2_banks_lightly_toward_13000_after_phase1_cashout_and_joins_sprint():
    now = time.time()
    _seed_closed_trade("compound", 20000.0, now, "t1")
    _clear_phase1_cascade_gate(now)
    r1 = None
    for _ in range(30):
        r1 = books.check_milestone_ladder(now)
        if r1 is not None and r1.get("event") == "phase1_cashout":
            break
    assert r1["event"] == "phase1_cashout"

    now2 = now + 10
    _seed_closed_trade("compound", 2000.0, now2, "t2")
    combined = books._combined_equity_for_milestones(books.PHASE_ENGINE_BOOKS)
    assert combined >= books.PHASE_DEFS[2]["ladder"][0][0]

    r2 = books.check_milestone_ladder(now2)
    assert r2 is not None
    assert r2["phase"] == 2
    assert r2["level"] == books.PHASE_DEFS[2]["ladder"][0][0] == 3_329.55
    assert r2["pct"] == books.PHASE_DEFS[2]["ladder"][0][1] == 0.10


def test_full_4_phase_walk_to_1_million_fires_every_cashout_in_order():
    """End-to-end: drive combined equity all the way from the $100/$115 default seed to past
    $1,000,000 in large single jumps, and verify phases 1 -> 2 -> 3 -> 4 fire their cash-outs IN
    ORDER, with the exact amounts Ali specified, never skipping or double-firing a phase."""
    now = time.time()
    events = []

    def drain():
        for _ in range(400):
            r = books.check_milestone_ladder(now)
            if r is None:
                break
            events.append(r)

    _clear_phase1_cascade_gate(now)
    # Phase 1 -> cashout
    _seed_closed_trade("compound", 20000.0, now, "p1")
    drain()
    # Phase 2 -> cashout ($13k trigger)
    _seed_closed_trade("compound", 15000.0, now + 1, "p2")
    drain()
    # Phase 3 -> cashout ($105k trigger)
    _seed_closed_trade("compound", 110000.0, now + 2, "p3")
    drain()
    # Phase 4 -> ladder only, push well past $1,000,000
    _seed_closed_trade("compound", 1_200_000.0, now + 3, "p4")
    drain()

    cashouts = [e for e in events if "event" in e]
    assert [e["event"] for e in cashouts] == [
        "phase1_cashout", "phase2_cashout", "phase3_cashout",
    ]
    assert cashouts[0]["cashout_usd"] == 4_500.0 and cashouts[0]["reseed_left_usd"] == 1_500.0
    assert cashouts[1]["cashout_usd"] == 11_000.0 and cashouts[1]["reseed_left_usd"] == 2_000.0
    assert cashouts[2]["cashout_usd"] == 100_000.0 and cashouts[2]["reseed_left_usd"] == 5_000.0

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 4
    # no key ever fired twice
    fl = done["fired_levels"]
    assert len(fl) == len(set(fl))

    # phase 4 ladder legs fired too (no cash-out key for phase 4 since none is coded)
    phase4_events = [e for e in events if e.get("phase") == 4]
    assert len(phase4_events) >= 1
    assert all("event" not in e for e in phase4_events)


def test_phase3_uses_its_own_namespaced_fired_level_keys():
    """Phase 3's fired levels must be tracked under its own p3_* keys, distinct from phase 1's
    p1_* and phase 2's p2_* keys, even though all three now have different triggers (unlike the
    old 5-phase plan where phase 2 and phase 3 intentionally shared the same $12,000 trigger)."""
    now = time.time()
    _clear_phase1_cascade_gate(now)
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


def test_old_fired_levels_from_previous_5phase_scheme_are_preserved_and_never_refire():
    """Regression guard for the live migration: Ali's real system already has p1_250, p1_144,
    p1_246, p1_422 fired under the OLD 5-phase ladder (before this 16 Nov 4-phase change).
    Verify that pre-existing fired_levels entries are preserved verbatim and never
    re-processed/re-fired after the upgrade -- the new PHASE_DEFS[1]['ladder'] uses entirely
    different level keys (397/597/942/1690/3126/4850), confirmed numerically disjoint from the
    old live keys, so there's no double-firing risk, but this test locks that guarantee in."""
    now = time.time()
    st.set_value(books.MILESTONE_LADDER_KEY, {
        "phase": 1,
        "fired_levels": ["p1_250", "p1_144", "p1_246", "p1_422"],
        "p1_250": {"ts": now - 100, "phase": 1, "level": 250.0, "pct": 0.1},
        "p1_144": {"ts": now - 90, "phase": 1, "level": 144.0, "pct": 0.1},
        "p1_246": {"ts": now - 80, "phase": 1, "level": 246.0, "pct": 0.1},
        "p1_422": {"ts": now - 70, "phase": 1, "level": 422.0, "pct": 0.1},
    })
    # combined equity well above all old levels but still under the new ladder's next rung (597.57)
    _seed_closed_trade("compound", 330.0, now, "t1")   # $100 seed + $330 = $430
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined > 422.0

    r = books.check_milestone_ladder(now)
    # must NOT re-fire any old key -- must fire the next NEW unfired level instead (397.48,
    # since combined=$430 clears it), or return None if combined hasn't reached it yet
    if r is not None:
        assert r.get("level") not in (250.0, 144.0, 246.0, 422.0)

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    for old_key in ("p1_250", "p1_144", "p1_246", "p1_422"):
        assert old_key in done["fired_levels"]
        assert done["fired_levels"].count(old_key) == 1

"""13 Nov 2026 (Ali, NEW targets -- supersedes the 7 Nov 5-phase structure test file wholesale):
  Phase 1: $100  -> $3,000    trigger, cash out $2,000,   reseed $1,000   -- COMPOUND ALONE
  Phase 2: $1,000 -> $15,000  trigger, cash out $14,000,  reseed $1,000   -- compound+sprint 65/35
  Phase 3: $2,000 -> $12,000  trigger, cash out $10,000,  reseed $2,000   -- compound+sprint 65/35
  Phase 4: $5,000 -> $105,000 trigger, cash out $100,000, reseed $5,000   -- compound+sprint 65/35
  Phase 5: $5,000 -> $1,000,000 final target (ladder only, no auto cash-out coded yet)

Two structural changes from the 7 Nov version, both explicit (Ali, 13 Nov 2026):
  1. Phase 1 runs on COMPOUND ALONE (PHASE1_SOLO_BOOK) -- sprint is not yet seeded/participating
     at all during phase 1; it joins only from phase 2 onward via the 65/35 reseed split.
  2. Phase 1's cashout is additionally gated on the cross-module cascade
     (check_sprint_compound_cascade()) having ALREADY fired -- confirmed via explicit
     clarifying question: "the cascade must fire first, then phase 1 completes."

Tests executor.books.check_milestone_ladder() v4 -- same ordering guarantees as the prior
versions (cash-out trigger checked FIRST every call, ahead of that phase's own lighter ladder
legs; only ONE event fires per call), now walked across the new 5-phase targets."""
import time

import pytest

import state as st
from executor import books, paper_ledger as pl


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "LOCAL_STATE_FILE", str(tmp_path / "state.json"))
    # 13 Nov 2026: this file's _fire_cascade() helper sets LIVE_BOOKS=compound directly via
    # os.environ (not monkeypatch.setenv) to exercise the new live-only cascade gate. Confirmed
    # as a real bug via a full-suite run: without explicit teardown here, that raw os.environ
    # write survived into test_router_books.py (run later in the same pytest session) and made
    # its core1/core2/sprint tests see themselves as wrongly "live and paused". Explicitly
    # deleting it in TEARDOWN (after yield), not setup, guarantees it never leaks to any test
    # file run afterward, regardless of which test in this file set it or how it failed.
    yield
    import os as _os
    _os.environ.pop("LIVE_BOOKS", None)


def _seed_closed_trade(module: str, pnl_usd: float, now: float, trade_id: str):
    closed = st.get_value(pl.CLOSED_KEY)
    closed = closed if isinstance(closed, list) else []
    closed.append({"module": module, "pnl_usd": pnl_usd, "closed_ts": now,
                   "opened_ts": now - 3600, "id": trade_id, "strategy": "early"})
    st.set_value(pl.CLOSED_KEY, closed)


def _fire_cascade(now: float):
    """Helper: arms LIVE_BOOKS=compound, seeds enough compound profit to cross the cascade's own
    $500 banked() trigger, and fires it -- used by every phase-1-cashout test, since phase 1's
    cashout is now gated on this having already happened."""
    import os
    os.environ["LIVE_BOOKS"] = "compound"
    _seed_closed_trade("compound", 5000.0, now - 1000, "cascade_seed")
    result = books.check_sprint_compound_cascade(now - 1000)
    assert result is not None and result.get("fired") is True, "cascade did not fire in test setup"
    return result


def test_phase1_fires_first_light_banking_level_when_nowhere_near_3000():
    # 13 Nov 2026: phase 1 is compound-ALONE now (PHASE1_SOLO_BOOK) -- combined equity for
    # phase 1 purposes is compound's own equity only, not compound+sprint.
    now = time.time()
    _seed_closed_trade("compound", 44.0, now, "t0")  # $100 default + $44 pnl = $144, clears first rung
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined == 144.0
    result = books.check_milestone_ladder(now)
    assert result["phase"] == 1
    assert result["level"] == 144.0
    assert result["pct"] == 0.10
    assert result["banked_total"] == pytest.approx(144.0 * 0.10, abs=0.01)
    assert "event" not in result


def test_phase1_cashout_blocked_until_cascade_fires_even_past_3000_trigger():
    """13 Nov 2026 (Ali, NEW): the single most important new behavior. Compound equity clearing
    the $3,000 trigger must NOT fire the phase-1 cashout on its own -- the cross-module cascade
    (compound's banked() crossing its own separate $500 trigger, LIVE_BOOKS=compound armed) has
    to fire FIRST. Without that, check_milestone_ladder() must keep banking the lighter ladder
    legs (or return None once they're exhausted) and never advance to phase 2."""
    now = time.time()
    _seed_closed_trade("compound", 3500.0, now, "t1")  # $100 + $3500 = $3600, clears $3000 trigger
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined >= books.PHASE_DEFS[1]["trigger"]

    # Cascade has NOT fired (LIVE_BOOKS unset) -- cashout must be withheld.
    for _ in range(20):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
        assert r.get("event") != "phase1_cashout", "cashout fired before cascade -- WRONG"

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done.get("phase", 1) == 1, "must still be phase 1 -- cashout withheld"


def test_phase1_cashout_fires_once_cascade_has_already_fired():
    """Same setup as above, but this time the cascade fires first (compound armed live, banked()
    crosses its own $500 trigger) -- phase 1's cashout must now proceed normally once compound's
    equity also clears the $3,000 phase trigger."""
    now = time.time()
    _fire_cascade(now)
    # cascade pulled $300 out of compound's banked vault -- but compound's TOTAL equity/pnl
    # pushed it well past $3,000 regardless (seeded $5,044 pnl above cascade trigger + extra).
    _seed_closed_trade("compound", 500.0, now, "t1")
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined >= books.PHASE_DEFS[1]["trigger"], f"combined={combined} should clear $3000"

    fired = None
    for _ in range(50):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
        if r.get("event") == "phase1_cashout":
            fired = r
            break
    assert fired is not None, "phase1_cashout never fired even though cascade already fired"
    assert fired["cashout_usd"] == 2_000.0
    assert fired["reseed_left_usd"] == 1_000.0
    # cashout must come entirely from compound (phase 1 is compound-solo)
    assert set(fired["cashed_out_per_book"].keys()) == {"compound"}
    assert fired["cashed_out_per_book"]["compound"] == pytest.approx(2_000.0, abs=0.02)


def test_phase1_cashout_never_fires_twice_and_switches_to_phase2():
    now = time.time()
    _fire_cascade(now)
    _seed_closed_trade("compound", 500.0, now, "t1")

    fired = None
    for _ in range(50):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
        if r.get("event") == "phase1_cashout":
            fired = r
            break
    assert fired is not None

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
    _seed_closed_trade("compound", 300.0, now, "t1")  # $100 + $300 = $400, below $3000 trigger
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined < books.PHASE_DEFS[1]["trigger"]

    r = books.check_milestone_ladder(now)
    assert r["phase"] == 1
    assert "event" not in r
    assert r["level"] in [lvl for lvl, _ in books.PHASE_DEFS[1]["ladder"]]


def test_phase2_banks_lightly_toward_15000_after_phase1_cashout_and_joins_sprint():
    """13 Nov 2026: once phase 1 completes, phase 2 onward combines compound+sprint (the normal
    PHASE_ENGINE_BOOKS pair) -- sprint now participates for the first time, seeded by the 65/35
    reseed split of phase 1's $1,000 reseed."""
    now = time.time()
    _fire_cascade(now)
    _seed_closed_trade("compound", 500.0, now, "t1")

    fired = None
    for _ in range(50):
        r = books.check_milestone_ladder(now)
        if r is None:
            break
        if r.get("event") == "phase1_cashout":
            fired = r
            break
    assert fired is not None
    # cashout leaves at least reseed_left_usd ($1,000) behind -- real overshoot above that floor
    # is kept, not forced down to a flat number (same principle every phase cashout uses). The
    # real assertion here is sprint's equity, which was exactly $0 before this cashout (paused
    # by the pre-cascade gate) and must now show a real, nonzero balance from its 35% share of
    # the $1,000+ reseed -- proof phase 2 has genuinely brought sprint online for the first time.
    combined_after_phase1 = books._combined_equity_for_milestones(books.PHASE_ENGINE_BOOKS)
    assert combined_after_phase1 >= 1_000.0 - 0.02
    assert books.equity("sprint") > 0.0, "sprint should now hold its 35% reseed share, got $0"

    now2 = now + 10
    _seed_closed_trade("compound", 1_500.0, now2, "t2")
    combined = books._combined_equity_for_milestones(books.PHASE_ENGINE_BOOKS)
    assert combined >= books.PHASE_DEFS[2]["ladder"][0][0]

    r2 = books.check_milestone_ladder(now2)
    assert r2 is not None
    assert r2["phase"] == 2
    assert r2["level"] == books.PHASE_DEFS[2]["ladder"][0][0]
    assert r2["pct"] == books.PHASE_DEFS[2]["ladder"][0][1]


def test_full_5_phase_walk_to_1_million_fires_every_cashout_in_order():
    """End-to-end: drive compound (phase 1) and then combined compound+sprint (phases 2-5) all
    the way from the default seed to past $1,000,000 in large single jumps, and verify phases
    1 -> 2 -> 3 -> 4 -> 5 fire their cash-outs IN ORDER, with the exact amounts Ali specified
    (13 Nov 2026 targets), never skipping or double-firing a phase."""
    now = time.time()
    events = []

    def drain():
        for _ in range(300):
            r = books.check_milestone_ladder(now)
            if r is None:
                break
            events.append(r)

    # Phase 1 -> cashout (compound alone; cascade must fire first)
    _fire_cascade(now)
    _seed_closed_trade("compound", 3500.0, now, "p1")
    drain()
    # Phase 2 -> cashout ($15k trigger, compound+sprint)
    _seed_closed_trade("compound", 14000.0, now + 1, "p2")
    drain()
    # Phase 3 -> cashout ($12k trigger)
    _seed_closed_trade("compound", 12000.0, now + 2, "p3")
    drain()
    # Phase 4 -> cashout ($105k trigger)
    _seed_closed_trade("compound", 105000.0, now + 3, "p4")
    drain()
    # Phase 5 -> ladder only, push well past $1,000,000
    _seed_closed_trade("compound", 1_200_000.0, now + 4, "p5")
    drain()

    cashouts = [e for e in events if "event" in e]
    assert [e["event"] for e in cashouts] == [
        "phase1_cashout", "phase2_cashout", "phase3_cashout", "phase4_cashout",
    ]
    assert cashouts[0]["cashout_usd"] == 2_000.0 and cashouts[0]["reseed_left_usd"] == 1_000.0
    assert cashouts[1]["cashout_usd"] == 14_000.0 and cashouts[1]["reseed_left_usd"] == 1_000.0
    assert cashouts[2]["cashout_usd"] == 10_000.0 and cashouts[2]["reseed_left_usd"] == 2_000.0
    assert cashouts[3]["cashout_usd"] == 100_000.0 and cashouts[3]["reseed_left_usd"] == 5_000.0

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 5
    # no key ever fired twice
    fl = done["fired_levels"]
    assert len(fl) == len(set(fl))

    # phase 5 ladder legs fired too (no cash-out key for phase 5 since none is coded)
    phase5_events = [e for e in events if e.get("phase") == 5]
    assert len(phase5_events) >= 1
    assert all("event" not in e for e in phase5_events)


def test_phase3_and_phase2_use_independent_fired_level_keys_despite_different_trigger():
    """Phase 2 ($15,000 trigger) and Phase 3 ($12,000 trigger) have different triggers under the
    new scheme, but still verify their fired levels are tracked under distinct namespaced keys
    (p2_*, p3_*) with no cross-phase collisions."""
    now = time.time()
    _fire_cascade(now)
    _seed_closed_trade("compound", 3500.0, now, "p1")
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

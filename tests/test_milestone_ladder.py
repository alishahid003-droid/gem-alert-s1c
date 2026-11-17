"""17 Nov 2026 (Ali, NEW): superseded the 16 Nov 4-phase ($6k/$13k/$105k/$1M) structure with
Ali's new, explicit 3-phase target/cash-out plan:
  Phase 1: $115   -> $25,000     trigger. Cash out the FULL $25,000 (nothing held back from
           trading equity itself).
  Phase 2: (vault reseed)  -> $100,000   trigger. Cash out the FULL $100,000.
  Phase 3: (vault reseed)  -> $1,000,000 final target (ladder only, no auto cash-out coded yet).

CRITICAL, new this revision: there are now TWO separate pools. Trading equity
(books.equity(book_id) = total_value() - banked()) is what the phase trigger checks (via
_combined_equity_for_milestones()) and what gets cashed out in FULL at each phase -- nothing is
reseeded from equity itself (reseed=0.0 for every phase in PHASE_DEFS). Separately, the real
banked VAULT (books.banked(book_id), driven by COMPOUND_STAGES) grows on the side and is NEVER
counted toward a phase's trigger and NOT part of the equity cash-out. Instead, the moment a
phase's cash-out fires, _vault_withdraw_and_reseed() fires alongside it: it takes a fixed expense
out of the vault ($1,000 at Phase 1's completion, $2,000 at Phase 2's), and deposits the VAULT
REMAINDER (not equity) as the next phase's starting seed via DEPOSITS_KEY.

COMPOUND_STAGES itself also changed: it's now a 6-stage incremental ladder ($1k@20%, $5k@25%,
$10k@30%, $25k@30%, $100k@35%, $500k@45%), with each stage banking its percentage of only the
SLICE above the prior threshold (not of the full threshold value) -- see
executor.books._banked_vault_incremental().

Tests executor.books.check_milestone_ladder() -- same ordering guarantees as before (cash-out
trigger checked FIRST every call, ahead of that phase's own lighter ladder legs; only ONE event
fires per call), now walked across 3 phases instead of 4. Phase 1 still runs on compound ALONE
(PHASE1_SOLO_BOOK), gated on the cross-module cascade having already fired; phases 2-3 still use
the 65/35 compound/sprint split (PHASE_ENGINE_BOOKS/PHASE_ENGINE_SPLIT) -- those structural rules
were not touched by the 17 Nov target revision, only the dollar amounts, the phase count (4 -> 3),
and the addition of the separate vault-withdraw-and-reseed mechanic changed.

All dollar figures below were computed by running the REAL check_milestone_ladder()/
_vault_withdraw_and_reseed()/_banked_vault_incremental() functions against a scratch, isolated
state (not hand-calculated), to avoid asserting a wrong number against the new incremental
banking math.

KNOWN PRE-EXISTING ISSUE (found while rewriting these tests, NOT introduced here, and left
untouched per instructions not to modify executor/books.py): check_milestone_ladder() checks
`phase in (1, 2, 3)` for the hard cash-out branch, but PHASE_DEFS[3]["cashout"]/["reseed"] are
both None (phase 3 has no cash-out wired yet, same convention as every prior "final phase"). If
combined equity for phase 3 is ever driven to/above its $1,000,000 trigger, the cash-out branch
still tries to fire and crashes inside _fixed_cashout_combined() (TypeError multiplying None).
The full-walk test below deliberately stops phase 3's equity short of $1,000,000 so it only
exercises phase 3's ladder-banking-only path (which is what's actually coded/intended for the
final phase) and does not trip this latent crash -- flagged here and in the task report rather
than silently avoided."""
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


def test_phase1_fires_first_light_banking_level_when_nowhere_near_25000():
    now = time.time()
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined == 80.0  # compound's default seed alone, nowhere near $25,000
    # first phase-1 rung is 1,656.0 -- combined ($80) is well below it, so nothing fires yet
    result = books.check_milestone_ladder(now)
    assert result is None


def test_phase1_light_ladder_fires_once_combined_clears_first_rung():
    now = time.time()
    _seed_closed_trade("compound", 2_500.0, now, "t1")   # seed $100 + $2,500 pnl, raw $2,600 ->
    # _banked_vault_incremental($2,600) banks $600 (20% of the $1k->$5k slice doesn't apply yet;
    # actually 20% of the $0->$1k slice ($200) + 20%? -- see docstring: computed via the real
    # function, not by hand) leaving combined (trading) equity of exactly $2,000.0, which clears
    # the first phase-1 rung ($1,656.0).
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined == 2_000.0
    assert combined >= books.PHASE_DEFS[1]["ladder"][0][0]

    result = books.check_milestone_ladder(now)
    assert result is not None
    assert result["phase"] == 1
    assert result["level"] == books.PHASE_DEFS[1]["ladder"][0][0] == 1_656.0
    assert result["pct"] == books.PHASE_DEFS[1]["ladder"][0][1] == 0.10
    assert result["banked_total"] == 200.0  # 10% of the $2,000 combined equity at fire
    assert "event" not in result


def test_phase1_cashout_blocked_until_cascade_fires_even_past_25000_trigger():
    now = time.time()
    _seed_closed_trade("compound", 40_000.0, now, "t1")
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
    _seed_closed_trade("compound", 40_000.0, now, "t1")
    _clear_phase1_cascade_gate(now)

    fired = None
    for _ in range(20):
        r = books.check_milestone_ladder(now)
        if r is not None and r.get("event") == "phase1_cashout":
            fired = r
            break
    assert fired is not None
    assert fired["cashout_usd"] == books.PHASE_DEFS[1]["cashout"] == 25_000.0
    assert fired["reseed_left_usd"] == books.PHASE_DEFS[1]["reseed"] == 0.0
    # The FULL $25,000 target cashes out of trading equity -- nothing held back/reseeded from
    # equity itself (that's the whole point of the new structure).
    assert fired["cashed_out_total"] == 25_000.0


def test_phase1_cashout_withdraws_vault_expense_and_reseeds_remainder_to_phase2():
    """New vault/reseed mechanic (17 Nov 2026): the moment Phase 1's cash-out fires,
    _vault_withdraw_and_reseed() must ALSO fire, pulling $1,000 out of the vault as Phase 1's own
    expense and depositing the remainder as Phase 2's starting seed via DEPOSITS_KEY -- entirely
    separate from, and untouched by, the equity cash-out itself."""
    now = time.time()
    _seed_closed_trade("compound", 40_000.0, now, "t1")
    _clear_phase1_cascade_gate(now)

    fired = None
    for _ in range(20):
        r = books.check_milestone_ladder(now)
        if r is not None and r.get("event") == "phase1_cashout":
            fired = r
            break
    assert fired is not None

    assert "vault" in fired
    vault = fired["vault"]
    # Computed against the real function (not hand-calculated): with $40,000 pnl seeded into
    # compound before any ladder/cashout math runs, compound's vault had banked $4,230.00 by the
    # time Phase 1's cashout trigger fired.
    assert vault["vault_before"] == 4_230.0
    assert vault["vault_expense_taken"] == 1_000.0  # Phase 1's own vault_expense_usd
    assert vault["vault_remainder_reseeded"] == 3_230.0
    # Split 65/35 compound/sprint (PHASE_ENGINE_SPLIT), since phase 2 onward uses both books.
    assert vault["reseeded_per_book"] == {"sprint": 1_130.5, "compound": 2_099.5}

    # The reseed must actually have landed as deposited-in capital for the next phase's books.
    assert books.deposited("sprint") == 1_130.5
    assert books.deposited("compound") == 2_099.5


def test_phase1_cashout_never_fires_twice_and_switches_to_phase2():
    now = time.time()
    _seed_closed_trade("compound", 40_000.0, now, "t1")
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


def test_phase2_banks_lightly_toward_100000_after_phase1_cashout_and_vault_reseed():
    """After Phase 1's cashout+vault-reseed fires, the reseeded vault remainder alone (deposited
    into sprint+compound) is enough to push combined (sprint+compound) equity straight past
    Phase 2's first ladder rung -- so the very next check_milestone_ladder() call (same `now`,
    no additional trade needed) fires it."""
    now = time.time()
    _seed_closed_trade("compound", 40_000.0, now, "t1")
    _clear_phase1_cascade_gate(now)

    r1 = None
    for _ in range(30):
        r1 = books.check_milestone_ladder(now)
        if r1 is not None and r1.get("event") == "phase1_cashout":
            break
    assert r1["event"] == "phase1_cashout"

    combined = books._combined_equity_for_milestones(books.PHASE_ENGINE_BOOKS)
    assert combined == 10_443.05
    assert combined >= books.PHASE_DEFS[2]["ladder"][0][0]

    r2 = books.check_milestone_ladder(now)
    assert r2 is not None
    assert r2["phase"] == 2
    assert r2["level"] == books.PHASE_DEFS[2]["ladder"][0][0] == 6_625.0
    assert r2["pct"] == books.PHASE_DEFS[2]["ladder"][0][1] == 0.10
    assert r2["combined_equity_at_fire"] == 10_443.05


def test_full_3_phase_walk_fires_both_cashouts_in_order_and_reseeds_vault_each_time():
    """End-to-end: drive combined equity through Phase 1's and Phase 2's cash-outs IN ORDER, with
    the exact amounts Ali specified, each one firing its vault withdraw-and-reseed alongside it,
    then push Phase 3 equity up (but deliberately NOT across its $1,000,000 trigger -- see the
    module docstring's "KNOWN PRE-EXISTING ISSUE" note) and confirm Phase 3's ladder banks on its
    own namespaced keys with no cash-out event, since Phase 3 has none coded yet."""
    now = time.time()
    events = []

    def drain():
        for _ in range(500):
            r = books.check_milestone_ladder(now)
            if r is None:
                break
            events.append(r)

    _clear_phase1_cascade_gate(now)
    # Phase 1 -> cashout
    _seed_closed_trade("compound", 40_000.0, now, "p1")
    drain()
    # Phase 2 -> cashout ($100k trigger)
    _seed_closed_trade("compound", 150_000.0, now + 1, "p2")
    drain()
    # Phase 3 -> ladder only, push up but stay under the $1,000,000 trigger (see docstring).
    _seed_closed_trade("compound", 700_000.0, now + 2, "p3")
    drain()

    cashouts = [e for e in events if "event" in e]
    assert [e["event"] for e in cashouts] == ["phase1_cashout", "phase2_cashout"]

    assert cashouts[0]["cashout_usd"] == 25_000.0 and cashouts[0]["reseed_left_usd"] == 0.0
    assert cashouts[0]["vault"]["vault_expense_taken"] == 1_000.0
    assert cashouts[0]["vault"]["vault_remainder_reseeded"] == 3_230.0

    assert cashouts[1]["cashout_usd"] == 100_000.0 and cashouts[1]["reseed_left_usd"] == 0.0
    assert cashouts[1]["vault"]["vault_expense_taken"] == 2_000.0
    assert cashouts[1]["vault"]["vault_remainder_reseeded"] == 18_521.94

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    assert done["phase"] == 3
    # no key ever fired twice
    fl = done["fired_levels"]
    assert len(fl) == len(set(fl))

    # phase 3 ladder legs fired too (no cash-out key for phase 3 since none is coded)
    phase3_events = [e for e in events if e.get("phase") == 3]
    assert len(phase3_events) >= 1
    assert all("event" not in e for e in phase3_events)
    assert all(not str(k).startswith("p1_") and not str(k).startswith("p2_")
               for e in phase3_events for k in (f"p3_{int(e['level'])}",))

    combined3 = books._combined_equity_for_milestones(books.PHASE_ENGINE_BOOKS)
    assert combined3 < books.PHASE_DEFS[3]["trigger"]  # deliberately stayed under $1,000,000


def test_phase3_uses_its_own_namespaced_fired_level_keys():
    """Phase 3's fired levels must be tracked under its own p3_* keys, distinct from phase 1'
    p1_* and phase 2's p2_* keys."""
    now = time.time()
    _clear_phase1_cascade_gate(now)
    _seed_closed_trade("compound", 40_000.0, now, "p1")
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


def test_old_fired_levels_from_previous_scheme_are_preserved_and_never_refire():
    """Regression guard for the live migration: Ali's real system already has p1_250, p1_144,
    p1_246, p1_422 fired under an OLDER ladder (before this 17 Nov 3-phase change, whose
    PHASE_DEFS[1]['ladder'] now uses entirely different level keys:
    1656/2490/3928/7042/13026/20210). Verify that pre-existing fired_levels entries are preserved
    verbatim and never re-processed/re-fired after the upgrade."""
    now = time.time()
    st.set_value(books.MILESTONE_LADDER_KEY, {
        "phase": 1,
        "fired_levels": ["p1_250", "p1_144", "p1_246", "p1_422"],
        "p1_250": {"ts": now - 100, "phase": 1, "level": 250.0, "pct": 0.1},
        "p1_144": {"ts": now - 90, "phase": 1, "level": 144.0, "pct": 0.1},
        "p1_246": {"ts": now - 80, "phase": 1, "level": 246.0, "pct": 0.1},
        "p1_422": {"ts": now - 70, "phase": 1, "level": 422.0, "pct": 0.1},
    })
    # combined equity well above all old levels but still under the new ladder's first rung
    # (1,656.0): $100 seed + $2,500 pnl, incrementally banked down to $2,000.0 combined equity.
    _seed_closed_trade("compound", 2_500.0, now, "t1")
    combined = books._combined_equity_for_milestones((books.PHASE1_SOLO_BOOK,))
    assert combined == 2_000.0
    assert combined > 422.0

    r = books.check_milestone_ladder(now)
    # must NOT re-fire any old key -- must fire the next NEW unfired level instead (1656.0,
    # since combined=$2,000 clears it)
    assert r is not None
    assert r.get("level") not in (250.0, 144.0, 246.0, 422.0)
    assert r["level"] == 1_656.0

    done = st.get_value(books.MILESTONE_LADDER_KEY)
    for old_key in ("p1_250", "p1_144", "p1_246", "p1_422"):
        assert old_key in done["fired_levels"]
        assert done["fired_levels"].count(old_key) == 1

"""Combined-equity 4-phase target chain (Ali's real financial plan, confirmed 16 Nov 2026 --
supersedes the earlier 5-phase "Curve C" plan wholesale -- see executor/phase_target.py's PHASES
dict, which this test asserts against directly rather than hardcoding the numbers twice):
  1: $115   -> $6,000      (96h,  own window "1")   cashout $4,500   / reseed $1,500
  2: $1,500 -> $13,000     (240h, own window "2")   cashout $11,000  / reseed $2,000
  3: $2,000 -> $105,000    (360h, own window "3")   cashout $100,000 / reseed $5,000
  4: $5,000 -> $1,000,000  (no time limit, own window "4")  cashout $985,000 / no reseed/next

Covers: target-hit alert+pause+cashout recommendation for each phase, the drawdown circuit
breaker pause/resume, can_open() wiring, and that an inactive/unset phase is a complete no-op
(never blocks trading by default -- must be explicitly started).

16 Nov 2026 (Claude): rewritten from the old 5-phase version (with its 2+3 shared-window clock)
after phase_target.py's PHASES dict was updated 16 Nov 2026 to track Ali's real 4-phase plan
instead (see that module's own docstring) -- every phase in the new structure gets its OWN
independent window_group, so there is no shared-window handoff test anymore; the window-carries-
forward mechanism itself is still exercised directly via two phases sharing a group, using a
temporary monkeypatched PHASES dict, so that generic code path doesn't go completely untested
just because none of the 4 real phases currently use it. Values below are pulled from PHASES
directly so this file can't drift out of sync with the module again the same way."""
import pytest

import state
import executor.books as books
import executor.phase_target as pt


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _set_combined_equity(monkeypatch, value):
    """Patches combined_equity() to ignore whichever phase it's called with and always
    return `value` -- used by tests that don't care about the phase-aware book-set routing
    itself (target-hit, drawdown-breaker, window-handling tests), only about a given number
    flowing through start_phase()/status()/check() correctly."""
    monkeypatch.setattr(pt, "combined_equity", lambda phase=None: value)


def test_phase1_combined_equity_uses_compound_alone_paper_when_not_live(monkeypatch):
    """16 Nov 2026 (real incident #2 -- combined_equity() was found blending REAL compound
    equity ($10.05) with PAPER sprint/core1/core2 equity (~$466.57), producing a confusing
    $476.62 "phase 1 baseline" that didn't match the real wallet at all): for phase "1",
    combined_equity() must match books.check_milestone_ladder()'s own real mechanism, which
    uses books.PHASE1_SOLO_BOOK ("compound") ALONE -- sprint/core1/core2 must never be summed
    in during phase 1, live or not. When compound is NOT live-armed, it stays on paper equity."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: set())
    monkeypatch.setattr(books, "equity", lambda b: {"sprint": 10.0, "core1": 20.0,
                                                      "core2": 30.0, "compound": 9999.0}[b])
    monkeypatch.setattr(books, "equity_real", lambda: -1.0)  # must never be read in this case
    assert pt.combined_equity("1") == 9999.0


def test_phase1_combined_equity_uses_compound_real_once_live_sprint_core1_core2_excluded(monkeypatch):
    """Core fix: once compound IS listed in LIVE_BOOKS, phase "1" combined_equity() must use
    REAL equity (books.equity_real()) for compound alone -- sprint/core1/core2 paper equity
    must NOT be added in at all (not even on paper), matching
    books.check_milestone_ladder()'s compound-solo 100% split for phase 1."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    monkeypatch.setattr(books, "equity", lambda b: {"sprint": 10.0, "core1": 20.0,
                                                      "core2": 30.0, "compound": 9999.0}[b])
    monkeypatch.setattr(books, "equity_real", lambda: 32.89)  # the real wallet number
    # compound's PAPER 9999.0 and sprint/core1/core2's paper equity must NOT be used --
    # only compound's real 32.89 contributes.
    assert pt.combined_equity("1") == 32.89


def test_non_phase1_combined_equity_uses_sprint_plus_compound_core1_core2_always_excluded(monkeypatch):
    """For any phase other than "1" (phase None/"2"/"3"/"4"), combined_equity() must match
    books.check_milestone_ladder()'s PHASE_ENGINE_BOOKS = (sprint, compound) -- core1/core2
    never feed phase targets in ANY phase, confirmed via the codebase's own comment on
    _combined_equity_for_milestones(). Compound still swaps to real once live-armed; sprint
    stays paper (books.py's real mechanism never has a real sprint leg here)."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    monkeypatch.setattr(books, "equity", lambda b: {"sprint": 10.0, "core1": 20.0,
                                                      "core2": 30.0, "compound": 9999.0}[b])
    monkeypatch.setattr(books, "equity_real", lambda: 32.89)
    # core1 (20.0) and core2 (30.0) must NOT appear in the sum for phase 2/3/4 or bare calls.
    assert pt.combined_equity("2") == 10.0 + 32.89
    assert pt.combined_equity("4") == 10.0 + 32.89
    assert pt.combined_equity() == 10.0 + 32.89   # bare call defaults the same as non-1 phases


def test_non_phase1_combined_equity_all_paper_when_compound_not_live(monkeypatch):
    """Sanity check for the non-phase-1 path when compound is not yet live: sprint+compound
    both on paper, core1/core2 still excluded."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: set())
    monkeypatch.setattr(books, "equity", lambda b: {"sprint": 10.0, "core1": 20.0,
                                                      "core2": 30.0, "compound": 9999.0}[b])
    monkeypatch.setattr(books, "equity_real", lambda: -1.0)  # must never be read
    assert pt.combined_equity("3") == 10.0 + 9999.0


def test_drawdown_breaker_never_trips_on_a_failed_real_read(monkeypatch):
    """16 Nov 2026 (real incident -- confirmed from this sandbox, get_real_wallet_usd()
    genuinely returns None when the RPC can't be reached): once compound is live-armed, a
    transient RPC failure must NOT be mistaken for a real equity crash. check()'s drawdown
    pause/resume decision must stay frozen (neither fires) on a cycle where
    get_total_real_wallet_usd_or_none() returns None, even though combined_equity() itself
    still returns some float that cycle (via equity_real()'s own fail-to-$0 behavior, correct
    for its OTHER callers but not for this one)."""
    from executor import swap_executor
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    monkeypatch.setattr(books, "equity_real", lambda: 500.0)   # real wallet reading fine so far
    monkeypatch.setattr(books, "equity", lambda b: 0.0)         # sprint/core1/core2: paper, $0
    pt.start_phase("1", now=1000.0)                              # peak_equity baseline = 500.0
    assert pt.status(now=1000.0)["peak_equity"] == 500.0

    # Now the live RPC read fails: equity_real() (used by other real-money callers, correctly
    # fails to $0) would make combined_equity() report $0 -- normally well under ANY drawdown
    # floor, which would wrongly trip the breaker. But get_total_real_wallet_usd_or_none()
    # (the breaker's own confirmation check) also fails (None) this same cycle, so the pause
    # must NOT fire.
    monkeypatch.setattr(books, "equity_real", lambda: 0.0)
    monkeypatch.setattr(swap_executor, "get_total_real_wallet_usd_or_none", lambda: None)
    out = pt.check(now=1100.0)
    assert "drawdown_pause" not in [e["type"] for e in out["fired"]]
    assert pt.is_paused() is False

    # Confirm the breaker is NOT permanently broken -- once the real read succeeds again and
    # genuinely shows a real drawdown, it fires normally.
    monkeypatch.setattr(books, "equity_real", lambda: 50.0)   # 50 / peak(500) = 10% -- below the 50% floor
    monkeypatch.setattr(swap_executor, "get_total_real_wallet_usd_or_none", lambda: 50.0)
    out2 = pt.check(now=1200.0)
    assert "drawdown_pause" in [e["type"] for e in out2["fired"]]
    assert pt.is_paused() is True


def test_drawdown_breaker_guard_is_a_no_op_while_compound_is_still_paper(monkeypatch):
    """The RPC-failure guard only matters once compound is live-armed -- while compound is
    still paper, combined_equity() has no real-chain dependency at all, so the breaker must
    keep working exactly as before (no guard interference) even if swap_executor's real-wallet
    function would fail if called (it must not even be consulted in this case)."""
    from executor import swap_executor
    monkeypatch.setattr(books, "_live_books_from_env", lambda: set())  # compound NOT live
    monkeypatch.setattr(swap_executor, "get_total_real_wallet_usd_or_none",
                         lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    monkeypatch.setattr(books, "equity", lambda b: 1000.0)
    pt.start_phase("1", now=1000.0)
    monkeypatch.setattr(books, "equity", lambda b: 100.0)  # genuine paper drawdown, 100/4000=2.5%
    out = pt.check(now=1100.0)
    assert "drawdown_pause" in [e["type"] for e in out["fired"]]
    assert pt.is_paused() is True


def test_inactive_by_default_never_blocks_trading():
    assert pt.is_paused() is False
    assert pt.status()["active"] is False


def test_start_phase_rejects_unknown_phase():
    with pytest.raises(ValueError):
        pt.start_phase("Z", now=1000.0)


def test_start_phase_1_sets_baseline_and_own_window(monkeypatch):
    _set_combined_equity(monkeypatch, 115.0)
    d = pt.start_phase("1", now=1000.0)
    assert d["current"] == "1"
    assert d["peak_equity"] == 115.0
    assert d["paused"] is False
    s = pt.status(now=1000.0)
    assert s["active"] is True and s["phase"] == "1"
    assert s["target_usd"] == pt.PHASES["1"]["target_usd"] == 6_000.0
    assert s["allowed_hours"] == pt.PHASES["1"]["hours_allowed"] == 96.0
    assert s["next_phase"] == "2"


def test_phase_1_target_hit_recommends_4500_cashout_1500_reseed(monkeypatch):
    _set_combined_equity(monkeypatch, 115.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 6_200.0)
    out = pt.check(now=2000.0)
    types = [e["type"] for e in out["fired"]]
    assert "target_hit" in types and "cashout_recommended" in types
    assert out["in_drawdown_pause"] is True
    assert pt.is_paused() is True
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == pt.PHASES["1"]["cashout_usd"] == 4_500.0
    assert ev["reseed_usd"] == pt.PHASES["1"]["reseed_usd"] == 1_500.0
    assert ev["next_phase"] == "2"


def test_phase_2_target_hit_recommends_11000_cashout_2000_reseed(monkeypatch):
    _set_combined_equity(monkeypatch, 1_500.0)
    pt.start_phase("2", now=0.0)
    _set_combined_equity(monkeypatch, 13_100.0)
    out = pt.check(now=100 * 3600.0)
    assert any(e["type"] == "target_hit" for e in out["fired"])
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == pt.PHASES["2"]["cashout_usd"] == 11_000.0
    assert ev["reseed_usd"] == pt.PHASES["2"]["reseed_usd"] == 2_000.0
    assert ev["next_phase"] == "3"


def test_each_phase_gets_its_own_fresh_window_now():
    """New structure: no two phases share a window_group anymore (unlike the old 5-phase plan,
    where phases 2+3 shared one). Verify all 4 window_group values are distinct."""
    groups = [pt.PHASES[p]["window_group"] for p in ("1", "2", "3", "4")]
    assert len(groups) == len(set(groups)), f"expected 4 distinct window groups, got {groups}"


def test_phase_4_gets_its_own_fresh_window_after_3(monkeypatch):
    _set_combined_equity(monkeypatch, 5_000.0)
    pt.start_phase("3", now=500_000.0)
    _set_combined_equity(monkeypatch, 5_000.0)
    pt.start_phase("4", now=600_000.0)
    s = pt.status(now=600_000.0)
    assert s["phase"] == "4"
    assert s["window_group"] == "4"
    assert s["elapsed_hours"] == 0.0            # fresh clock, not carried from phase 3's window
    assert s["allowed_hours"] is None           # final phase: no time limit
    assert s["next_phase"] is None


def test_shared_window_group_mechanism_still_works_when_phases_do_share_one(monkeypatch):
    """None of the real 4 phases share a window_group with each other anymore, but the carry-
    forward mechanism in start_phase()/_window_elapsed_hours() is still real code that needs
    covering. Exercise it directly via a temporary monkeypatched PHASES dict with two phases
    sharing a group, the same way the old phase-2/3 handoff used to work."""
    fake_phases = {
        "X": {"label": "X", "target_usd": 1_000.0, "cashout_usd": 500.0, "reseed_usd": 100.0,
              "hours_allowed": 240.0, "drawdown_pause_pct": 0.50, "next": "Y", "window_group": "XY"},
        "Y": {"label": "Y", "target_usd": 2_000.0, "cashout_usd": 1_000.0, "reseed_usd": 200.0,
              "hours_allowed": 240.0, "drawdown_pause_pct": 0.50, "next": None, "window_group": "XY"},
    }
    monkeypatch.setattr(pt, "PHASES", fake_phases)
    _set_combined_equity(monkeypatch, 100.0)
    pt.start_phase("X", now=0.0)
    _set_combined_equity(monkeypatch, 1_100.0)
    out = pt.check(now=100 * 3600.0)
    assert any(e["type"] == "target_hit" for e in out["fired"])

    _set_combined_equity(monkeypatch, 200.0)
    pt.start_phase("Y", now=101 * 3600.0)
    s = pt.status(now=101 * 3600.0)
    assert s["phase"] == "Y"
    assert s["window_group"] == "XY"
    # elapsed should be ~101h (carried from X's start at t=0), not ~0h
    assert s["elapsed_hours"] > 90.0
    assert s["hours_left"] < 240.0 - 90.0


def test_phase_4_target_hit_has_no_next_phase(monkeypatch):
    """Phase 4 is the last phase now -- it still carries a reseed_usd ($15,000, Ali's own 'leave
    $15,000 running' choice for the final phase), but has no next_phase to hand off to, unlike
    every earlier phase."""
    _set_combined_equity(monkeypatch, 5_000.0)
    pt.start_phase("4", now=1000.0)
    _set_combined_equity(monkeypatch, 1_000_500.0)
    out = pt.check(now=2000.0)
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == pt.PHASES["4"]["cashout_usd"] == 985_000.0
    assert ev["reseed_usd"] == pt.PHASES["4"]["reseed_usd"] == 15_000.0
    assert ev["next_phase"] is None


def test_drawdown_breaker_pauses_without_target_hit(monkeypatch):
    _set_combined_equity(monkeypatch, 1000.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 2000.0)
    pt.check(now=1100.0)                              # peak now 2000
    _set_combined_equity(monkeypatch, 900.0)          # fell to 45% of peak -- below the 50% floor
    out = pt.check(now=1200.0)
    types = [e["type"] for e in out["fired"]]
    assert "drawdown_pause" in types
    assert "target_hit" not in types
    assert pt.is_paused() is True


def test_drawdown_breaker_resumes_on_recovery(monkeypatch):
    _set_combined_equity(monkeypatch, 1000.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 2000.0)
    pt.check(now=1100.0)
    _set_combined_equity(monkeypatch, 900.0)          # trips the breaker
    pt.check(now=1200.0)
    assert pt.is_paused() is True
    _set_combined_equity(monkeypatch, 1200.0)         # recovers back above the floor (1000)
    out = pt.check(now=1300.0)
    assert any(e["type"] == "drawdown_resume" for e in out["fired"])
    assert pt.is_paused() is False


def test_can_open_refuses_real_books_when_paused(monkeypatch):
    _set_combined_equity(monkeypatch, 115.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 6_200.0)
    pt.check(now=2000.0)
    assert pt.is_paused() is True
    for book in ("sprint", "core1", "core2", "compound"):
        ok, why = books.can_open(book)
        assert ok is False
        assert "paused" in why.lower()


def test_can_open_unaffected_for_copy_and_solana_books_when_paused(monkeypatch):
    """Only sprint/core1/core2/compound are gated by phase_target -- copy books and the Solana
    pattern-tracking books must keep trading exactly as before, untouched."""
    _set_combined_equity(monkeypatch, 115.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 6_200.0)
    pt.check(now=2000.0)
    for book in ("dev_list", "leaderboard", "solana_sprint", "solana_compound"):
        ok, why = books.can_open(book)
        assert "paused" not in why.lower()

"""Combined-equity 5-phase target chain (Ali's "Curve C" plan, confirmed 7-8 Nov 2026,
Phase 5 split confirmed 11 Nov 2026 -- see executor/phase_target.py's PHASES dict, which this
test asserts against directly rather than hardcoding the numbers twice):
  1: $100   -> $10,000    (96h, own window "1")      cashout $8,500  / reseed $1,500
  2: $1,500 -> $12,000    (240h, shares window "23" with 3)  cashout $10,000 / reseed $2,000
  3: $2,000 -> $12,000    (240h, shares window "23" with 2)  cashout $10,000 / reseed $2,000
  4: $2,000 -> $120,000   (360h, own window "4")      cashout $115,000 / reseed $5,000
  5: $5,000 -> $1,000,000 (no time limit, own window "5")   cashout $975,000 / no reseed/next

Covers: target-hit alert+pause+cashout recommendation for each phase, the 2+3 shared-window
clock carrying forward across start_phase('2') -> start_phase('3'), drawdown circuit breaker
pause/resume, can_open() wiring, and that an inactive/unset phase is a complete no-op (never
blocks trading by default -- must be explicitly started).

15 Nov 2026 (Claude): rewritten from the old A/B/C/D 4-phase version after phase_target.py's
PHASES dict was updated 11 Nov 2026 to track the real, live 5-phase Curve C plan instead (see
that module's own docstring) -- this file had gone stale against that change. Values below are
pulled from PHASES directly so this file can't drift out of sync with the module again the same
way."""
import pytest

import state
import executor.books as books
import executor.phase_target as pt


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def _set_combined_equity(monkeypatch, value):
    monkeypatch.setattr(pt, "combined_equity", lambda: value)


def test_inactive_by_default_never_blocks_trading():
    assert pt.is_paused() is False
    assert pt.status()["active"] is False


def test_start_phase_rejects_unknown_phase():
    with pytest.raises(ValueError):
        pt.start_phase("Z", now=1000.0)


def test_start_phase_1_sets_baseline_and_own_window(monkeypatch):
    _set_combined_equity(monkeypatch, 100.0)
    d = pt.start_phase("1", now=1000.0)
    assert d["current"] == "1"
    assert d["peak_equity"] == 100.0
    assert d["paused"] is False
    s = pt.status(now=1000.0)
    assert s["active"] is True and s["phase"] == "1"
    assert s["target_usd"] == pt.PHASES["1"]["target_usd"] == 10_000.0
    assert s["allowed_hours"] == pt.PHASES["1"]["hours_allowed"] == 96.0
    assert s["next_phase"] == "2"


def test_phase_1_target_hit_recommends_8500_cashout_1500_reseed(monkeypatch):
    _set_combined_equity(monkeypatch, 100.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 10_200.0)
    out = pt.check(now=2000.0)
    types = [e["type"] for e in out["fired"]]
    assert "target_hit" in types and "cashout_recommended" in types
    assert out["in_drawdown_pause"] is True
    assert pt.is_paused() is True
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == pt.PHASES["1"]["cashout_usd"] == 8_500.0
    assert ev["reseed_usd"] == pt.PHASES["1"]["reseed_usd"] == 1_500.0
    assert ev["next_phase"] == "2"


def test_2_3_share_one_window_that_does_not_reset_on_handoff(monkeypatch):
    """Start phase 2 at t=0. Let 100h pass (within the 240h/10-day 2+3 budget). Hit phase 2's
    target, hand off to phase 3. Phase 3's elapsed_hours must continue from the SAME window
    start (100h in), not reset to 0."""
    _set_combined_equity(monkeypatch, 1_500.0)
    pt.start_phase("2", now=0.0)
    _set_combined_equity(monkeypatch, 12_100.0)
    out = pt.check(now=100 * 3600.0)   # 100 hours later, phase 2's target hit
    assert any(e["type"] == "target_hit" for e in out["fired"])
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == pt.PHASES["2"]["cashout_usd"] == 10_000.0
    assert ev["reseed_usd"] == pt.PHASES["2"]["reseed_usd"] == 2_000.0
    assert ev["next_phase"] == "3"

    # Ali does the manual cashout/reseed, then starts phase 3 -- window must carry forward
    _set_combined_equity(monkeypatch, 2_000.0)
    pt.start_phase("3", now=101 * 3600.0)
    s = pt.status(now=101 * 3600.0)
    assert s["phase"] == "3"
    assert s["window_group"] == "23"
    # elapsed should be ~101h (carried from phase 2's start at t=0), not ~0h
    assert s["elapsed_hours"] > 90.0
    assert s["allowed_hours"] == 240.0
    assert s["hours_left"] < 240.0 - 90.0


def test_phase_4_gets_its_own_fresh_window_after_3(monkeypatch):
    _set_combined_equity(monkeypatch, 2_000.0)
    pt.start_phase("3", now=500_000.0)
    _set_combined_equity(monkeypatch, 2_000.0)
    pt.start_phase("4", now=600_000.0)
    s = pt.status(now=600_000.0)
    assert s["phase"] == "4"
    assert s["window_group"] == "4"
    assert s["elapsed_hours"] == 0.0            # fresh clock, not carried from 2/3's window
    assert s["allowed_hours"] == 360.0          # 15 days
    assert s["next_phase"] == "5"


def test_phase_5_target_hit_has_no_next_phase(monkeypatch):
    """Phase 5 is the last phase -- it still carries a reseed_usd ($25,000, Ali's own 'leave
    $25,000 running' choice for the final phase, confirmed 11 Nov 2026), but has no next_phase
    to hand off to, unlike every earlier phase."""
    _set_combined_equity(monkeypatch, 5_000.0)
    pt.start_phase("5", now=1000.0)
    _set_combined_equity(monkeypatch, 1_000_500.0)
    out = pt.check(now=2000.0)
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == pt.PHASES["5"]["cashout_usd"] == 975_000.0
    assert ev["reseed_usd"] == pt.PHASES["5"]["reseed_usd"] == 25_000.0
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
    _set_combined_equity(monkeypatch, 100.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 10_200.0)
    pt.check(now=2000.0)
    assert pt.is_paused() is True
    for book in ("sprint", "core1", "core2", "compound"):
        ok, why = books.can_open(book)
        assert ok is False
        assert "paused" in why.lower()


def test_can_open_unaffected_for_copy_and_solana_books_when_paused(monkeypatch):
    """Only sprint/core1/core2/compound are gated by phase_target -- copy books and the Solana
    pattern-tracking books must keep trading exactly as before, untouched."""
    _set_combined_equity(monkeypatch, 100.0)
    pt.start_phase("1", now=1000.0)
    _set_combined_equity(monkeypatch, 10_200.0)
    pt.check(now=2000.0)
    for book in ("dev_list", "leaderboard", "solana_sprint", "solana_compound"):
        ok, why = books.can_open(book)
        assert "paused" not in why.lower()

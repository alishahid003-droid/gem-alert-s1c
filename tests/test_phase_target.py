"""Combined-equity 4-phase target chain (Ali, 2 Nov 2026, revised):
  A: $100  -> $10,000  (96h, own window)      cashout $9,000 / reseed $1,000
  B: $1,000-> $40,000  (shares 10-day window with C)  cashout $37,000 / reseed $3,000
  C: $3,000-> $40,000  (shares 10-day window with B)  cashout $37,000 / reseed $3,000
  D: $3,000-> $40,000  (own separate 15-day window)   cashout $37,000 / no reseed/next

Covers: target-hit alert+pause+cashout recommendation for each phase, the B+C shared-window
clock carrying forward across start_phase('B') -> start_phase('C'), drawdown circuit breaker
pause/resume, can_open() wiring, and that an inactive/unset phase is a complete no-op (never
blocks trading by default -- must be explicitly started)."""
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


def test_start_phase_a_sets_baseline_and_own_window(monkeypatch):
    _set_combined_equity(monkeypatch, 100.0)
    d = pt.start_phase("A", now=1000.0)
    assert d["current"] == "A"
    assert d["peak_equity"] == 100.0
    assert d["paused"] is False
    s = pt.status(now=1000.0)
    assert s["active"] is True and s["phase"] == "A"
    assert s["target_usd"] == 10_000.0
    assert s["allowed_hours"] == 96.0
    assert s["next_phase"] == "B"


def test_phase_a_target_hit_recommends_9000_cashout_1000_reseed(monkeypatch):
    _set_combined_equity(monkeypatch, 100.0)
    pt.start_phase("A", now=1000.0)
    _set_combined_equity(monkeypatch, 10_200.0)
    out = pt.check(now=2000.0)
    types = [e["type"] for e in out["fired"]]
    assert "target_hit" in types and "cashout_recommended" in types
    assert out["in_drawdown_pause"] is True
    assert pt.is_paused() is True
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == 9_000.0
    assert ev["reseed_usd"] == 1_000.0
    assert ev["next_phase"] == "B"


def test_bc_share_one_window_that_does_not_reset_on_handoff(monkeypatch):
    """Start B at t=0. Let 100h pass (within the 240h/10-day B+C budget). Hit B's target, hand
    off to C. C's elapsed_hours must continue from the SAME window start (100h in), not reset to 0."""
    _set_combined_equity(monkeypatch, 1_000.0)
    pt.start_phase("B", now=0.0)
    _set_combined_equity(monkeypatch, 40_500.0)
    out = pt.check(now=100 * 3600.0)   # 100 hours later, B's target hit
    assert any(e["type"] == "target_hit" for e in out["fired"])
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == 37_000.0 and ev["reseed_usd"] == 3_000.0 and ev["next_phase"] == "C"

    # Ali does the manual cashout/reseed, then starts C -- window must carry forward
    _set_combined_equity(monkeypatch, 3_000.0)
    pt.start_phase("C", now=101 * 3600.0)
    s = pt.status(now=101 * 3600.0)
    assert s["phase"] == "C"
    assert s["window_group"] == "BC"
    # elapsed should be ~101h (carried from B's start at t=0), not ~0h
    assert s["elapsed_hours"] > 90.0
    assert s["allowed_hours"] == 240.0
    assert s["hours_left"] < 240.0 - 90.0


def test_phase_d_gets_its_own_fresh_window_after_c(monkeypatch):
    _set_combined_equity(monkeypatch, 3_000.0)
    pt.start_phase("C", now=500_000.0)
    _set_combined_equity(monkeypatch, 3_000.0)
    pt.start_phase("D", now=600_000.0)
    s = pt.status(now=600_000.0)
    assert s["phase"] == "D"
    assert s["window_group"] == "D"
    assert s["elapsed_hours"] == 0.0           # fresh clock, not carried from C/BC
    assert s["allowed_hours"] == 360.0          # 15 days
    assert s["next_phase"] is None


def test_phase_d_target_hit_has_no_reseed_or_next(monkeypatch):
    _set_combined_equity(monkeypatch, 3_000.0)
    pt.start_phase("D", now=1000.0)
    _set_combined_equity(monkeypatch, 41_000.0)
    out = pt.check(now=2000.0)
    ev = next(e for e in out["fired"] if e["type"] == "cashout_recommended")
    assert ev["cashout_usd"] == 37_000.0
    assert ev["reseed_usd"] is None
    assert ev["next_phase"] is None


def test_drawdown_breaker_pauses_without_target_hit(monkeypatch):
    _set_combined_equity(monkeypatch, 1000.0)
    pt.start_phase("A", now=1000.0)
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
    pt.start_phase("A", now=1000.0)
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
    pt.start_phase("A", now=1000.0)
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
    pt.start_phase("A", now=1000.0)
    _set_combined_equity(monkeypatch, 10_200.0)
    pt.check(now=2000.0)
    for book in ("dev_list", "leaderboard", "solana_sprint", "solana_compound"):
        ok, why = books.can_open(book)
        assert "paused" not in why.lower()

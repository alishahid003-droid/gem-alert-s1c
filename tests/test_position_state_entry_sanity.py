"""27 Oct 2026: entry-sanity rebase must refuse a garbled near-zero live read as a rebase
target -- a REAL incident on the paper side (executor/paper_ledger.py) showed this exact
unvalidated-rebase shape turning a $10.76 stake into a fictional $10,152,470 "win" once a
corrupted $0.04 mcap read replaced a real ~$59,303 entry. position_state.sanity_rebase_entry
is the identical code path for REAL-MONEY positions (once PumpPortal goes live), so it gets
the same floor."""
import state
import executor.position_state as ps

T0 = 1_790_700_000.0


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))


def test_rebase_accepts_a_plausible_far_away_read(monkeypatch, tmp_path):
    """Sanity check the existing, intended behavior still works: a real data-source mismatch
    (e.g. GeckoTerminal vs DexScreener units) still gets corrected."""
    _setup(monkeypatch, tmp_path)
    ps.record_stage_entry("solana", "A", "stage1", 30.0, 6_300, "test")
    rebased = ps.sanity_rebase_entry("solana", "A", 20_000_000, now=T0)
    assert rebased is True
    pos = ps.get_position("solana", "A")
    assert pos["stages"]["stage1"]["entry_mcap"] == 20_000_000
    assert pos["stages"]["stage1"]["entry_mcap_original"] == 6_300


def test_rebase_rejects_near_zero_garbled_read(monkeypatch, tmp_path):
    """The real incident, reproduced: a real ~$59,303 entry must NOT be rebased onto a
    garbled $0.04 read. The original entry is kept instead -- a bad read is worse than no
    rebase at all."""
    _setup(monkeypatch, tmp_path)
    ps.record_stage_entry("solana", "GARBLE", "stage1", 10.76, 59_303.35, "test")
    rebased = ps.sanity_rebase_entry("solana", "GARBLE", 0.04, now=T0)
    assert rebased is False
    pos = ps.get_position("solana", "GARBLE")
    assert pos["stages"]["stage1"]["entry_mcap"] == 59_303.35
    assert "entry_mcap_original" not in pos["stages"]["stage1"]


def test_rebase_rejects_read_right_at_the_floor_boundary(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ps.record_stage_entry("solana", "EDGE", "stage1", 10.0, 10_000.0, "test")
    assert ps.sanity_rebase_entry("solana", "EDGE", ps.ENTRY_SANITY_MIN_MCAP_USD - 1.0, now=T0) is False
    pos = ps.get_position("solana", "EDGE")
    assert pos["stages"]["stage1"]["entry_mcap"] == 10_000.0

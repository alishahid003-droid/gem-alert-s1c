"""Checklist 3.1/3.3/3.4/3.5/3.6 entry guards (Sept 30 2026)."""
import pytest

import state
import executor.entry_guards as g
import executor.entrypoint as entrypoint
import executor.paper_ledger as pl


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    yield


def test_clean_candidate_passes():
    assert g.check("B", 22.5, {"liquidity_usd": 80_000, "change_m5": 5, "change_h1": 40})[0]


def test_band_floor_score_only():
    ok, why = g.check("C", 20, {"liquidity_usd": 80_000})
    assert not ok and "band C" in why
    assert g.check("C", 20, {"liquidity_usd": 80_000, "signals": 2})[0]     # trusted deployer etc.


def test_thin_pool_and_pool_share():
    assert "liquidity" in g.check("A", 20, {"liquidity_usd": 4_000})[1]
    assert "of the pool" in g.check("A", 300, {"liquidity_usd": 12_000})[1]


def test_vertical_candle_and_late_entry():
    assert "5 min" in g.check("A", 20, {"liquidity_usd": 90_000, "change_m5": 80})[1]
    assert "1 h" in g.check("A", 20, {"liquidity_usd": 90_000, "change_h1": 450})[1]
    assert g.check("C", 20, {"liquidity_usd": 90_000, "change_h1": 450}, momentum=True)[0]  # momentum: 1h ok


def test_snipers():
    assert "snipers" in g.check("A", 20, {"liquidity_usd": 90_000, "sniper_pct": 0.4})[1]


def test_confluence_switch(monkeypatch):
    monkeypatch.setenv("REQUIRE_CONFLUENCE", "true")
    assert "one confirming signal" in g.check("A", 20, {"liquidity_usd": 90_000, "signals": 1})[1]
    assert g.check("A", 20, {"liquidity_usd": 90_000, "signals": 2})[0]


def test_unknown_data_never_blocks():
    assert g.check("A", 20, {})[0]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("REAL_MONEY_MIN_BAND", "A")
    assert not g.check("B", 20, {"liquidity_usd": 90_000})[0]


def test_stage1_guard_blocks_real_but_paper_records(monkeypatch):
    r = entrypoint.handle_stage1_candidate("solana", "GT1", "B", None, 0, 100_000, signal_coverage=0.9,
                                           liquidity_usd=90_000, entry_ctx={"change_m5": 120})
    assert r["fired"] is False and "entry guard" in r["reason"]
    sb = pl.scoreboard()
    assert sb["open_count"] == 1
    assert list(pl._open().values())[0]["guard"] == "blocked"

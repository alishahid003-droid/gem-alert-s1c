"""Phase 2 paper-trading ledger (Sept 30 2026)."""
import pytest

import state
import executor.paper_ledger as pl
import executor.entrypoint as entrypoint

T0 = 1_790_700_000.0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    # mechanics tests pin the rule values; defaults are tested separately
    monkeypatch.setenv("STOP_LOSS_PCT", "0.25")
    monkeypatch.setenv("TRAIL_GIVEBACK_PCT", "0.35")
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    yield


def snap(mcap, liq=50_000):
    return lambda chain, token: {"mcap_usd": mcap, "liquidity_usd": liq}


def test_classify_signal():
    assert pl.classify_stage1_signal("A", None, 0, 0.8) == "score_band_A"
    assert pl.classify_stage1_signal("B", None, 0, 0.2) is None           # coverage gate
    assert pl.classify_stage1_signal("C", "good", 0) == "deployer_trusted"
    assert pl.classify_stage1_signal("D", None, 3) == "wallet_convergence"
    assert pl.classify_stage1_signal("C", None, 0) is None


def test_open_dedupe_and_chain_filter():
    assert pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0)
    assert pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0) is None
    assert pl.open_paper("base", "X", "stage1", "score_band_A", 30, 100_000, now=T0) is None


def test_stop_loss_trade_is_a_loss():
    pl.open_paper("solana", "M", "stage1", "score_band_B", 30, 100_000, now=T0)
    pl.manage(snap(70_000), now=T0 + 60)
    sb = pl.scoreboard()
    assert sb["overall"]["n"] == 1 and sb["overall"]["wins"] == 0
    assert sb["by_exit_type"]["stop_loss"]["n"] == 1
    assert sb["recent"][0]["pnl_pct"] < -25


def test_breakeven_lock_then_stop_is_a_win():
    pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0)
    pl.manage(snap(160_000), now=T0 + 60)       # lock
    pl.manage(snap(70_000), now=T0 + 120)       # stopped out
    r = pl.scoreboard()["recent"][0]
    assert r["pnl_usd"] > 0                      # stake already recovered -> locked win


def test_rug_written_off_as_full_loss():
    pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0)
    for i in range(pl.UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF):
        pl.manage(lambda c, t: None, now=T0 + 60 * (i + 1))
    r = pl.scoreboard()["recent"][0]
    assert r["exit_type"] == "written_off" and r["pnl_pct"] == -100.0


def test_moonbag_rung_and_trailing():
    pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0)
    pl.manage(snap(160_000), now=T0 + 60)       # breakeven lock
    pl.manage(snap(320_000), now=T0 + 120)      # 3x rung (scaled)
    pl.manage(snap(190_000), now=T0 + 180)      # trailing: >35% off 3.2x peak
    r = pl.scoreboard()["recent"][0]
    assert r["exit_type"] == "trailing_stop" and r["pnl_usd"] > 0


def test_signal_gate_blocks_losing_signal(monkeypatch):
    monkeypatch.setenv("MIN_TRADES_FOR_VERDICT", "3")
    for i in range(3):
        pl.open_paper("solana", f"L{i}", "stage1", "score_band_B", 30, 100_000, now=T0)
    pl.manage(snap(60_000), now=T0 + 60)
    ok, why = pl.signal_allowed("score_band_B")
    assert ok is False and "below" in why
    assert pl.signal_allowed("score_band_A")[0] is True  # no data yet -> allowed


def test_entrypoint_opens_paper_even_when_budget_blocks(monkeypatch):
    import executor.triggers as triggers
    monkeypatch.setattr(triggers.position_state, "stage_committed_usd", lambda stage: 10_000.0)
    r = entrypoint.handle_stage1_candidate("solana", "PB", "A", None, 0, 100_000, signal_coverage=0.9)
    assert r["fired"] is False and "budget" in r["reason"]
    assert pl.scoreboard()["open_count"] == 1


def test_entrypoint_gate_blocks_real_but_keeps_paper(monkeypatch):
    monkeypatch.setattr(pl, "signal_allowed", lambda s: (False, "bad record"))
    r = entrypoint.handle_stage1_candidate("solana", "PG", "A", None, 0, 100_000, signal_coverage=0.9)
    assert r["fired"] is False and "paper-record gate" in r["reason"]
    assert pl.scoreboard()["open_count"] == 1

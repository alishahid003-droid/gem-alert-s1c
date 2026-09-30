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
    assert pl.open_paper("base", "X", "stage1", "score_band_A", 30, 100_000, now=T0)   # Base: paper-tracked
    assert pl.open_paper("ethereum", "Y", "stage1", "score_band_A", 30, 100_000, now=T0) is None


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
    assert pl.scoreboard()["open_count"] == 1          # 3 misses in 3 min is a hiccup, not a rug
    pl.manage(lambda c, t: None, now=T0 + 60 + 31 * 60)
    r = pl.scoreboard()["recent"][0]
    assert r["exit_type"] == "written_off" and r["pnl_pct"] == -100.0


def test_moonbag_rung_and_trailing():
    pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0)
    pl.manage(snap(160_000), now=T0 + 60)       # breakeven lock
    pl.manage(snap(320_000), now=T0 + 120)      # 3x rung (scaled)
    pl.manage(snap(190_000), now=T0 + 180)      # trailing: >35% off 3.2x peak
    r = pl.scoreboard()["recent"][0]
    assert r["exit_type"] == "trailing_stop" and r["pnl_usd"] > 0
    sb = pl.scoreboard()
    assert sb["runners"]["riding"] == 1 and sb["overall"]["n"] == 1   # runner rides on, trade scored
    pl.manage(snap(700_000), now=T0 + 240)      # runner survives the dip and runs to 7x
    pl.manage(snap(300_000), now=T0 + 300)      # -57% from 7x: still riding (deep trail = 75%)
    assert pl.scoreboard()["runners"]["riding"] == 1
    pl.manage(snap(150_000), now=T0 + 360)      # -79% from peak: runner trail exits
    sb = pl.scoreboard()
    assert sb["runners"]["riding"] == 0 and sb["runners"]["n"] == 1 and sb["runners"]["best_multiple"] == 7.0
    assert sb["overall"]["n"] == 1              # runner not double-counted in the win rate


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


def test_batch_pricing_one_call_per_chain():
    for i in range(5):
        pl.open_paper("solana", f"B{i}", "stage1", "score_band_A", 30, 100_000, now=T0)
    calls = []

    def batch(chain, tokens):
        calls.append((chain, len(tokens)))
        return {t: {"marketCap": 40_000, "liquidity": {"usd": 50_000}} for t in tokens}
    pl.manage(now=T0 + 60, batch_fn=batch)
    assert calls == [("solana", 5)]
    assert pl.scoreboard()["overall"]["n"] == 5        # all stopped out at -60%


def test_paper_entry_sanity_rebase():
    pl.open_paper("solana", "B", "stage1", "score_band_B", 30, 6_300, now=T0)
    pl.manage(snap(20_000_000), now=T0 + 60)          # 3,173x in a minute = data mismatch
    pos = pl._open()["stage1:solana:B"]
    assert pos["entry_mcap"] == 20_000_000 and pos["entry_mcap_original"] == 6_300
    assert pl.scoreboard()["overall"]["n"] == 0       # no fake win booked


def test_pre_fix_trades_excluded_from_stats(monkeypatch):
    pl.open_paper("solana", "OLD", "stage1", "score_band_B", 30, 100_000, now=T0)
    pl.manage(snap(40_000), now=T0 + 60)                    # a loss, opened "before the fix"
    assert pl.scoreboard()["overall"]["n"] == 1
    monkeypatch.setattr(pl, "STATS_SINCE_TS", T0 + 1)
    assert pl.scoreboard()["overall"]["n"] == 0             # excluded from win rate
    assert len(pl._closed_raw()) == 1                        # but kept for history

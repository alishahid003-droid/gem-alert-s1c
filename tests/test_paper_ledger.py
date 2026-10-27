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
    """28 Oct 2026: a position that is NEVER priced from entry now uses the short never-priced
    window (see test_never_priced_since_entry_writes_off_fast), not the long original one --
    this test moved to using that short window for its timing to stay correct."""
    pl.open_paper("solana", "M", "stage1", "score_band_A", 30, 100_000, now=T0)
    for i in range(int(pl.NEVER_PRICED_CYCLES_BEFORE_WRITE_OFF)):
        pl.manage(lambda c, t: None, now=T0 + 20 * (i + 1))
    assert pl.scoreboard()["open_count"] == 1          # a few misses in under 2 min is a hiccup, not a rug
    pl.manage(lambda c, t: None, now=T0 + 20 + pl.NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF * 60 + 1)
    r = pl.scoreboard()["recent"][0]
    assert r["exit_type"] == "written_off" and r["pnl_pct"] == -100.0


def test_never_priced_since_entry_writes_off_fast(monkeypatch):
    """28 Oct 2026 (Ali): robinhood_chain trades were dying at 96% post-restart, ALL with
    last_mcap=None -- never priced even once, yet held for the full 8-min grace period meant
    for a position that WAS pricing fine and then had a brief hiccup. A pool the price source
    never indexes at all is a different, worse situation and should give up far sooner."""
    monkeypatch.setattr(pl, "STATS_SINCE_TS", T0 - 1)
    pl.open_paper("robinhood_chain", "NEW", "early_sprint", "early_sprint", 30, 100_000,
                  now=T0, strategy="early", exit_profile="sprint", module="sprint")
    for i in range(int(pl.NEVER_PRICED_CYCLES_BEFORE_WRITE_OFF)):
        pl.manage(lambda c, t: None, now=T0 + 20 * (i + 1))
    # still within the short never-priced cycle count but not yet the short minute threshold
    assert pl.scoreboard()["open_count"] == 1
    pl.manage(lambda c, t: None,
              now=T0 + 20 + pl.NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF * 60 + 1)
    rows = [r for r in pl._closed() if r["id"] == "early_sprint:robinhood_chain:NEW"]
    assert len(rows) == 1
    r = rows[0]
    assert r["exit_type"] == "written_off"
    assert "never priceable" in r["close_reason"]
    assert r["pnl_pct"] == -100.0


def test_priced_then_goes_dark_keeps_the_longer_grace_period(monkeypatch):
    """Contrast case: a position that DID get priced at least once, then loses its feed, is a
    data hiccup, not dead-on-arrival -- it must keep the full (longer) original grace period,
    not the new short never-priced one."""
    monkeypatch.setattr(pl, "STATS_SINCE_TS", T0 - 1)
    pl.open_paper("robinhood_chain", "WAS_LIVE", "early_sprint", "early_sprint", 30, 100_000,
                  now=T0, strategy="early", exit_profile="sprint", module="sprint")
    pl.manage(snap(100_000), now=T0 + 60)       # priced successfully once
    assert pl.scoreboard()["open_count"] == 1
    # now the feed goes dark -- tick through several cycles past the SHORT never-priced window;
    # must still be open because this position has a real last_mcap and should use the longer
    # grace period, not the short one
    base = T0 + 60
    for i in range(int(pl.UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF)):
        pl.manage(lambda c, t: None, now=base + (pl.NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF * 60) + i + 1)
    assert pl.scoreboard()["open_count"] == 1          # not written off yet -- longer grace applies
    # now wait past the full original long grace period, measured from when it actually went
    # dark (unpriced_since), not from the last successful price -- should finally write off
    unpriced_since = pl._open()["early_sprint:robinhood_chain:WAS_LIVE"]["unpriced_since"]
    pl.manage(lambda c, t: None, now=unpriced_since + pl.UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF * 60 + 30)
    rows = [r for r in pl._closed() if r["id"] == "early_sprint:robinhood_chain:WAS_LIVE"]
    assert len(rows) == 1
    r = rows[0]
    assert r["exit_type"] == "written_off"
    assert "delisted/rugged" in r["close_reason"]


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


def test_entry_rebase_rejects_near_zero_garbled_read():
    """27 Oct 2026 fix: a REAL incident -- the entry-sanity rebase used to trust whatever the new
    live read was, with no floor on it being plausible. One core1 trade had its real ~$59,303 entry
    rebased onto a garbled $0.04 read, turning a $10.76 stake into a fictional $10,152,470 'win' once
    any real price came back in against that near-zero base. A bad read is worse than keeping the
    original entry -- the rebase must now refuse an implausibly-tiny proposed entry_mcap, so the
    position is priced/exited against its REAL entry instead (here: correctly stopped out for a
    bounded, real loss of the stake -- not a fabricated multi-million-dollar 'win')."""
    pl.open_paper("solana", "GARBLE", "early_core1", "early_core1", 10.76, 59_303.35,
                  strategy="early", exit_profile="core1", module="core1", now=T0)
    pl.manage(snap(0.04), now=T0 + 60)          # the garbled near-zero read
    rows = pl.module_rows("core1", include_runners=True)
    assert rows, "position should have closed (correctly stopped out against its real entry)"
    # the bug produced pnl_usd = +10,152,470.17 on a $10.76 stake -- any bounded, real-scale
    # loss or win here proves the rebase guard held; a multi-digit-thousand swing proves it didn't.
    assert -10.76 <= rows[0]["pnl_usd"] <= 100, f"pnl_usd={rows[0]['pnl_usd']} -- rebase guard failed"
    assert rows[0]["pnl_usd"] < 0 and rows[0]["win"] is False


def test_pre_fix_trades_excluded_from_stats(monkeypatch):
    pl.open_paper("solana", "OLD", "stage1", "score_band_B", 30, 100_000, now=T0)
    pl.manage(snap(40_000), now=T0 + 60)                    # a loss, opened "before the fix"
    assert pl.scoreboard()["overall"]["n"] == 1
    monkeypatch.setattr(pl, "STATS_SINCE_TS", T0 + 1)
    assert pl.scoreboard()["overall"]["n"] == 0             # excluded from win rate
    assert len(pl._closed_raw()) == 1                        # but kept for history


def test_liquidity_collapse_never_books_fake_profit():
    """Oct 13 2026: a drained pool with a glitched huge mcap read booked +$505 on a $30 stake."""
    pl.open_paper("solana", "TOKX", "early_sprint", "early_sprint", 30.0, 23_000, strategy="early",
                  exit_profile="sprint", module="sprint", now=T0)
    pl.manage(snapshot_fn=snap(460_000, liq=0), now=T0 + 120)
    rows = pl.module_rows("sprint", include_runners=True)
    assert rows and rows[0]["pnl_usd"] < 0 and rows[0]["close_reason"].startswith("liquidity")


def test_legacy_paper_traders_retired_by_default(monkeypatch):
    monkeypatch.setenv("LEGACY_PAPER_ENABLED", "false")
    assert pl.open_paper("solana", "TOKL", "stage1", "score_band_A", 25.0, 50_000, strategy="scalper") is None
    assert pl.open_paper("solana", "TOKE", "early_sprint", "early_sprint", 25.0, 50_000, strategy="early",
                         exit_profile="sprint", module="sprint") is not None

import os
import time

import pytest

import state
from executor import paper_ledger, pumpportal_exec
from early_recorder import Recorder
from layers.layer17_early_entry import EarlyTracker


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("GEM_ALERT_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.chdir(tmp_path)
    for k in ("EXECUTION_ENABLED", "PUMPPORTAL_API_KEY", "EARLY_LIVE_LANES"):
        monkeypatch.delenv(k, raising=False)


def _open(lane="core2", usd=20.0, now=1000.0, **kw):
    return paper_ledger.open_paper("solana", "MINT1", f"early_{lane}", f"early_{lane}", usd, 5000.0,
                                   strategy="early", exit_profile=lane, now=now, **kw)


def _run(mcap, now):
    return paper_ledger.manage(lambda c, t: {"mcap_usd": mcap, "liquidity_usd": 9999}, now=now, source_prefix="early_")


def test_take_profits_then_trailing_stop():
    # 7 Nov 2026: moved from core2 (default lane) to core1. core2's ladder/trail-ratchet were
    # redesigned as part of the sprint/core1/core2 differentiation work (Ali: "these three modules
    # should differ... not everything identical") -- core2 now also has a moonshot carve-out that
    # intercepts a position once it peaks >= CORE2_MOONSHOT_TRIGGER_MULT (8.0x, exactly where this
    # test's own sequence peaks), so running this exact scenario on core2 no longer produces a clean
    # full-sell trailing_stop -- it produces a deliberate partial carve, covered by its own dedicated
    # test (test_core2_moonshot_carveout_leaves_a_slice_running below). core1's ladder/trail-ratchet
    # were NOT changed by the differentiation work, so this test now exercises core1 instead, to keep
    # a clean "full ladder then trailing stop, nothing carved" scenario covered somewhere.
    pos = _open(lane="core1")
    assert pos["cost_pct"] >= 0.10
    # core1 ladder: {1.3: 0.10, 2: 0.15, 4: 0.15, 10: 0.10}. One tick straight to 2.1x crosses both
    # the 1.3x and 2x tiers in the same manage() cycle -> remaining = 1.0 - 0.10 - 0.15 = 0.75.
    _run(10500, 1010)                      # 2.1x -> sells 1.3x rung (10%) + 2x rung (15%) = 25%
    p = paper_ledger._open()["early_core1:solana:MINT1"]
    assert abs(p["remaining"] - 0.75) < 1e-6
    _run(25000, 1020)                      # 5x -> 4x rung (15%) fires, remaining -> 0.60
    _run(40000, 1030)                      # 8x peak -- below core1's 10x rung, nothing new fires
    _run(15000, 1040)                      # 3x = 37.5% of the 8x peak -> below core1's 8x-band trail
                                            # lock (0.70) -> trailing_stop on the whole remainder
    assert not paper_ledger._open()
    closed = paper_ledger._closed()[-1]
    assert closed["exit_type"] == "trailing_stop" and closed["pnl_usd"] > 20


def test_hard_stop_and_dead_coin_time_stop():
    _open()
    _run(1500, 1010)                       # 0.3x -> stop (0.35)
    assert paper_ledger._closed()[-1]["exit_type"] == "stop_loss"
    paper_ledger.open_paper("solana", "M2", "early_core1", "early_core1", 10, 5000, strategy="early",
                            exit_profile="core1", now=1000.0)
    # 7 Nov 2026: was mcap=5200 (1.04x) -- that falls BELOW the EARLY_NO_POP_MULT (1.05) threshold,
    # so EARLY_NO_POP fires first with exit_type=stop_loss (same as the original, pre-differentiation
    # code). The original test only read time_stop because of a real, separate pre-existing bug: the
    # dead-coin time_stop was chained as an elif off the (unrelated) hard-stop/trailing-stop if-block
    # rather than off EARLY_NO_POP, so it silently OVERWROTE an already-set stop_loss with time_stop --
    # same dollars sold at the same mcap (pnl unaffected), just a mislabeled exit_type. That elif-chain
    # bug is what the 7 Nov 2026 moonshot-carveout fix corrected (it also made the 24h max-hold safety
    # net reachable for a fully-carved core2 position, which is the actual bug that fix targets -- see
    # EARLY_RULES comments above). 5500 (1.10x) clears the no-pop band (>=1.05) while staying inside
    # the genuine dead-coin band (<1.2x, peak <1.5x), so this now actually exercises the time_stop
    # safety net in isolation instead of accidentally exercising the no-pop path.
    paper_ledger.manage(lambda c, t: {"mcap_usd": 5500, "liquidity_usd": 9999}, now=1000 + 100 * 60, source_prefix="early_")
    assert paper_ledger._closed()[-1]["exit_type"] == "time_stop"


def test_source_prefix_leaves_other_positions_alone():
    paper_ledger.open_paper("solana", "OTHER", "radar_news", "x", 10, 5000, now=1000.0)
    _open()
    _run(10500, 1010)
    assert "radar_news:solana:OTHER" in paper_ledger._open()


def test_live_gating_and_kill_switch(monkeypatch):
    assert pumpportal_exec.allowed("core2", 20)[0] is False
    monkeypatch.setenv("EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "k")
    monkeypatch.setenv("EARLY_LIVE_LANES", "core2")
    monkeypatch.setenv("LIVE_MAX_STAKE_USD", "5")
    ok, why, stake = pumpportal_exec.allowed("core2", 20)
    assert ok and stake == 5.0                     # capped by LIVE_MAX_STAKE_USD default
    assert pumpportal_exec.allowed("core1", 20)[0] is False
    open("KILL_SWITCH", "w").write("on")
    assert pumpportal_exec.allowed("core2", 20) == (False, "KILL SWITCH on", 0.0)


def test_live_sell_is_mirrored(monkeypatch):
    calls = []
    monkeypatch.setattr(pumpportal_exec, "sell", lambda m, pct: calls.append((m, round(pct))) or {"ok": True})
    # 7 Nov 2026: moved to core1 for the same reason as test_take_profits_then_trailing_stop above --
    # core2's ladder was redesigned by the differentiation work (1.5x rung at 5%, not 1.3x at 10%).
    # core1's ladder is untouched: {1.3: 0.10, 2: 0.15, 4: 0.15, 10: 0.10}.
    _open(lane="core1", live=True)
    # One tick to 2.1x fires both the 1.3x and 2x rungs in the same cycle. Each rung sells its
    # fraction of the ORIGINAL stake as a pct of what's CURRENTLY remaining when it fires: the 1.3x
    # rung fires first at 100% remaining -> sells 10% of that (round(pct)=10); the 2x rung then fires
    # against the new 90% remaining -> sells 0.15/0.90 = ~16.7% of what's left (round=17).
    _run(10500, 1010)
    assert calls == [("MINT1", 10), ("MINT1", 17)]


def test_daily_loss_cap(monkeypatch):
    monkeypatch.setenv("EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "k")
    monkeypatch.setenv("EARLY_LIVE_LANES", "core2")
    monkeypatch.setenv("LIVE_DAILY_LOSS_USD", "3")
    _open(live=True, usd=5.0, now=time.time())
    _run(1500, time.time() + 1)            # stop loss on a live position
    assert paper_ledger._closed()[-1]["pnl_usd"] < -3
    assert "daily loss cap" in pumpportal_exec.allowed("core2", 5)[1]


def test_bundled_launch_blocked():
    t = EarlyTracker()
    t.on_create({"txType": "create", "mint": "B", "traderPublicKey": "dev", "initialBuy": 1e6, "solAmount": .03,
                 "marketCapSol": 28}, now=100.0)
    for i in range(11):
        t.on_trade({"txType": "buy", "mint": "B", "traderPublicKey": f"w{i}", "solAmount": 1, "marketCapSol": 30}, now=101.0)
    assert t.block_reason(t.recs["B"]).startswith("bundled")


def test_recorder_samples_outcomes(tmp_path):
    r = Recorder(str(tmp_path / "log"))
    r.on_create({"mint": "A", "traderPublicKey": "d", "marketCapSol": 28, "symbol": "A"}, True, False, now=100.0)
    assert r.due(now=200.0) == []
    seen = []
    n = r.sample(lambda c, ms: seen.append(ms) or {"A": {"marketCap": 9000, "liquidity": {"usd": 5000}}}, now=100.0 + 301)
    assert n == 1 and seen == [["A"]] and r.pending["A"][1] == 1
    r.sample(lambda c, ms: {}, now=100.0 + 3601)
    r.sample(lambda c, ms: {}, now=100.0 + 3601)
    assert "A" not in r.pending
    assert any(f.endswith(".jsonl") for f in os.listdir(tmp_path / "log"))


def test_creator_reputation_and_lane(tmp_path):
    from early_creators import Creators
    c = Creators(str(tmp_path / "c.json"))
    c.on_create("DEVX", "M1", now=1000.0)
    assert not c.repeat_grad("DEVX", now=1001.0)
    assert c.on_graduate("M1", now=1200.0) == "DEVX"
    c.on_create("DEVX", "M2", now=1000.0 + 2 * 86400)             # second launch two days later
    assert c.repeat_grad("DEVX", now=1000.0 + 2 * 86400)
    c.save(now=1000.0 + 2 * 86400)
    assert Creators(str(tmp_path / "c.json")).t["DEVX"]["grads"] == 1
    t = EarlyTracker()
    t.creator_fn = c.repeat_grad
    t.feed_off = True
    t.on_create({"txType": "create", "mint": "M2", "traderPublicKey": "DEVX", "initialBuy": 1e6, "solAmount": .03,
                 "marketCapSol": 28}, now=1000.0 + 2 * 86400)
    d = t.evaluate("M2", now=1001.0 + 2 * 86400)
    assert d and d["lane"] == "dev_list" and "repeat_grad" in d["confirmations"]


def test_analyze_script_runs(tmp_path, monkeypatch, capsys):
    import json, runpy, sys
    monkeypatch.chdir(tmp_path)
    os.makedirs("early_log")
    rows = []
    for i in range(300):
        rows.append({"t": "create", "ts": 1.0, "mint": f"m{i}", "init_sol": 0.1, "mcap_sol": 28, "dev_list": i < 10, "repeat_grad": False})
    rows += [{"t": "grad", "ts": 2.0, "mint": f"m{i}"} for i in range(0, 8)]
    open("early_log/2026-10-13.jsonl", "w").write("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(sys, "argv", ["analyze_early.py"])
    src = os.path.join(os.path.dirname(__file__), "..", "analyze_early.py")
    runpy.run_path(src)
    out = capsys.readouterr().out
    assert "GRADUATION LIFT" in out and "dev-list creator" in out


# ---- 1.3x sub-rung (Ali, 31 Oct 2026) -----------------------------------------------------------

def test_sub_2x_rung_fires_and_banks_profit_before_a_reversal():
    """31 Oct 2026: the exact gap diagnosed that day -- a coin that runs to 1.3x-2x then reverses
    used to get ZERO profit protection (first rung was 2x). Now a coin that clears core2's first
    rung and then reverses back toward entry should have banked something real on the way, not
    ridden the whole move down to a full loss on the stake.
    7 Nov 2026: core2's ladder was redesigned by the sprint/core1/core2 differentiation work --
    first rung moved from 1.3x/10% to 1.5x/5% (small, conservative stakes: cut losers fast via a
    tighter hard stop, reserve risk tolerance for proven winners via the moonshot carve-out instead
    of a generous early ladder). Updated to the new rung/stop values; the shape of what's being
    tested (a sub-2x rung protects something before a reversal) is unchanged."""
    _open(usd=100.0)                       # core2, $100 stake, entry mcap 5000
    _run(7500, 1010)                       # 1.5x -> fires the rung, sells 5% of original
    p = paper_ledger._open()["early_core2:solana:MINT1"]
    assert abs(p["remaining"] - 0.95) < 1e-6
    assert "1.5" in p["rungs_fired"]
    # reverses hard toward entry -- without the rung, this would be a near-total loss on $100;
    # with it, 5% of the stake was already banked as real profit before the drop. core2's hard
    # stop now fires at mult <= 0.45 (tightened 7 Nov 2026, see EARLY_RULES), so drop well under
    # that to trip it cleanly.
    _run(1500, 1020)                       # 0.3x of entry -> hard stop (<=0.45)
    closed = paper_ledger._closed_raw()[-1]    # _closed() filters to STATS_SINCE_TS; this test uses epoch-near timestamps
    assert closed["exit_type"] == "stop_loss"
    # the 5% already sold at 1.5x locked in profit on that slice -- bounds the overall loss
    # tighter than a full-stake stop-loss from entry would (sanity bound, not an exact figure)
    assert closed["pnl_usd"] > -100.0 * 0.80   # well short of a full -$80+ loss on $100


def test_sub_2x_rung_does_not_fire_below_1_5x():
    """Contrast: a coin that never reaches core2's new 1.5x first rung should not have it fire."""
    _open(usd=100.0)
    _run(7000, 1010)                       # 1.4x -- clears the old 1.3x threshold but below the new 1.5x one
    p = paper_ledger._open()["early_core2:solana:MINT1"]
    assert p["remaining"] == 1.0
    assert p["rungs_fired"] == []


def test_compound_sub_2x_rung_uses_its_own_25pct_fraction():
    """Compound's new 1.3x rung takes 25% (not core2/sprint's 15%) -- matches its existing, more
    aggressive profit-banking design (its old first rung at 2x already took 60%, far more than
    sprint/core1/core2's 20%)."""
    paper_ledger.open_paper("solana", "CMINT", "early_compound", "early_compound", 100.0, 5000.0,
                            strategy="early", exit_profile="compound", now=1000.0)
    paper_ledger.manage(lambda c, t: {"mcap_usd": 6500, "liquidity_usd": 9999}, now=1010,
                        source_prefix="early_")
    p = paper_ledger._open()["early_compound:solana:CMINT"]
    assert abs(p["remaining"] - 0.75) < 1e-6
    assert "1.3" in p["rungs_fired"]


def test_sprint_sub_2x_rung_uses_its_own_ladder():
    """2 Nov 2026: sprint's 1.3x rung trimmed from 15% to 10% (same change as core2/core1).
    7 Nov 2026: sprint's whole ladder was redesigned by the differentiation work -- fastest/biggest
    stakes, so it takes profit earlier and more aggressively: {1.2: 0.20, 1.6: 0.20, 2.5: 0.15,
    5: 0.10} (vs core1's {1.3: 0.10, 2: 0.15, 4: 0.15, 10: 0.10}). A tick to 1.3x now clears
    sprint's first rung at 1.2x (20%), not the old uniform 1.3x rung."""
    paper_ledger.open_paper("solana", "SMINT", "early_sprint", "early_sprint", 100.0, 5000.0,
                            strategy="early", exit_profile="sprint", now=1000.0)
    paper_ledger.manage(lambda c, t: {"mcap_usd": 6500, "liquidity_usd": 9999}, now=1010,
                        source_prefix="early_")
    p = paper_ledger._open()["early_sprint:solana:SMINT"]
    assert abs(p["remaining"] - 0.80) < 1e-6
    assert "1.2" in p["rungs_fired"]


def test_dev_list_and_leaderboard_unaffected_by_sub_2x_rung():
    """dev_list/leaderboard/fast_* were NOT backtested for this change (no evidence pulled for
    them yet) and must keep their original ladders untouched."""
    paper_ledger.open_paper("solana", "DMINT", "early_dev_list", "early_dev_list", 100.0, 5000.0,
                            strategy="early", exit_profile="dev_list", now=1000.0)
    paper_ledger.manage(lambda c, t: {"mcap_usd": 6500, "liquidity_usd": 9999}, now=1010,
                        source_prefix="early_")
    p = paper_ledger._open()["early_dev_list:solana:DMINT"]
    assert p["remaining"] == 1.0            # 1.3x is still below dev_list's unchanged first rung (2x)
    assert p["rungs_fired"] == []


# ---- core2 moonshot carve-out (Ali, 7 Nov 2026) --------------------------------------------------

def test_core2_moonshot_carveout_leaves_a_slice_running():
    """7 Nov 2026 (Ali): "at least one of these three modules should have this instinct -- 20-25%
    should bypass the ratchet entirely and aim for that moonshot attempt." core2 got this: once a
    position's peak crosses CORE2_MOONSHOT_TRIGGER_MULT (default 8.0x), CORE2_MOONSHOT_CARVEOUT_PCT
    (default 0.22) of the ORIGINAL stake is carved out and exempted from the trailing stop from then
    on -- only the non-carved remainder is sold when the trail would normally fire. The carved slice
    keeps riding with no trailing stop; only the dead-coin time-stop and 24h max-hold safety nets can
    still close it (see the EARLY_RULES/elif-chain comments for why that safety net had to be fixed
    first)."""
    paper_ledger.open_paper("solana", "MOON", "early_core2", "early_core2", 100.0, 5000.0,
                            strategy="early", exit_profile="core2", now=1000.0)
    # a near-entry read first, so the entry-sanity-rebase check (position_state) doesn't treat an
    # immediate 8x-in-one-tick as a bad/garbled first read and rebase entry_mcap onto it instead
    paper_ledger.manage(lambda c, t: {"mcap_usd": 5100, "liquidity_usd": 9999}, now=1010, source_prefix="early_")
    # now run the ladder up through the 8x trigger
    paper_ledger.manage(lambda c, t: {"mcap_usd": 40000, "liquidity_usd": 9999}, now=1020, source_prefix="early_")
    p = paper_ledger._open()["early_core2:solana:MOON"]
    assert p.get("moonshot_carved") is True
    assert abs(p.get("moonshot_carve_pct", 0.0) - 0.22) < 1e-6
    remaining_at_trigger = p["remaining"]      # 0.75 -- the ladder (1.5x/3x/8x rungs) already sold
                                                # 25%; the carve marks 0.22 of the ORIGINAL stake as
                                                # exempt but hasn't sold anything new yet itself
    # now pull back hard -- far enough to fire the trailing stop on the non-carved portion
    paper_ledger.manage(lambda c, t: {"mcap_usd": 15000, "liquidity_usd": 9999}, now=1030, source_prefix="early_")
    p = paper_ledger._open()["early_core2:solana:MOON"]
    # the carved slice is still open and still exactly its carved size -- nothing sold it
    assert abs(p["remaining"] - 0.22) < 1e-6
    assert remaining_at_trigger > 0.22       # confirms the trailing stop actually sold something
    # the dead-coin time-stop and 24h max-hold are the only things that can still close a fully
    # carved position -- confirm the 24h safety net actually reaches it (the bug this fix corrected:
    # it used to be unreachable because it was chained as an elif off the trailing-stop branch, which
    # is always "entered" once a coin has run, even when the fully-carved fall-through sells nothing).
    paper_ledger.manage(lambda c, t: {"mcap_usd": 15000, "liquidity_usd": 9999},
                        now=1030 + 1441 * 60, source_prefix="early_")
    assert "early_core2:solana:MOON" not in paper_ledger._open()
    closed = paper_ledger._closed_raw()[-1]
    assert closed["exit_type"] == "max_hold"

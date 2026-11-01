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
    pos = _open()
    assert pos["cost_pct"] >= 0.10
    # 2 Nov 2026 (Ali audit): core2's 1.3x/2x rungs trimmed from (0.15, 0.20) to (0.10, 0.15) --
    # more of a real winner rides, since a $50 stake's runner alone would need ~50,000x to reach
    # $1M and raising it further gives up more on the 2-4x band that's already working on real
    # data. One tick straight to 2.1x crosses BOTH tiers in the same manage() cycle, so remaining
    # is now 1.0 - 0.10 - 0.15 = 0.75.
    _run(10500, 1010)                      # 2.1x -> sells 1.3x rung (10%) + 2x rung (15%) = 25%
    p = paper_ledger._open()["early_core2:solana:MINT1"]
    assert abs(p["remaining"] - 0.75) < 1e-6
    _run(25000, 1020)                      # 5x -> 4x rung sells 25%
    _run(40000, 1030)                      # 8x peak
    _run(15000, 1040)                      # 3x = 37% of peak -> trailing stop (0.40)
    assert not paper_ledger._open()
    closed = paper_ledger._closed()[-1]
    assert closed["exit_type"] == "trailing_stop" and closed["pnl_usd"] > 20


def test_hard_stop_and_dead_coin_time_stop():
    _open()
    _run(1500, 1010)                       # 0.3x -> stop (0.35)
    assert paper_ledger._closed()[-1]["exit_type"] == "stop_loss"
    paper_ledger.open_paper("solana", "M2", "early_core1", "early_core1", 10, 5000, strategy="early",
                            exit_profile="core1", now=1000.0)
    paper_ledger.manage(lambda c, t: {"mcap_usd": 5200, "liquidity_usd": 9999}, now=1000 + 100 * 60, source_prefix="early_")
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
    _open(live=True)
    # 2 Nov 2026: ladder trimmed to (0.10, 0.15) -- see test_take_profits_then_trailing_stop. One
    # tick to 2.1x fires both rungs in the same cycle. Each rung sells its fraction of the ORIGINAL
    # stake as a pct of what's CURRENTLY remaining when it fires: the 1.3x rung fires first at 100%
    # remaining -> sells 10% of that (round(pct)=10); the 2x rung then fires against the new 90%
    # remaining -> sells 0.15/0.90 = ~16.7% of what's left (round=17).
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
    used to get ZERO profit protection (first rung was 2x). Now a coin that peaks at 1.5x (clears
    the 1.3x core2 rung, trimmed 2 Nov 2026 to 10%) and then reverses back toward entry should have
    banked something real on the way, not ridden the whole move down to a full loss on the stake."""
    _open(usd=100.0)                       # core2, $100 stake, entry mcap 5000
    _run(6500, 1010)                       # 1.3x -> fires the rung, sells 10% of original
    p = paper_ledger._open()["early_core2:solana:MINT1"]
    assert abs(p["remaining"] - 0.90) < 1e-6
    assert "1.3" in p["rungs_fired"]
    # reverses hard toward entry -- without the rung, this would be a near-total loss on $100;
    # with it, 10% of the stake was already banked as real profit before the drop. core2's hard
    # stop fires at mult <= 0.35 (see EARLY_RULES), so drop well under that to trip it cleanly.
    _run(1500, 1020)                       # 0.3x of entry -> hard stop (<=0.35)
    closed = paper_ledger._closed_raw()[-1]    # _closed() filters to STATS_SINCE_TS; this test uses epoch-near timestamps
    assert closed["exit_type"] == "stop_loss"
    # the 10% already sold at 1.3x locked in profit on that slice -- bounds the overall loss
    # tighter than a full-stake stop-loss from entry would (sanity bound, not an exact figure)
    assert closed["pnl_usd"] > -100.0 * 0.75   # well short of a full -$75+ loss on $100


def test_sub_2x_rung_does_not_fire_below_1_3x():
    """Contrast: a coin that never reaches 1.3x should not have the new rung fire at all."""
    _open(usd=100.0)
    _run(6000, 1010)                       # 1.2x -- below the new rung
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


def test_sprint_sub_2x_rung_uses_10pct_fraction():
    """2 Nov 2026: sprint's 1.3x rung trimmed from 15% to 10% (same change as core2/core1) --
    more of a real winner rides past the early rungs."""
    paper_ledger.open_paper("solana", "SMINT", "early_sprint", "early_sprint", 100.0, 5000.0,
                            strategy="early", exit_profile="sprint", now=1000.0)
    paper_ledger.manage(lambda c, t: {"mcap_usd": 6500, "liquidity_usd": 9999}, now=1010,
                        source_prefix="early_")
    p = paper_ledger._open()["early_sprint:solana:SMINT"]
    assert abs(p["remaining"] - 0.90) < 1e-6


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

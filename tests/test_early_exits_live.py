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
    _run(10500, 1010)                      # 2.1x -> sell 20%
    p = paper_ledger._open()["early_core2:solana:MINT1"]
    assert abs(p["remaining"] - 0.80) < 1e-6
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
    _run(10500, 1010)                      # 20% of original at 100% remaining
    assert calls == [("MINT1", 20)]


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

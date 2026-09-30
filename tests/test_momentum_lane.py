"""Sprint momentum lane (layers/layer16) + quick exit profile."""
import time
import pytest

import state
import scheduler
from layers import layer16_momentum_lane as l16
from executor import compound_scalper as cs
from executor import sprint
import executor.paper_ledger as pl

NOW = time.time()


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def pair(liq=80_000, mcap=900_000, age_min=120, m5=6, h1=22, buys=400, sells=220, vol=90_000):
    return {"liquidity": {"usd": liq}, "marketCap": mcap, "pairCreatedAt": (NOW - age_min * 60) * 1000,
            "priceChange": {"m5": m5, "h1": h1}, "txns": {"h1": {"buys": buys, "sells": sells}},
            "volume": {"h1": vol}, "priceUsd": "0.001"}


def test_burst_qualifies():
    ok, score, m, fails = l16.burst(pair(), now=NOW)
    assert ok and not fails and score > 40


@pytest.mark.parametrize("kw", [{"liq": 10_000}, {"m5": 40}, {"m5": -3}, {"h1": 2}, {"buys": 200, "sells": 220},
                                {"age_min": 10}, {"vol": 5_000}, {"liq": 30_000, "mcap": 5_000_000}])
def test_burst_gates(kw):
    assert not l16.burst(pair(**kw), now=NOW)[0]


def test_quick_exit_profile():
    d = cs.scalp_exit_decision(0.91, 1.0, 3, False, "quick")
    assert d.should_exit and d.exit_type == "hard_stop"                      # -9% < -8% stop
    d = cs.scalp_exit_decision(1.13, 1.13, 5, False, "quick")
    assert d.exit_type == "take_profit_partial" and d.pct_to_sell == 0.70
    d = cs.scalp_exit_decision(1.10, 1.18, 8, True, "quick")
    assert d.exit_type == "trail_stop"                                        # 6.8% off peak
    d = cs.scalp_exit_decision(1.02, 1.03, 21, False, "quick")
    assert d.exit_type == "time_stop"
    # default profile unchanged
    assert not cs.scalp_exit_decision(0.91, 1.0, 3, False).should_exit


def test_lane_paper_always_real_only_in_sprint(monkeypatch):
    state.set_value("lane_watch:solana", {"ts": time.time(), "tokens": ["LANE1"]})
    monkeypatch.setattr(scheduler.layer14, "fetch_dexscreener_batch",
                        lambda chain, addrs: {"LANE1": pair()} if chain == "solana" else {})
    called = []
    import executor.entrypoint as ep
    monkeypatch.setattr(ep, "handle_compound_scalper_candidate",
                        lambda *a, **k: called.append((a, k)) or {"fired": True, "position_usd": 90})
    monkeypatch.delenv("SPRINT_MODE", raising=False)
    out = scheduler._run_momentum_lane()
    assert out["qualified"] == 1 and not called
    assert "momentum_lane:solana:LANE1" in pl._open()
    assert pl._open()["momentum_lane:solana:LANE1"]["exit_profile"] == "quick"
    # sprint on: next burst (new token) goes to the pool as a lane entry
    state.set_value("lane_watch:solana", {"ts": time.time(), "tokens": ["LANE2"]})
    monkeypatch.setattr(scheduler.layer14, "fetch_dexscreener_batch",
                        lambda chain, addrs: {"LANE2": pair()} if chain == "solana" else {})
    monkeypatch.setenv("SPRINT_MODE", "true")
    scheduler._run_momentum_lane()
    assert called and called[0][1]["entry_ctx"]["lane"] == "momentum"


def test_lane_entry_through_handler(monkeypatch):
    import executor.entrypoint as ep
    monkeypatch.setenv("SPRINT_MODE", "true")
    monkeypatch.setattr(cs, "SCALPER_CONFIG", cs.CompoundScalperConfig())
    opened = []
    monkeypatch.setattr(ep.compound_scalper, "open_scalp", lambda *a, **k: opened.append((a, k)) or {"ok": True})
    r = ep.handle_compound_scalper_candidate("solana", "LT", None, 900_000, liquidity_usd=400_000, momentum=True,
                                             entry_ctx={"lane": "momentum", "change_m5": 6, "change_h1": 22})
    assert r["fired"] and opened[0][1]["profile"] == "quick"


def test_lane_pauses_after_bad_record(monkeypatch):
    pool = cs._default_pool()
    pool["trades"] = [{"profile": "quick", "exit_type": "hard_stop", "pnl_usd": -3}] * 5 + \
                     [{"profile": "quick", "exit_type": "time_stop", "pnl_usd": 1}]
    cs._save_pool(pool)
    ok, why = sprint.lane_allowed()
    assert not ok and "1/6" in why
    monkeypatch.setenv("SPRINT_MOMENTUM", "false")
    assert not sprint.lane_allowed()[0]

import pytest
import state
from layers import layer15_buyer_quality as q
from executor import entry_guards


def _buys(n, t0=1000, step=10, sol=1.0, prefix="W"):
    return [{"wallet": f"{prefix}{i}", "mint": "M", "block_time": t0 + i * step, "sol_delta": sol + i * 0.137} for i in range(n)]


def test_smart_wallet_needs_two(monkeypatch):
    monkeypatch.delenv("SMART_WALLET_MIN", raising=False)
    b = _buys(5)
    assert not q.smart_wallet_hits(b, ["W1"])["fires"]
    assert q.smart_wallet_hits(b, ["W1", "W3", "X"])["fires"]


def test_velocity_counts_unique_and_acceleration():
    b = _buys(4, step=10) + _buys(8, t0=1160, step=10, prefix="Z")
    v = q.buyer_velocity(b + [b[0]])
    assert v["distinct"] == 12 and v["accelerating"] is True
    assert q.buyer_velocity([])["distinct"] == 0


def test_bot_farm_few_funders():
    b = _buys(8)
    r = q.funding_cluster_report(b, {f"W{i}": "FUNDER" for i in range(8)})
    assert r["verdict"] == "bot_farm"


def test_bot_farm_identical_sizes():
    b = [dict(x, sol_delta=0.5) for x in _buys(8)]
    assert q.funding_cluster_report(b, {})["verdict"] == "bot_farm"


def test_organic_many_funders():
    b = _buys(8)
    r = q.funding_cluster_report(b, {f"W{i}": f"F{i}" for i in range(8)})
    assert r["verdict"] == "organic"


def test_unknown_funders_never_called_farm():
    assert q.funding_cluster_report(_buys(8), {})["verdict"] == "unknown"


def test_concentration(monkeypatch):
    monkeypatch.delenv("GUARD_MAX_DEV_SNIPER_PCT", raising=False)
    assert q.concentration_ok(None, None)[0]
    assert q.concentration_ok(0.1, 0.1)[0]
    assert not q.concentration_ok(0.15, 0.2)[0]


def test_deployer_weight():
    assert q.deployer_weight(None) == 0
    assert q.deployer_weight({"tier": "blacklisted"}) == -100
    assert q.deployer_weight({"tier": "trusted", "wins": 4}) == 25
    assert q.deployer_weight({"tier": "neutral", "wins": 0, "losses": 2}) == -5


def test_assess_demand_verdicts():
    b = _buys(10)
    strong = q.assess_demand(b, ["W1", "W2"], {f"W{i}": f"F{i}" for i in range(10)})
    assert strong["verdict"] in ("strong", "moderate") and strong["score"] >= 60
    farm = q.assess_demand(b, ["W1", "W2"], {f"W{i}": "F" for i in range(10)})
    assert farm["verdict"] == "bot_farm"
    conc = q.assess_demand(b, [], {f"W{i}": f"F{i}" for i in range(10)}, dev_pct=0.3)
    assert conc["verdict"] == "concentrated"


def test_archive_roundtrip_and_dedupe(monkeypatch):
    store = {}
    monkeypatch.setattr(state, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(state, "set_value", lambda k, v: store.__setitem__(k, v) or True)
    b = _buys(6)
    assert q.archive_buys(b) == 6
    assert q.archive_buys(b) == 0
    assert len(q.archived_buys("M")) == 6
    monkeypatch.setattr(q, "MAX_ARCHIVE_MINTS", 2)
    q.archive_buys([{"wallet": "a", "mint": "N1", "block_time": 5000, "sol_delta": 1}])
    q.archive_buys([{"wallet": "a", "mint": "N2", "block_time": 6000, "sol_delta": 1}])
    assert "M" not in store[q.ARCHIVE_KEY]


def test_assess_mint_needs_enough_buyers_and_caches_funders(monkeypatch):
    store = {}
    monkeypatch.setattr(state, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(state, "set_value", lambda k, v: store.__setitem__(k, v) or True)
    assert q.assess_mint("M", []) is None
    q.archive_buys(_buys(10))
    calls = []
    def g(w):
        calls.append(w); return "F" + w
    r = q.assess_mint("M", [], getter=g)
    assert r is not None and len(calls) == 8      # per-call RPC cap
    q.assess_mint("M", [], getter=g)
    assert len(calls) == 10                        # rest fetched, none repeated


def test_guard_blocks_bot_farm_and_dev_sniper(monkeypatch):
    for k in ("GUARD_MAX_DEV_SNIPER_PCT", "REQUIRE_CONFLUENCE"):
        monkeypatch.delenv(k, raising=False)
    base = {"liquidity_usd": 100000, "signals": 2}
    assert entry_guards.check("A", 10, base)[0]
    assert not entry_guards.check("A", 10, dict(base, demand_verdict="bot_farm"))[0]
    ok, why = entry_guards.check("A", 10, dict(base, dev_pct=0.17, sniper_pct=0.14))
    assert not ok and "dev + snipers" in why
    assert entry_guards.check("A", 10, dict(base, dev_pct=0.05, sniper_pct=0.1))[0]


def test_backtest_judge_is_strict():
    import backtest_buyer_signals as bt
    assert bt.judge([], 40)[0] == "INSUFFICIENT DATA"
    rows = [{"verdict": "strong", "pnl_pct": 30 + (i % 5)} for i in range(20)] + \
           [{"verdict": "weak", "pnl_pct": -20} for i in range(25)]
    assert bt.judge(rows, 40)[0] == "SIGNAL SHOWS EDGE"
    rows = [{"verdict": "strong", "pnl_pct": -10 + (i % 3)} for i in range(45)]
    assert bt.judge(rows, 40)[0] == "NO EDGE PROVEN"

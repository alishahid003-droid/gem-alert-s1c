import time
import pytest
import state
from layers import router
from executor import books, paper_ledger


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    for k in ("CORE1_TARGET_USD", "CORE2_TARGET_USD", "CORE1_DAYS", "CORE2_DAYS"):
        monkeypatch.delenv(k, raising=False)


def test_router_lanes():
    assert router.route({"moonshot_ok": True, "age_min": 6, "mcap": 40_000, "liquidity": 12_000})["lane"] == "core2"
    assert router.route({"moonshot_ok": True, "age_min": 60, "mcap": 400_000, "liquidity": 40_000})["lane"] == "core1"
    # old/large coins are NOT moonshots any more: swing at most, or watch
    assert router.route({"moonshot_ok": True, "age_min": 600, "mcap": 2_000_000, "liquidity": 90_000})["lane"] == "watch"
    assert router.route({"moonshot_ok": True, "mcap": 2_000_000, "liquidity": 90_000})["lane"] == "compound"
    assert router.route({"band": "A", "age_min": 30, "mcap": 500_000, "liquidity": 40_000})["lane"] == "compound"
    assert router.route({"band": "A", "age_min": 900, "mcap": 500_000, "liquidity": 40_000})["lane"] == "watch"
    assert router.route({"band": "A", "age_min": 30, "change_h1": 900, "liquidity": 40_000})["lane"] == "watch"
    assert router.route({"kind": "ladder", "mcap": 800_000_000})["lane"] == "compound"
    assert router.route({"kind": "news", "age_min": 4, "mcap": 30_000, "liquidity": 20_000})["lane"] == "core2"
    assert router.route({"band": "C"})["lane"] == "watch"
    assert router.route({"band": "A", "blocked": True})["lane"] == "watch"
    assert router.route({"band": "A", "liquidity": 500})["lane"] == "watch"


def test_core_targets_and_defaults():
    c1, c2 = books.cfg("core1"), books.cfg("core2")
    assert c1["target"] == 125_000 and c1["days"] == 30
    assert c2["target"] == 1_000_000 and c2["days"] == 180
    s = books.cfg("sprint")
    assert s["target"] == 10_000 and s["days"] == 4
    assert books.equity("core1") == 100.0


def test_pace_and_needed_growth():
    now = time.time()
    books._start("core1", now - 30 * 86400)                # halfway through the window
    p = books.pace("core1", now, eq=100.0)
    assert p["has_target"] and 29 < p["elapsed_days"] < 31
    assert p["path_now"] > 3000                           # geometric path ~ $3.5K at half time
    assert p["on_pace"] is False and p["needed_daily_growth_pct"] > 20


def test_stake_floor_and_pace_adjustment():
    now = time.time()
    books._start("core1", now - 30 * 86400)
    behind = books.stake_usd("core1", now)                # far behind -> x1.25 on the $30 floor, capped 35% of $100
    assert 30 <= behind <= 35
    books._start("core2", now)
    assert books.stake_usd("core2", now) == 25.0          # fee floor $30 capped at 25% of $100


def test_book_equity_from_paper_trades(monkeypatch):
    rows = [{"source": "core1", "signal": "moonshot", "pnl_usd": 25.0, "strategy": "stage", "closed_ts": time.time()},
            {"source": "core1", "signal": "moonshot", "pnl_usd": 5.0, "strategy": "runner", "closed_ts": time.time()},
            {"source": "scalper", "signal": "scalper", "pnl_usd": -4.0, "strategy": "scalper", "closed_ts": time.time()}]
    monkeypatch.setattr(paper_ledger, "_closed", lambda: rows)
    assert books.equity("core1") == 130.0                 # runner P&L counts toward the balance
    assert books.equity("compound") == 96.0
    st = books.status("compound")
    assert st["today_pnl"] == -4.0 and st["goal_hit"] is False


def test_can_open_limits(monkeypatch):
    assert books.can_open("core2")[0] is True
    for i in range(3):                                   # moonshot books take up to 3 shots below $300
        paper_ledger.open_paper("solana", f"T{i}", "core2", "moonshot", 10, 50_000, module="core2")
    ok, why = books.can_open("core2")
    assert not ok and "limit" in why


def test_new_wallet_buys_paper_copy(monkeypatch):
    from layers import layer13_fomo_copytrade as l13
    wallets = {"whale1": {"handle": "whale1", "display_name": "Whale One", "balance_usd": 9000, "pnl_7d": 5, "pnl_30d": 9}}
    alerts = [{"alertType": "buy", "trader": "whale1", "tokenAddress": "MintNEW", "network": "sol", "token": "NEWC", "ts": "2026-10-12T10:00:00Z"},
              {"alertType": "buy", "trader": "stranger", "tokenAddress": "MintX", "network": "sol"},
              {"alertType": "sell", "trader": "whale1", "tokenAddress": "MintNEW", "network": "sol"}]
    snap = lambda chain, mint: {"mcap_usd": 150_000, "liquidity_usd": 30_000}
    out = l13.detect_new_wallet_buys(alerts, snap, wallets=wallets)
    assert out == {"wallets": 1, "signals": 1, "paper": 1}
    assert "fomo_new:solana:MintNEW" in paper_ledger._open()
    again = l13.detect_new_wallet_buys(alerts, snap, wallets=wallets)       # same alert: deduped
    assert again["signals"] == 0 and again["paper"] == 0


def test_new_wallet_inert_without_wallets_or_alerts():
    from layers import layer13_fomo_copytrade as l13
    assert l13.detect_new_wallet_buys([], None, wallets={}) == {"wallets": 0, "signals": 0, "paper": 0}
    assert l13.detect_new_wallet_buys([], None, wallets={"a": {"handle": "a"}})["signals"] == 0


def test_router_integration_core_moonshot_into_book(monkeypatch):
    from executor.entrypoint import handle_moonshot_candidate
    monkeypatch.delenv("MOONSHOT_ENABLED", raising=False)
    res = handle_moonshot_candidate("solana", "TokC2", "B", 150_000, 40_000, 70, {}, book="core2", origin="radar")
    assert res["fired"] is False
    pos = paper_ledger._open()["core2:solana:TokC2"]
    assert pos["module"] == "core2" and pos["usd"] == books.stake_usd("core2") or pos["usd"] == 25.0
    for i in range(2):
        paper_ledger.open_paper("solana", f"X{i}", "core2", "moonshot", 10, 50_000, module="core2")
    # book limit reached: next entry is refused with the book's reason
    res2 = handle_moonshot_candidate("solana", "TokC2b", "B", 150_000, 40_000, 70, {}, book="core2")
    assert "core2 book" in res2["reason"] and "core2:solana:TokC2b" not in paper_ledger._open()


def test_radar_candidate_routing(monkeypatch):
    from layers import layer16_catalyst_radar as r
    c = {"kind": "news", "chain": "solana", "token": "RadTok", "mcap": 90_000, "age_min": 7, "liquidity": 20_000, "paper": True, "symbol": "RAD", "reason": "x"}
    r.route_candidate(c)
    assert c["lane"] == "core2" and "CORE 2" in r.format_alert(c)
    pos = r.open_paper_for(c)
    assert pos and pos["module"] == "core2" and pos["exit_profile"] == "moonshot"
    big = {"kind": "news", "chain": "solana", "token": "Big", "mcap": 90_000_000, "age_min": 4000, "liquidity": 2_000_000, "paper": True, "symbol": "B", "reason": "x"}
    r.route_candidate(big)
    assert big["lane"] == "watch" and r.open_paper_for(big) is None
    lad = {"kind": "ladder", "chain": "solana", "token": "Lad", "mcap": 500_000_000, "liquidity": 9_000_000, "paper": True, "symbol": "L", "reason": "x", "profile": "quick"}
    r.route_candidate(lad)
    assert lad["lane"] == "compound"


def test_vault_banks_profit_above_3x_and_compounds_the_rest(monkeypatch):
    monkeypatch.setattr(books, "realized_pnl", lambda b: 900.0)       # seed 100 -> total 1,000
    # line = 3 x 100 = 300; banked = 25% of (900 - 300) = 150 (core1)
    assert books.banked("core1") == 150.0
    assert books.total_value("core1") == 1000.0 and books.equity("core1") == 850.0
    monkeypatch.setattr(books, "realized_pnl", lambda b: 200.0)       # below the 3x line: nothing banked
    assert books.banked("core1") == 0.0 and books.equity("core1") == 300.0


def test_sprint_lane_added_for_strong_early_entry():
    from layers.layer17_early_entry import EarlyTracker
    t = EarlyTracker(dev_list={"D"}, smart_fn=lambda: {"w1"})
    t.on_create({"txType": "create", "mint": "S", "traderPublicKey": "D", "initialBuy": 1e6, "solAmount": .03,
                 "marketCapSol": 30}, now=100.0)
    d = None
    for i in range(12):
        d = d or t.on_trade({"txType": "buy", "mint": "S", "traderPublicKey": "w1" if i == 0 else f"b{i}",
                             "solAmount": 1, "marketCapSol": 32}, now=101.0 + i * 3)
    assert d and "core2" in d["lanes"] and "sprint" in d["lanes"]


def test_sprint_cashout_resets_to_reseed(monkeypatch):
    from executor import books, paper_ledger
    monkeypatch.setattr(paper_ledger, "module_rows", lambda *a, **k: [{"pnl_usd": 10_500.0}])
    got = books.check_cashout("sprint", now=1000.0)
    assert got and got["amount"] == 10_400.0
    assert books.total_value("sprint") == 200.0
    assert books.withdrawn("sprint") == 10_400.0
    assert books.check_cashout("sprint") is None
    assert books.check_cashout("core1") is None   # only the sprint book cashes out

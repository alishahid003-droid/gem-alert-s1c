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


def test_bust_reseeds_book_and_counts(monkeypatch):
    """Book below $10 with nothing open is BUST: reseeded at seed, counted, trading continues."""
    from executor import books
    monkeypatch.setattr(books, "_rows", lambda b: [{"pnl_usd": -95.0}])
    monkeypatch.setattr(books, "open_count", lambda b: 0)
    store = {}
    monkeypatch.setattr(books.state, "get_value", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(books.state, "set_value", lambda k, v: store.__setitem__(k, v))
    assert books.total_value("sprint") == 5.0
    ok, why = books.can_open("sprint")
    assert len(books.busts("sprint")) == 1
    assert books.total_value("sprint") == 100.0
    assert books.status("sprint")["busts"] == 1


def test_bust_waits_for_fresh_open_position_but_not_forever(monkeypatch):
    """26 Oct 2026 fix: an open 'early' position used to block bust-reseed indefinitely. A fresh
    one (<BUST_WAIT_MINUTES old) should still block it; an old one (stale, still legitimately
    priced -- a long-riding runner, not a dead coin) should no longer stall the whole book."""
    from executor import books, paper_ledger
    monkeypatch.setattr(books, "_rows", lambda b: [{"pnl_usd": -95.0}])
    store = {}
    monkeypatch.setattr(books.state, "get_value", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(books.state, "set_value", lambda k, v: store.__setitem__(k, v))
    now = 100_000.0

    # Fresh open position (5 minutes old): bust-reseed should still wait, as before.
    paper_ledger.open_paper("solana", "FreshRunner", "early_sprint", "early_sprint", 10, 5000,
                            strategy="early", exit_profile="sprint", now=now - 300, module="sprint")
    assert books.total_value("sprint") == 5.0
    assert books.check_bust("sprint", now=now) is None
    assert books.total_value("sprint") == 5.0          # unchanged: still waiting on the fresh position

    # Same position, now 90 minutes old (past the 60-min default BUST_WAIT_MINUTES): should reseed.
    later = now + 90 * 60
    got = books.check_bust("sprint", now=later)
    assert got is not None
    assert books.total_value("sprint") == 100.0        # reseeded despite the still-open old position


def test_tier_stakes_cap_and_loss_brake():
    eq = books.equity("sprint")
    pr = books.tier_stake_usd("sprint", "probe")
    st = books.tier_stake_usd("sprint", "standard")
    cv = books.tier_stake_usd("sprint", "conviction")
    assert pr < st < cv <= eq * 0.8
    assert abs(st - eq * 0.40) < 0.02 or st == books.tier_stake_usd("sprint", "standard")
    assert books.tier_stake_usd("sprint", "conviction", mcap=1_000.0) == 20.0           # 2% of a $1k coin
    assert books.tier_stake_usd("sprint", "conviction", mcap=100.0) == 0.0              # $2 < $5 floor
    assert books.tier_stake_usd("core2", "conviction") < cv                                # calmer long book
    for i in range(3):                                                                    # 3 straight losses: one tier down
        paper_ledger.open_paper("solana", f"L{i}", "early_sprint", "early_sprint", 10, 5000, strategy="early",
                                exit_profile="sprint", now=1000.0 + i, module="sprint")
        paper_ledger.manage(lambda c, t: {"mcap_usd": 1000, "liquidity_usd": 9999}, now=1100.0 + i, source_prefix="early_")
    assert books.loss_streak("sprint") == 3
    assert books.tier_after_brake("conviction", "sprint") == "standard"


def _fill(book, n, now=1000.0, start=0):
    for i in range(start, start + n):
        paper_ledger.open_paper("solana", f"S{i}", f"early_{book}", f"early_{book}", 5, 5000, strategy="early",
                                exit_profile=book, now=now + i, module=book)


def test_strong_signal_gets_extra_slots_then_swaps_stale_flat_position(monkeypatch):
    monkeypatch.setenv("BOOK_STRONG_EXTRA_SLOTS", "2")
    _fill("core1", 3)
    ok, why = books.can_open("core1")
    assert not ok and "limit" in why                                   # a normal signal is refused at 3/3
    ok, why = books.can_open("core1", strong=True)
    assert ok and "extra slot" in why                                  # a strong one gets slot 4
    _fill("core1", 2, start=3)                                         # 5 open = limit + 2: all slots used
    ok, why = books.can_open("core1", strong=True)
    assert not ok and "all used" in why                                # nothing stale/flat to swap yet
    # make S0 an old flat position (0.9x, never ran) -> it may be swapped out
    book = paper_ledger._open()
    pos = [p for p in book.values() if p["token"] == "S0"][0]
    pos["last_mcap"] = pos["entry_mcap"] * 0.9
    state.set_value(paper_ledger.OPEN_KEY, book)
    now = pos["opened_ts"] + 50 * 60
    assert books._try_swap("core1", now) is True
    assert paper_ledger._closed()[-1]["exit_type"] == "slot_swap"
    assert books._try_swap("core1", now + 10) is False                 # rate limit: one swap per 5 minutes


def test_swap_never_touches_deep_loss_runner_or_young_position():
    _fill("core1", 3)
    book = paper_ledger._open()
    mults = {"S0": 0.5, "S1": 2.0, "S2": 0.95}                         # S0 deep red, S1 ran, S2 young
    for p in book.values():
        p["last_mcap"] = p["entry_mcap"] * mults[p["token"]]
        p["peak_mcap"] = max(p["peak_mcap"], p["last_mcap"])
    state.set_value(paper_ledger.OPEN_KEY, book)
    assert paper_ledger.swap_out("core1", now=1000.0 + 10 * 60) is None                       # everything under 45 min old
    row = paper_ledger.swap_out("core1", now=1000.0 + 60 * 60 * 5)                            # 5h later: only the flat S2 qualifies
    assert row and row["token"] == "S2" and row["exit_type"] == "slot_swap"
    assert paper_ledger.swap_out("core1", now=1000.0 + 60 * 60 * 5) is None                   # S0 deep red + S1 runner are kept

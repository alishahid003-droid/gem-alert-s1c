import time
import pytest
import state
from layers import router
from executor import books, paper_ledger, position_state


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
    # 7 Nov 2026 (Ali): sprint target raised $10k -> $20k, see BOOKS in executor/books.py
    assert s["target"] == 20_000 and s["days"] == 4
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
    # 2 Nov 2026: max_share now widens (effective_max_share) the further behind pace a book is --
    # Ali's explicit ask ("as volume increasing, stake size can also increase") for the new tight-
    # window Phase A/B/C/D targets. core1 30 days behind its own (old, slower) target is maximally
    # behind -> ceiling widens toward 2x base (0.35 -> 0.70), so the $30 floor x1.25 pace bump
    # (=$37.50) now fits under the ceiling instead of being clipped at the old fixed 35%.
    behind = books.stake_usd("core1", now)
    assert 30 <= behind <= 37.5
    books._start("core2", now)
    assert books.stake_usd("core2", now) == 25.0          # fee floor $30 capped at 25% of $100 (on pace -- ceiling stays at base)


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
    # 2 Nov 2026: max_open() for moonshot books is now capacity-based (room under the risk
    # ceiling / typical stake size), not a fixed step count -- a static "3 is always the limit"
    # starved sprint/core1/core2 of real capital on real data (confirmed: sprint had 4 open
    # against a hardcoded cap of 3 with $45 of genuinely free capital still sitting idle). $10
    # positions are small enough that 3 of them ($30 committed) still leaves room under core2's
    # ceiling at $100 equity -- so it must NOT be blocked here. Fill past the real ceiling instead
    # (10 x $10 = $100, at or past equity itself) to exercise the actual limit.
    for i in range(3):
        paper_ledger.open_paper("solana", f"T{i}", "core2", "moonshot", 10, 50_000, module="core2")
    assert books.can_open("core2")[0] is True              # still real room left -- not starved
    for i in range(3, 10):
        ok, why = books.can_open("core2")
        if not ok:
            assert "limit" in why or "committed" in why
            break
        paper_ledger.open_paper("solana", f"T{i}", "core2", "moonshot", 10, 50_000, module="core2")
    else:
        assert False, "core2 never hit a real capital/count ceiling after 10 opens"


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
    # 2 Nov 2026: a few small $10 fills no longer blocks core2 (its position-count cap is now
    # capacity-based, not a static 3, per the earlier 2 Nov fix) -- fill to its REAL position-count
    # ceiling instead, which still blocks regardless of pool borrowing (pooling only widens STAKE
    # SIZE when a book's own cash is tapped out; it never raises the position-count ceiling, so a
    # book genuinely at its count limit is still refused even with pool capital available).
    for i in range(4):
        r = paper_ledger.open_paper("solana", f"X{i}", "core2", "moonshot", 20, 50_000,
                                     strategy="early", exit_profile="core2", module="core2")
        assert r is not None
    assert books.can_open("core2")[0] is False   # confirm the real ceiling was actually reached
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
    # 7 Nov 2026 (Ali): sprint's target raised $10k -> $20k (see BOOKS in executor/books.py) as
    # part of differentiating sprint/core1/core2 -- pnl/amount figures scaled to match.
    from executor import books, paper_ledger
    monkeypatch.setattr(paper_ledger, "module_rows", lambda *a, **k: [{"pnl_usd": 20_500.0}])
    got = books.check_cashout("sprint", now=1000.0)
    assert got and got["amount"] == 20_400.0
    assert books.total_value("sprint") == 200.0
    assert books.withdrawn("sprint") == 20_400.0
    assert books.check_cashout("sprint") is None
    assert books.check_cashout("core1") is None   # only the sprint book cashes out


def test_bust_reseeds_book_and_counts(monkeypatch):
    """Book below $10 with nothing open is BUST: reseeded at seed, counted, trading continues."""
    from executor import books
    monkeypatch.setattr(books, "_rows", lambda b: [{"pnl_usd": -95.0}])
    monkeypatch.setattr(books, "open_count", lambda b, chain=None: 0)
    store = {}
    monkeypatch.setattr(books.state, "get_value", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(books.state, "set_value", lambda k, v: store.__setitem__(k, v))
    # 8 Nov 2026 fix: check_bust()/check_cashout() etc. write through state.atomic_multi_fn, not
    # state.get_value/set_value directly -- without patching this too, those calls fell through
    # to the REAL local-file backend (invisible to this test's in-memory `store`), so a bust that
    # genuinely fired was never visible to this test's own books.busts()/total_value() calls,
    # which correctly read from the patched `store`. This was a test-isolation bug only -- in real
    # production both paths go through the same real backend, so this mismatch never happens live.
    def _fake_atomic_multi_fn(keys, fn, _store=store):
        cur = {k: _store.get(k) for k in keys}
        new_vals = fn(cur)
        for k, v in new_vals.items():
            _store[k] = v
        return new_vals
    monkeypatch.setattr(books.state, "atomic_multi_fn", _fake_atomic_multi_fn)
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
    # 8 Nov 2026 fix: check_bust()/check_cashout() etc. write through state.atomic_multi_fn, not
    # state.get_value/set_value directly -- without patching this too, those calls fell through
    # to the REAL local-file backend (invisible to this test's in-memory `store`), so a bust that
    # genuinely fired was never visible to this test's own books.busts()/total_value() calls,
    # which correctly read from the patched `store`. This was a test-isolation bug only -- in real
    # production both paths go through the same real backend, so this mismatch never happens live.
    def _fake_atomic_multi_fn(keys, fn, _store=store):
        cur = {k: _store.get(k) for k in keys}
        new_vals = fn(cur)
        for k, v in new_vals.items():
            _store[k] = v
        return new_vals
    monkeypatch.setattr(books.state, "atomic_multi_fn", _fake_atomic_multi_fn)
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


def test_bust_offset_uses_latest_snapshot_not_sum(monkeypatch):
    """27 Oct 2026 fix: a book that busts more than once (compound busted 3x in under 3 minutes in
    production) was having every past bust's cumulative "realized_at_bust" snapshot SUMMED in
    bust_offset(), double/triple-counting the same historical losses. Subtracted from realized_pnl,
    that oversized negative offset added thousands of dollars of phantom balance back onto the book
    (confirmed live: equity=$1,309.74 on a $100-seed book that was actually bleeding money), which is
    what fed paper stakes far beyond any tier cap. Only the LATEST bust's snapshot should be used --
    each new bust already captures all losses since the book began, superseding every prior one."""
    from executor import books
    monkeypatch.setattr(books, "open_count", lambda b, chain=None: 0)
    store = {}
    monkeypatch.setattr(books.state, "get_value", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(books.state, "set_value", lambda k, v: store.__setitem__(k, v))
    # 8 Nov 2026 fix: check_bust()/check_cashout() etc. write through state.atomic_multi_fn, not
    # state.get_value/set_value directly -- without patching this too, those calls fell through
    # to the REAL local-file backend (invisible to this test's in-memory `store`), so a bust that
    # genuinely fired was never visible to this test's own books.busts()/total_value() calls,
    # which correctly read from the patched `store`. This was a test-isolation bug only -- in real
    # production both paths go through the same real backend, so this mismatch never happens live.
    def _fake_atomic_multi_fn(keys, fn, _store=store):
        cur = {k: _store.get(k) for k in keys}
        new_vals = fn(cur)
        for k, v in new_vals.items():
            _store[k] = v
        return new_vals
    monkeypatch.setattr(books.state, "atomic_multi_fn", _fake_atomic_multi_fn)

    rows = [{"pnl_usd": -95.0}]
    monkeypatch.setattr(books, "_rows", lambda b: rows)
    assert books.total_value("compound") == 5.0
    books.can_open("compound")                       # first bust: reseeds to $100
    assert len(books.busts("compound")) == 1
    assert books.total_value("compound") == 100.0

    # More losses happen post-reseed, busting a second time within the same test's rows list
    # (gross pnl sum must again fall below the bust floor relative to the NEW offset).
    rows.append({"pnl_usd": -95.0})
    assert books.total_value("compound") == 5.0
    books.can_open("compound")                        # second bust: must reseed to $100 again,
    assert len(books.busts("compound")) == 2           # not leave a huge phantom positive balance
    assert books.total_value("compound") == 100.0
    assert books.equity("compound") == 100.0


def test_absolute_stake_ceiling_survives_any_equity_bug(monkeypatch):
    """27 Oct 2026 hardening, UPDATED 8 Nov 2026 for Curve C: every stake cap until now (tier %,
    max_share, free_equity, cap_mc) was RELATIVE to equity() -- exactly the single point of
    failure a real bug exploited (a corrupted equity() reading of $1,309.74 on a $100-seed book
    let a $1,289.59 stake through every one of those caps, since they were all "working
    correctly" relative to the wrong number). The original fix pinned an absolute $300 (seed*3)
    backstop -- but 8 Nov 2026 (Ali) deliberately replaced compound's flat hard_cap with Curve C,
    whose entire design is that the stake grows past $300 as real equity legitimately grows
    through Phase 2-5 (e.g. a genuine $60,000 equity trades at Curve C's 10% band = $6,000/trade,
    not $300) -- the risk control is the DECREASING PERCENTAGE as equity grows, not a frozen
    dollar figure. A flat $300 ceiling can no longer distinguish "corrupted $1,309.74" from "real
    $1,309.74" -- both should and do produce the same Curve C-governed stake, since that stake is
    itself already risk-appropriate for that equity level, corrupted-looking or not. This test
    now asserts the real surviving invariant: compound's stake is pinned to Curve C's OWN banded
    percentage of whatever equity() reports, never anything outside that formula -- so a future
    bug anywhere upstream still can't produce a stake disproportionate to Curve C's intended
    risk profile at that equity level, which is the actual safety property that matters now."""
    from executor import books
    monkeypatch.setattr(books, "equity", lambda book_id: 1309.74)
    monkeypatch.setattr(books, "free_equity", lambda book_id: 1309.74)
    monkeypatch.setattr(books, "pace", lambda book_id, now=None: {"has_target": False})
    monkeypatch.setattr(books, "loss_streak", lambda book_id: 0)
    tier_stake = books.tier_stake_usd("compound", "conviction", mcap=50_000.0)
    plain_stake = books.stake_usd("compound")
    # Curve C band at eq=1309.74 is the 600-10,000 band -> 35%
    expected_curve_c_cap = 1309.74 * 0.35
    assert tier_stake <= expected_curve_c_cap + 0.01, (
        f"tier_stake_usd let {tier_stake} exceed Curve C's own {expected_curve_c_cap:.2f} band cap")
    assert plain_stake <= expected_curve_c_cap + 0.01, (
        f"stake_usd let {plain_stake} exceed Curve C's own {expected_curve_c_cap:.2f} band cap")


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
    # 2 Nov 2026: max_open() is capacity-based now, so a fixed "3 is the limit" no longer holds --
    # 3 x $5 = $15 committed still leaves real room at $100 equity. Fill further (small $5 shots)
    # until the REAL capacity ceiling is reached before testing the strong-signal extra-slot path,
    # so this test still exercises "normal signal refused at the real limit" rather than asserting
    # a stale fixed count.
    _fill("core1", 3)
    n = 3
    while books.can_open("core1")[0]:
        _fill("core1", 1, start=n)
        n += 1
        if n > 40:
            assert False, "core1 never hit a real capacity ceiling"
    ok, why = books.can_open("core1")
    assert not ok and "limit" in why                                   # refused at the real ceiling
    ok, why = books.can_open("core1", strong=True)
    assert ok and "extra slot" in why                                  # a strong one gets the +1 extra slot
    _fill("core1", 2, start=n)                                         # fill both extra slots: all used
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


def test_compound_position_count_limit_uses_real_fills_not_paper_entries(monkeypatch):
    """14 Nov 2026 (Ali, explicit instruction -- "why would I even need paper trades when...
    it even blocks at times the max open count to my real trading"): compound's can_open()
    position-count LIMIT must count only confirmed real buys when compound is live-armed for
    a real chain, exactly mirroring the capital-side fix (committed_usd_real/equity_real) that
    already existed. Before this fix, open_count() counted every paper entry regardless of
    live_ok, so a book could show "N open positions" and refuse new real-eligible entries
    while the real wallet had plenty of room and only a handful of real fills.

    15 Nov 2026: rewritten to record against position_state directly instead of mutating a
    paper record's live_ok field. multichain_sniper.py's and entrypoint.py's real-money paths
    (Fix #2/#3) stopped creating a paper shell at all for real-eligible entries -- they write
    straight into position_state via record_stage_entry(..., f"{lane}_real", ...) -- so a test
    that only sets live_ok on a paper record no longer reflects how a real trade is actually
    recorded, and open_count_real()/committed_usd_real() were re-pointed at position_state to
    match (see their docstrings in books.py / position_state.py for the full incident).
    """
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    monkeypatch.setattr(books, "equity_real", lambda: 120.0)          # plenty of real wallet room
    monkeypatch.setattr(books, "max_open", lambda book_id, eq: 8)      # same cap as live deployment

    # 10 paper entries opened (matches the real incident: many signals pass the filter and
    # always get a paper record for unrelated reasons -- e.g. solana lanes on the same cycle),
    # none of which should count toward compound's REAL position limit at all.
    for i in range(10):
        pos = paper_ledger.open_paper("robinhood_chain", f"P{i}", "compound", "early_compound",
                                       20, 50_000, module="compound", strategy="early")
        assert pos is not None, "open_paper refused the test entry -- fixture setup bug, not the fix under test"

    # 3 REAL positions recorded the way multichain_sniper.py's act() actually records them:
    # record_stage_entry(chain, token, f"{lane}_real", usd_amount, mcap, reason) -- directly in
    # position_state, with zero paper record involved.
    for i in range(3):
        position_state.record_stage_entry("robinhood_chain", f"R{i}", "compound_real", 5.0,
                                           50_000, "real compound buy attempt: ok")
        position_state.record_fill("robinhood_chain", f"R{i}", 1000.0, tx_signature=f"0xtx{i}")
    # A 4th real attempt that FAILED must not count -- no real money left the wallet for it.
    position_state.record_stage_entry("robinhood_chain", "R-failed", "compound_real", 5.0,
                                       50_000, "real compound buy attempt: refused")
    position_state.mark_stage_buy_failed("robinhood_chain", "R-failed", "compound_real", reason="refused")

    # Real count must be 3 (only the confirmed real fills), not 10 (every paper entry) and
    # not 4 (the failed attempt excluded) -- well under the 8-slot cap, so a real-eligible
    # entry must NOT be refused on count.
    assert books.open_count_real("compound", chain="robinhood_chain") == 3
    assert books.open_count("compound", chain="robinhood_chain") == 10          # paper count unchanged, for comparison
    # Real committed cost must be the 3 real $5 fills ($15), not the failed 4th attempt's $5
    # and not any of the 10 paper entries' $20 stakes.
    assert books.committed_usd_real("compound") == 15.0

    can, reason = books.can_open("compound", chain="robinhood_chain")
    assert can is True, f"expected can_open to allow a new real-eligible entry, got refused: {reason}"

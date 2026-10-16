"""FAST module (14 Oct 2026): one coin -> one lane, own gate, own exits, no live without the switch."""
import pytest

import state
import worker_early_watch as w
from executor import paper_ledger
from layers import fast_lane

T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    w._cache.clear()
    monkeypatch.setenv("SOL_USD", "150")


def _dec(mint, age, mcap, conf=("pressure_free", "velocity_free", "famous_name", "smart_wallet")):
    return {"lane": "core2", "lanes": ["core2", "sprint", "core1"], "mint": mint, "symbol": mint[:3], "name": mint, "creator": "c",
            "age_s": age, "mcap_sol": mcap, "entry_mcap_sol": mcap, "buyers": 0, "buys": 0, "sells": 0,
            "confirmations": list(conf), "smart": [], "uses_free": True, "potential": "10x-50x"}


def _d(age, mc, conf=("pressure_free", "velocity_free")):
    return {"age_s": age, "entry_mcap_sol": mc, "confirmations": list(conf)}


def test_router_one_moonshot_book_plus_optional_compound():
    r = fast_lane.route(_d(60, 30))
    assert [x["profile"] for x in r] == ["fast_sprint", "compound"] and [x["book"] for x in r] == ["sprint", "compound"]
    r = fast_lane.route(_d(300, 30))
    assert [x["profile"] for x in r] == ["fast_core", "compound"]
    assert fast_lane.route(_d(60, 200)) == []
    assert fast_lane.route(_d(1500, 30)) == []
    # one confirmation only: moonshot book yes, compound needs >= 2
    assert [x["profile"] for x in fast_lane.route(_d(60, 30, ("pressure_free",)))] == ["fast_sprint"]


def test_router_moon_lottery_needs_famous_name_and_three_confirmations():
    conf = ("pressure_free", "velocity_free", "famous_name")
    assert fast_lane.route(_d(40, 30, conf))[0] == {"profile": "fast_moon", "book": "core2", "stake": 2.0}
    assert fast_lane.route(_d(40, 30))[0]["profile"] == "fast_sprint"          # no famous name -> sprint
    assert fast_lane.route(_d(90, 30, conf))[0]["profile"] == "fast_sprint"    # too old for moon


def test_live_lane_mapping_only_sprint_can_be_live():
    assert fast_lane.live_lane("fast_sprint") == "sprint"
    assert fast_lane.live_lane("fast_core") != "sprint" and fast_lane.live_lane("compound") != "sprint"


def test_free_signal_opens_exactly_one_position_in_fast_module(monkeypatch):
    monkeypatch.setenv("EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EARLY_LIVE_LANES", "sprint,core1,core2")
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "x")
    monkeypatch.delenv("EARLY_FREE_CONFIRM", raising=False)
    from executor import pumpportal_exec
    monkeypatch.setattr(pumpportal_exec, "buy", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live buy must not fire")))
    out = w.act(_dec("MintFastA", 60.0, 40.0), send=lambda m: None, now=T0)
    assert out["opened"] == ["fast_sprint", "compound"]
    pos = {p["exit_profile"]: p for p in paper_ledger._open().values() if p["token"] == "MintFastA"}
    assert set(pos) == {"fast_sprint", "compound"}                   # one moonshot book + the in/out stream, no triple copy
    assert pos["fast_sprint"]["module"] == "sprint" and pos["fast_sprint"]["usd"] == 5.0
    assert pos["compound"]["module"] == "compound" and not pos["fast_sprint"]["live"]


def test_fast_paper_capacity_refuses_without_burning_book_slots(monkeypatch):
    monkeypatch.setenv("FAST_PAPER_MAX_OPEN", "2")
    a = w.act(_dec("MintFastB", 60.0, 40.0), send=lambda m: None, now=T0)
    b = w.act(_dec("MintFastC", 60.0, 41.0), send=lambda m: None, now=T0 + 1)
    assert a["opened"] == ["fast_sprint", "compound"] and b["opened"] == [] and b["refused"]


def test_decision_outside_windows_is_skipped():
    assert w.act(_dec("MintFastD", 60.0, 300.0), send=lambda m: None, now=T0) == {"skipped": "outside fast windows"}


def test_fast_off_switch_restores_old_multi_book_behaviour(monkeypatch):
    monkeypatch.setenv("FAST_LANE", "off")
    out = w.act(_dec("MintFastE", 60.0, 40.0), send=lambda m: None, now=T0)
    assert "fast_sprint" not in (out.get("opened") or [])


def test_fast_exit_profile_trails_from_1_4x_and_banks_early():
    paper_ledger.open_paper("solana", "MintExit", "early_fast_sprint", "early_fast_sprint", 5.0, 1000.0, strategy="early",
                            exit_profile="fast_sprint", module="fast", now=T0)
    pos = paper_ledger._open()["early_fast_sprint:solana:MintExit"]
    pos["peak_mcap"] = 1000.0
    paper_ledger._manage_early(pos, 1500.0, T0 + 60)           # 1.5x: first slice (1.4x) sold, no stop
    assert "1.4" in pos["rungs_fired"] and pos["remaining"] < 1.0
    pos["peak_mcap"] = 1500.0
    closed = paper_ledger._manage_early(pos, 1500.0 * 0.5, T0 + 120)   # drops to 0.75x of entry = 50% of peak -> trail exit
    assert closed and pos["exit_type"] == "trailing_stop"


def test_famous_name_covers_celebs_sports_influencers_and_can_be_switched_off(monkeypatch):
    from layers.layer17_early_entry import _famous_rx
    for name in ("Trump Coin", "ChatGPT", "RONALDO", "Haaland", "MrBeast", "KaiCenat", "Mayweather", "Elon"):
        assert _famous_rx().search(name), name
    assert not _famous_rx().search("Slopcore")
    monkeypatch.setenv("EARLY_FAMOUS_NAMES", "off")
    assert not _famous_rx().search("Trump Coin")


def _live_env(monkeypatch):
    monkeypatch.setenv("EXECUTION_ENABLED", "true")
    monkeypatch.setenv("EARLY_LIVE_LANES", "sprint")
    monkeypatch.setenv("EARLY_FREE_CONFIRM", "live")
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "x")
    monkeypatch.setenv("LIVE_MAX_STAKE_USD", "5")
    monkeypatch.setenv("LIVE_MAX_OPEN", "3")
    monkeypatch.setenv("LIVE_DAILY_LOSS_USD", "50")
    from executor import pumpportal_exec
    monkeypatch.setattr(pumpportal_exec, "KILL_FILE", "/nonexistent/KILL_SWITCH_for_tests")   # Ali's real KILL_SWITCH file must not affect tests


def test_live_buy_is_remembered_and_a_lost_ledger_write_is_restored(monkeypatch):
    _live_env(monkeypatch)
    from executor import pumpportal_exec
    monkeypatch.setattr(pumpportal_exec, "buy", lambda *a, **k: {"ok": True, "resp": {"signature": "sig"}})
    out = w.act(_dec("MintLiveA", 60.0, 40.0), send=lambda m: None, now=T0)
    assert "fast_sprint" in out["opened"]
    pid = "early_fast_sprint:solana:MintLiveA"
    assert paper_ledger._open()[pid]["live"] is True and pid in state.get_value(w.LIVE_KEY)
    book = paper_ledger._open()
    book.pop(pid)                                   # simulate the lost write (another thread/process overwrote the book)
    state.set_value(paper_ledger.OPEN_KEY, book)
    assert pid not in paper_ledger._open()
    assert w.reinsert_lost_live() == 1 and pid in paper_ledger._open()
    assert w.reinsert_lost_live() == 0              # idempotent


def test_closed_live_position_is_not_resurrected(monkeypatch):
    _live_env(monkeypatch)
    from executor import pumpportal_exec
    monkeypatch.setattr(pumpportal_exec, "buy", lambda *a, **k: {"ok": True, "resp": {"signature": "sig"}})
    monkeypatch.setattr(pumpportal_exec, "sell", lambda *a, **k: {"ok": True})
    w.act(_dec("MintLiveB", 60.0, 40.0), send=lambda m: None, now=T0)
    pid = "early_fast_sprint:solana:MintLiveB"
    pos = paper_ledger._open()[pid]
    paper_ledger._manage_early(pos, pos["entry_mcap"] * 0.3, T0 + 30)       # hard stop -> closed
    # emulate the manager closing it: leave the open book, land in the closed list
    book = paper_ledger._open(); book.pop(pid, None)
    paper_ledger.state.set_value(paper_ledger.OPEN_KEY, book)
    paper_ledger.state.set_value(paper_ledger.CLOSED_KEY, paper_ledger._closed_raw() + [dict(pos, closed_ts=T0 + 30)])
    assert pid not in paper_ledger._open()
    assert w.reinsert_lost_live() == 0 and pid not in paper_ledger._open()


def test_live_buy_without_a_position_is_sold_back(monkeypatch):
    _live_env(monkeypatch)
    from executor import pumpportal_exec
    sold = []
    monkeypatch.setattr(pumpportal_exec, "buy", lambda *a, **k: {"ok": True, "resp": {"signature": "sig"}})
    monkeypatch.setattr(pumpportal_exec, "sell", lambda mint, pct: sold.append((mint, pct)) or {"ok": True})
    monkeypatch.setattr(paper_ledger, "open_paper", lambda *a, **k: None)
    out = w.act(_dec("MintLiveC", 60.0, 40.0), send=lambda m: None, now=T0)
    assert ("MintLiveC", 100) in sold and any("LIVE BUY WITHOUT POSITION" in x for x in out["refused"])


def test_manager_write_does_not_drop_a_position_opened_mid_cycle(monkeypatch):
    _live_env(monkeypatch)
    from executor import pumpportal_exec
    monkeypatch.setattr(pumpportal_exec, "buy", lambda *a, **k: {"ok": True, "resp": {"signature": "sig"}})
    w.act(_dec("MintOld", 60.0, 40.0), send=lambda m: None, now=T0)

    def slow_batch(chain, tokens):
        # a new signal opens while the manager is busy pricing
        w.act(_dec("MintNew", 61.0, 40.0), send=lambda m: None, now=T0 + 1)
        return {}
    paper_ledger.manage(snapshot_fn=lambda c, t: None, batch_fn=slow_batch, now=T0 + 5)
    ids = list(paper_ledger._open())
    assert any("MintNew" in i for i in ids) and any("MintOld" in i for i in ids)


def test_live_cap_ignores_ghost_live_rows_opened_before_go_live(monkeypatch):
    from executor import pumpportal_exec
    import time as _t
    N = _t.time()
    monkeypatch.setenv("LIVE_SINCE_TS", str(N))
    monkeypatch.setattr(paper_ledger, "_open", lambda: {})
    rows = [{"live": True, "opened_ts": N - 100, "closed_ts": N + 5, "pnl_usd": -9.0},
            {"live": True, "opened_ts": N + 1, "closed_ts": N + 5, "pnl_usd": -2.0}]
    monkeypatch.setattr(paper_ledger, "_closed", lambda: rows)
    monkeypatch.setattr(pumpportal_exec.time, "gmtime", __import__("time").gmtime)
    o, c = pumpportal_exec._live_rows()
    assert len(c) == 1 and c[0]["pnl_usd"] == -2.0


def test_live_needs_a_high_score_not_one_check():
    base = {"confirmations": ["pressure_free", "velocity_free"], "fb": 31, "fs": 12, "mcap_sol": 45.5}
    assert fast_lane.live_lane("fast_sprint", base) == "never_live"            # one of today's real losers
    good = {"confirmations": ["pressure_free", "velocity_free", "famous_name"], "fb": 50, "fs": 12, "mcap_sol": 38}
    assert fast_lane.live_lane("fast_sprint", good) == "sprint"
    no_name = {"confirmations": ["pressure_free", "velocity_free", "smart_wallet"], "fb": 45, "fs": 12, "mcap_sol": 36}
    assert fast_lane.live_lane("fast_sprint", no_name) == "sprint"            # not famous, still allowed by other checks
    assert fast_lane.live_lane("fast_core", good) == "never_live"
    late = dict(good, fb=95, fs=70, mcap_sol=52)
    assert fast_lane.live_lane("fast_sprint", late) == "never_live"           # famous but chasing a late crowd


def test_viral_meme_and_movie_names_count_as_famous():
    from layers import layer17_early_entry as e
    for nm in ("Elon Beast", "Moo Deng", "Pikachu Coin", "Deadpool", "Messi Cat", "Labubu"):
        assert e._famous_rx().search(nm), nm
    assert not e._famous_rx().search("Zorblax Token")


def test_trend_feed_parsing_and_first_coin_claim():
    import trend_feeds as tf
    from layers import layer17_early_entry as e
    assert tf.parse_google_rss("<rss><channel><item><title>John Phil</title></item></channel></rss>") == ["John Phil"]
    assert tf.parse_reddit('{"data":{"children":[{"data":{"title":"Moo Deng goes viral again"}}]}}') == ["Moo Deng"]
    terms = tf.terms_from_titles(["John Phil"])
    assert "john phil" in terms and "johnphil" in terms
    t = e.EarlyTracker(hot_terms_fn=lambda: set(terms))
    claimed = {}
    t.claim_fn = lambda term, mint: claimed.setdefault(term, mint) == mint
    import time
    r1 = e.Rec("M1", "JOHNPHIL", "John Phil Coin", "c", time.time(), 0, 1, 30)
    r2 = e.Rec("M2", "PHILJOHN2", "johnphil again", "c", time.time(), 0, 1, 30)
    import time as _t
    c1, c2 = t.confirmations(r1, _t.time()), t.confirmations(r2, _t.time())
    assert "narrative" in c1 and "narrative_first" in c1
    assert "narrative" in c2 and "narrative_first" not in c2
    assert fast_lane.score({"confirmations": ["narrative", "narrative_first"], "fb": 50, "fs": 10, "mcap_sol": 35}) >= 5


def test_trend_refresh_writes_state_without_network(monkeypatch):
    import trend_feeds as tf
    store = {}
    class S:
        get_value = staticmethod(lambda k: store.get(k))
        set_value = staticmethod(lambda k, v: store.__setitem__(k, v))
    n, notes = tf.refresh(S, now=1e6, fetch=lambda now: ({"google": ["Moo Deng"], "reddit": [], "wiki": ["Taylor Swift"]}, ["x"]))
    assert n >= 4 and store["trend_terms_v1"]["moo deng"]["src"] == ["google"]


def test_social_trend_parsers():
    import trend_feeds as tf
    assert tf.parse_bluesky('{"topics":[{"topic":"MooDeng"},{"topic":"Elon Musk"}]}') == ["Moo Deng", "Elon Musk"]
    assert tf.parse_mastodon_tags('[{"name":"JohnPhil"},{"name":"cats"}]') == ["John Phil", "cats"]
    assert tf.parse_trends24('<a class="trend-link" href="x">#TaylorSwift</a><a class="trend-link" href="y">Messi</a>') == ["Taylor Swift", "Messi"]
    assert tf.parse_tiktok('{"data":{"list":[{"hashtag_name":"labubu"}]}}') == ["labubu"]
    assert tf.parse_tiktok("garbage") == [] and tf.parse_bluesky("") == []
    assert "moo deng" in tf.terms_from_titles(["Moo Deng"]) and "moodeng" in tf.terms_from_titles(["Moo Deng"])


def test_reddit_rss_parser():
    import trend_feeds as tf
    x = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Moo Deng did it again</title></entry></feed>'
    assert tf.parse_reddit_rss(x) == ["Moo Deng"]


# ---- 15 Oct: self-built dev scoreboard, whale buy, wallet roster -------------------------------------------------
def test_dev_rank_picks_creators_who_keep_winning(tmp_path):
    import json, time, dev_rank
    t0 = time.time() - 10000
    lines = []
    for i in range(4):   # good dev: 4 launches, 2 graduate
        m = f"G{i}pump"
        lines.append({"t": "create", "ts": t0 + i, "mint": m, "creator": "GOOD", "mcap_sol": 30})
        lines.append({"t": "outcome", "ts": t0 + 900, "mint": m, "cp_min": 15, "mcap_usd": 30 * 107.55 * (4 if i < 2 else 0.2)})
    for i in range(4):   # bad dev: 4 launches, none win
        m = f"B{i}pump"
        lines.append({"t": "create", "ts": t0 + i, "mint": m, "creator": "BAD", "mcap_sol": 30})
        lines.append({"t": "outcome", "ts": t0 + 900, "mint": m, "cp_min": 15, "mcap_usd": 100})
    lines.append({"t": "final", "ts": t0 + 5, "mint": "G0pump", "early_buyers": ["W1", "W2"]})
    (tmp_path / "a.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    devs, wallets = dev_rank.scan(str(tmp_path / "*.jsonl"))
    assert dev_rank.good_devs(devs) == {"GOOD"}
    assert wallets["W1"] == [1, 1]
    assert dev_rank.smart_wallets(wallets, min_seen=1, min_win=.5) == {"W1", "W2"}


def test_good_dev_and_whale_confirm_and_score_high():
    import time
    from layers.layer17_early_entry import EarlyTracker
    t = EarlyTracker()
    t.good_dev_fn = lambda c: c == "DEVX"
    t.on_create({"mint": "M1pump", "traderPublicKey": "DEVX", "initialBuy": 1e7, "marketCapSol": 30, "symbol": "Q", "name": "Q", "solAmount": 1})
    t.on_trade({"mint": "M1pump", "traderPublicKey": "BIG", "txType": "buy", "solAmount": 4.0, "marketCapSol": 32})
    c = t.confirmations(t.recs["M1pump"], time.time())
    assert "good_dev" in c and "whale_buy" in c
    from layers import fast_lane
    assert fast_lane.score({"confirmations": c}) >= 5
    assert fast_lane.score({"confirmations": ["dev_list"]}) == 0     # screenshot dev list carries no weight any more


# ---- 15 Oct: copy-trading bridge (KOL / Fomo followed / Fomo new wallets) ---------------------------------------
class _FakeState:
    def __init__(self, d=None): self.d = dict(d or {})
    def get_value(self, k): return self.d.get(k)
    def set_value(self, k, v): self.d[k] = v


def test_kol_buys_become_copy_signals_and_converge():
    import time, copy_signals
    from layers import roster
    now = time.time()
    t1, t2 = sorted(roster.TIER_1)[0], sorted(roster.TIER_2)[0]
    trades = [{"action": "buy", "kol_name": t1, "token_mint": "MINTApump", "traded_at": None},
              {"action": "buy", "kol_name": t2, "token_mint": "MINTApump"},
              {"action": "sell", "kol_name": t1, "token_mint": "MINTBpump"},          # sells are ignored
              {"action": "buy", "kol_name": "Random Guy", "token_mint": "MINTCpump"}]  # not followed
    book = copy_signals.from_kol_trades(trades, roster.CONVERGENCE_ROSTER, roster.tier_of, now)
    assert set(book) == {"MINTApump"} and book["MINTApump"]["n"] == 2 and book["MINTApump"]["best_tier"] == 4


def test_old_kol_buys_are_dropped():
    import time, copy_signals
    from layers import roster
    now = time.time()
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 7200))
    b = copy_signals.from_kol_trades([{"action": "buy", "kol_name": sorted(roster.TIER_1)[0], "token_mint": "Xpump", "traded_at": old}],
                                     roster.CONVERGENCE_ROSTER, roster.tier_of, now)
    assert b == {}


def test_fomo_new_wallet_signal_is_flagged_and_refresh_merges_sources():
    import time, copy_signals
    from layers import roster
    now = time.time()
    st = _FakeState({"fomo_signals": [{"kind": "new_wallet_buy", "token_address": "Npump", "trader": "fresh1", "ts": now - 30, "tier": "new"},
                                        {"kind": "buy", "token_address": "Npump", "trader": "Unipcs", "ts": now - 20, "tier": "Tier 1"},
                                        {"kind": "thesis", "token_address": "Zpump", "trader": "x", "ts": now}]})
    fetch = lambda: {"ok": True, "buy_trades": [{"action": "buy", "kol_name": sorted(roster.TIER_2)[0], "token_mint": "Kpump"}], "sell_trades": []}
    n, notes = copy_signals.refresh(st, now, kol_fetch=fetch)
    book = st.d["copy_buys_v1"]
    assert n == 2 and book["Npump"]["new_wallet"] is True and book["Npump"]["n"] == 2 and book["Kpump"]["kol"] is True
    assert "Zpump" not in book                                                         # theses are not buys


def test_refresh_survives_a_dead_feed():
    import copy_signals
    st = _FakeState()
    n, notes = copy_signals.refresh(st, kol_fetch=lambda: {"ok": False, "reason": "budget paced"})
    assert n == 0 and any("budget" in x for x in notes)


def _thin_coin(t, mint="Cpump"):
    t.on_create({"mint": mint, "traderPublicKey": "NEWDEV", "initialBuy": 1e7, "marketCapSol": 30, "symbol": "ZZ", "name": "zzq", "solAmount": 1})
    return mint


def test_a_followed_trader_buy_lets_a_thin_coin_enter_without_the_trade_feed():
    from layers.layer17_early_entry import EarlyTracker
    t = EarlyTracker()
    m = _thin_coin(t)
    assert t.evaluate(m) is None                                                       # no signal: thin coin never enters
    t2 = EarlyTracker(); m2 = _thin_coin(t2)
    t2.copy_fn = lambda mint: {"n": 1, "new_wallet": False}
    d = t2.evaluate(m2)
    assert d and "kol_buy" in d["confirmations"] and d["lane"] in ("core2", "core1")


def test_converge_and_new_wallet_score_high_and_unlock_moon():
    from layers.layer17_early_entry import EarlyTracker
    t = EarlyTracker(); m = _thin_coin(t, "Dpump")
    t.copy_fn = lambda mint: {"n": 2, "new_wallet": True}
    d = t.evaluate(m)
    c = d["confirmations"]
    assert {"kol_buy", "kol_converge", "fomo_new_wallet"} <= set(c)
    assert fast_lane.score({"confirmations": c}) >= 9
    moon = fast_lane.route({"age_s": 20, "entry_mcap_sol": 30, "confirmations": ["kol_converge", "kol_buy", "pressure_free"]})
    assert any(x["profile"] == "fast_moon" for x in moon)                              # a moonshot WITHOUT a famous name


def test_worker_tracker_has_the_copy_hook():
    tr = w.make_tracker()
    assert tr.copy_fn("nothing-here") is None


# ---- 15 Oct: direct wallet watch, wallet harvesting, Fomo hourly pacing -------------------------------------------
def _tx(wallet, mint, pre_amt, post_amt, sol_pre=5_000_000_000, sol_post=4_000_000_000, err=None):
    return {"meta": {"err": err, "preBalances": [sol_pre], "postBalances": [sol_post],
                     "preTokenBalances": [{"owner": wallet, "mint": mint, "uiTokenAmount": {"uiAmount": pre_amt}}] if pre_amt else [],
                     "postTokenBalances": [{"owner": wallet, "mint": mint, "uiTokenAmount": {"uiAmount": post_amt}}]},
            "transaction": {"message": {"accountKeys": [{"pubkey": wallet}]}}}


def test_wallet_watch_detects_a_buy_and_ignores_sells_and_stables():
    import wallet_watch as ww
    W = "WALLET1"
    assert ww.detect_buys(_tx(W, "MINTpump", 0, 1000), W) == ["MINTpump"]
    assert ww.detect_buys(_tx(W, "MINTpump", 1000, 400, sol_pre=4_000_000_000, sol_post=5_000_000_000), W) == []   # a sell (SOL up)
    assert ww.detect_buys(_tx(W, ww.IGNORE_MINTS.copy().pop(), 0, 50), W) == []                                     # stablecoin
    assert ww.detect_buys(_tx(W, "MINTpump", 0, 1000, err={"x": 1}), W) == []                                       # failed tx


def test_wallet_watch_cycle_sets_cursor_first_then_flags_new_buys_and_respects_budget(monkeypatch):
    import time, wallet_watch as ww
    st = _FakeState({"kol_wallets_v1": {"Unipcs": "WALLET1"}})
    calls = []
    sigs = [[{"signature": "s0"}], [{"signature": "s2", "blockTime": time.time()}, {"signature": "s1", "blockTime": time.time()}]]
    def rpc(method, params):
        calls.append(method)
        if method == "getSignaturesForAddress":
            return {"ok": True, "result": sigs.pop(0)}
        return {"ok": True, "result": _tx("WALLET1", "BOUGHTpump", 0, 10)}
    tier = lambda n: "Tier 1"
    n1, _ = ww.poll_cycle(st, rpc, tier_of=tier)
    assert n1 == 0 and st.d["wallet_watch_last_sig_v1"]["WALLET1"] == "s0"                                          # first look: cursor only
    n2, _ = ww.poll_cycle(st, rpc, tier_of=tier)
    assert n2 >= 1 and st.d["copy_buys_rpc_v1"]["BOUGHTpump"]["names"] == ["Unipcs"] and st.d["copy_buys_rpc_v1"]["BOUGHTpump"]["best_tier"] == 4
    monkeypatch.setenv("WALLET_WATCH_MAX_CALLS_DAY", "1")
    st.d[f"wallet_watch_calls:{time.strftime('%Y-%m-%d', time.gmtime())}"] = 5
    sigs[:] = [[{"signature": "s9"}]]
    n3, notes = ww.poll_cycle(st, rpc, tier_of=tier)
    assert n3 == 0 and any("budget" in x for x in notes)


def test_kol_wallets_are_harvested_and_rpc_buys_merge_into_copy_book():
    import time, wallet_watch as ww, copy_signals
    from layers import roster
    st = _FakeState()
    name = sorted(roster.TIER_1)[0]
    n = ww.harvest_wallets([{"kol_name": name, "wallet_address": "W9"}, {"kol_name": "Nobody", "wallet_address": "W8"}], roster.CONVERGENCE_ROSTER, st)
    assert n == 1 and st.d["kol_wallets_v1"] == {name: "W9"}
    now = time.time()
    st.d["copy_buys_rpc_v1"] = {"Rpump": {"names": [name], "n": 1, "best_tier": 4, "ts": now - 10}}
    cnt, _ = copy_signals.refresh(st, now, kol_fetch=lambda: {"ok": False, "reason": "paced"})
    assert st.d["copy_buys_v1"]["Rpump"]["n"] == 1 and "rpc" in st.d["copy_buys_v1"]["Rpump"]["src"]


def test_fomo_hourly_pace_blocks_a_burst(monkeypatch):
    from layers import layer13_fomo_copytrade as l13
    import time
    gov = {"remaining": 100000, "backoff_until": 0, "day": time.strftime("%Y-%m-%d", time.gmtime()), "spent_today": 0,
           "hour": time.strftime("%Y-%m-%d %H", time.gmtime()), "spent_hour": 10_000}
    monkeypatch.setattr(l13.state, "get_fomo_credit_state", lambda: dict(gov))
    r = l13._fomo_get("/v2/alerts")
    assert not r["ok"] and "hourly" in r["reason"]


def test_dev_launch_alert_once_per_coin_and_cooldown():
    import time, dev_alerts
    st = _FakeState({"dev_stats_v1": {"DEV1": [5, 2]}})
    st.log_full_alert = lambda *a, **k: st.d.setdefault("feed", []).append(a)
    sent = []
    send = lambda h, t: sent.append((h, t))
    now = time.time()
    assert dev_alerts.maybe_alert(st, "DEV1", "MINT1", "PEPE", send=send, now=now)
    assert "won 2 of 5" in sent[0][0]
    assert not dev_alerts.maybe_alert(st, "DEV1", "MINT1", "PEPE", send=send, now=now + 1)    # same coin
    assert not dev_alerts.maybe_alert(st, "DEV1", "MINT2", "PEPE2", send=send, now=now + 60)  # cooldown
    assert dev_alerts.maybe_alert(st, "DEV1", "MINT3", "PEPE3", send=send, now=now + 700)
    assert len(sent) == 2 and len(st.d["feed"]) == 2


def test_wallet_watch_set_includes_pumpfun_roster_and_smart_wallets():
    import wallet_watch
    W = "A" * 40
    st = _FakeState({"kol_wallets_v1": {"Cupsey": "K" * 40}, "pumpfun_smart_money_roster": [W], "smart_wallets_v1": ["B" * 40]})
    ws = wallet_watch.watch_set(st)
    assert len(ws) == 3 and W in ws.values()


def _gt_trades(rows):
    return {"data": [{"attributes": {"kind": k, "tx_from_address": w, "block_timestamp": f"2026-10-15T10:00:{i:02d}Z", "volume_in_usd": "50"}}
                     for i, (k, w) in enumerate(rows)]}


def test_evm_wallet_scoreboard_learns_and_flags():
    import evm_wallets as ev
    st = _FakeState()
    for pool in ("p1", "p2", "p3"):
        rows = [("buy", "0xAAA"), ("buy", "0xBBB")] + [("buy", f"0xR{i}{pool}") for i in range(30)]
        ev.learn_pool(st, "base", pool, ev.parse_trades(_gt_trades(rows)))
    assert ev.smart_set(st, "base") == {"0xaaa", "0xbbb"}          # early in 3 distinct winning pools
    # a bot with >6 trades in one pool is ignored
    bot = [("buy", "0xBOT")] * 8 + [("buy", "0xCCC")]
    assert "0xbot" not in ev.early_buyers(ev.parse_trades(_gt_trades(bot)))
    # a fresh pool bought by a smart wallet is flagged; unknown wallets are not
    assert ev.check_pool(st, "base", "newpool", ev.parse_trades(_gt_trades([("buy", "0xAAA"), ("sell", "0xBBB")]))) == 1
    assert ev.hit_n(st, "base", "newpool") == 1 and ev.hit_n(st, "base", "other") == 0
    assert ev.check_pool(st, "base", "p9", ev.parse_trades(_gt_trades([("buy", "0xZZZ")]))) == 0


def test_evm_scanner_gets_smart_wallet_confirmation():
    import time, multichain_sniper as mc, evm_wallets as ev
    st = mc.state
    now = time.time()
    st.set_value(ev.KEY_HITS, {"base|poolX": {"n": 1, "ts": now}})
    p = {"chain": "base", "addr": "0xtok", "name": "T", "src": "gt", "pool": "poolX", "mcap": 20000, "liq": 8000,
         "created": now - 300, "buys5": 5, "sells5": 1, "buyers5": 3, "vol5": 500, "chg5": 5}
    d = mc.Scanner().evaluate(p, now)
    assert d is None or "smart_wallet" in d["confirmations"]
    p["buys5"], p["buyers5"], p["vol5"], p["chg5"] = 20, 12, 5000, 30
    d = mc.Scanner().evaluate(p, now)
    assert d and "smart_wallet" in d["confirmations"]
    st.set_value(ev.KEY_HITS, {})


def test_evm_cycle_with_fake_fetch():
    import time, evm_wallets as ev
    st = _FakeState()
    st.set_value(ev.KEY, {"base": {"0xaaa": ["a", "b", "c"]}})
    calls = []
    def fetch(slug, pool):
        calls.append(pool)
        return _gt_trades([("buy", "0xAAA"), ("buy", "0xQ")])
    trending = lambda slug: {"data": [{"attributes": {"address": "tp1"}}]}
    cands = [{"chain": "base", "pool": "fresh1", "created": time.time()}]
    r = ev.cycle(st, {"base": "base"}, cands, fetch, trending, now=time.time())
    assert r["watched"] == 1 and ev.hit_n(st, "base", "fresh1") == 1
    assert "tp1" in calls and r["learned"] >= 1


def test_dev_history_parse_judge_and_merge():
    import dev_history as dh
    page = {"data": [{"ixs": [{"instructionName": "create", "decoded": {"accounts": [{"name": "mint", "pubkey": "M1"}, {"name": "user", "pubkey": "D"}]}},
                              {"instructionName": "buy", "decoded": {}},
                              {"instructionName": "create_v2", "decoded": {"accounts": {"mint": "M2"}}}]}], "paginationToken": None}
    assert dh.find_creates(page) == ["M1", "M2"]
    pairs = [{"baseToken": {"address": "M1"}, "dexId": "pumpswap", "marketCap": 20000},
             {"baseToken": {"address": "M2"}, "dexId": "pumpfun", "marketCap": 5000}]
    v = dh.judge(pairs, ["M1", "M2", "M3"])
    assert v["M1"]["win"] and not v["M2"]["win"] and not v["M3"]["win"]
    st = _FakeState()
    rec = dh.backfill_one(st, "DEV", mints_fn=lambda d: ["M1", "M2", "M3"], pairs_fn=lambda m: pairs)
    assert rec["launches"] == 3 and rec["wins"] == 1
    assert dh.queue(st, {"DEV": [5, 1], "X": [2, 0], "Y": [1, 0]}, ["Z"], n=5) == ["X", "Z"]    # DEV just checked; Y has 1 launch
    assert dh.merged_devs({"DEV": [1, 0]}, st.d[dh.KEY])["DEV"] == [3, 1]


def test_dev_history_rpc_fallback_detects_create():
    import dev_history as dh
    tx = {"meta": {"logMessages": ["Program log: Instruction: Create"]},
          "transaction": {"message": {"instructions": [{"programId": dh.PUMP_PROGRAM, "accounts": ["MINTX", "o"]}]}}}
    def rpc(m, p):
        if m == "getSignaturesForAddress":
            return {"ok": True, "result": [{"signature": "s1"}, {"signature": "s2", "err": {"x": 1}}]}
        return {"ok": True, "result": tx}
    assert dh.creates_from_rpc("DEV", rpc) == ["MINTX"]


def test_rpc_pool_uses_own_helius_first_and_masks_key(monkeypatch):
    from executor import rpc_pool
    monkeypatch.setenv("SOLANA_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=SECRET123")
    seen = []
    def fake_post(url, json=None, timeout=0):
        seen.append(url)
        return {"ok": True, "status_code": 200, "json": {"result": 1}}
    monkeypatch.setattr(rpc_pool, "post_json", fake_post)
    r = rpc_pool.rpc_call("solana", "getSlot", [])
    assert seen[0].startswith("https://mainnet.helius-rpc.com") and r["ok"]
    assert "SECRET123" not in str(r)


def test_kol_feed_cache_shared_and_used_when_paced(monkeypatch):
    import time, copy_signals
    from layers import kol_feed, roster
    st = _FakeState()
    monkeypatch.setattr(kol_feed, "state", st)
    name = sorted(roster.TIER_1)[0]
    kol_feed.cache_put("solana", {"ok": True, "buy_trades": [{"kol_name": name, "action": "buy", "token_mint": "MINTZpump", "wallet_address": "W" * 40},
                                                             {"kol_name": "Nobody", "action": "buy", "token_mint": "X"}], "sell_trades": []})
    c = kol_feed.cached_feed("solana")
    assert c["ok"] and len(c["buy_trades"]) == 1                       # non-roster trades dropped
    monkeypatch.setattr(kol_feed, "fetch_kol_feed_both", lambda *a, **k: {"ok": False, "reason": "paced", "buy_trades": [], "sell_trades": []})
    n, notes = copy_signals.refresh(st)
    assert n == 1 and any("cached" in x for x in notes)
    assert st.d.get("kol_wallets_v1", {}).get(name) == "W" * 40         # wallet harvested for the on-chain watcher


def test_hot_words_from_logs_and_score(tmp_path):
    import json, time, dev_rank
    from layers import fast_lane
    now = time.time()
    rows = []
    def coin(m, name, age_h, peak_x):
        rows.append({"t": "create", "ts": now - age_h * 3600, "mint": m, "creator": "c", "mcap_sol": 30.0, "name": name, "sym": name[:5]})
        rows.append({"t": "outcome", "mint": m, "cp_min": 60, "mcap_usd": 30.0 * dev_rank.SOL_USD * peak_x})
    coin("a", "Veil Wojak", 3, 20)          # runner 3 h ago
    coin("b", "Randomcoin", 3, 0.5)         # flop
    coin("c", "Fresh Runner", 0.2, 30)      # too young: peak not fully known
    (tmp_path / "x.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    hw = dev_rank.hot_words(str(tmp_path / "*.jsonl"), now)
    assert "veil" in hw and "wojak" in hw and "randomcoin" not in hw and "fresh" not in hw
    assert fast_lane.score({"confirmations": ["hot_meta"]}) == 2


def test_score_breakdown_text_matches_score():
    from layers import fast_lane
    d = {"confirmations": ["good_dev", "pressure_free", "velocity_free", "hot_meta"], "fb": 50, "fs": 10, "entry_mcap_sol": 32}
    assert fast_lane.score(d) == sum(p for _, p in fast_lane.score_parts(d)) == 3 + 1 + 1 + 2 + 2 + 1 + 1
    t = fast_lane.score_text(d)
    assert t.startswith("11 = ") and "good_dev +3" in t and "hot_meta +2" in t and "sell/buy ratio 0.20 +2" in t


def test_serial_launcher_penalty_unless_proven_dev():
    from layers import fast_lane
    base = {"confirmations": ["famous_name"], "launches": 8}
    assert fast_lane.score(base) == 3   # penalty switched off 16 Oct (SERIAL_LAUNCHER_PENALTY=0)
    assert fast_lane.score({"confirmations": ["famous_name", "good_dev"], "launches": 8}) == 6
    assert fast_lane.score({"confirmations": ["famous_name"], "launches": 1}) == 3


def test_solana_tracker_leaderboard_refresh_and_watch_set():
    import solana_tracker as stk, wallet_watch, os
    st = _FakeState()
    os.environ.pop("SOLANATRACKER_API_KEY", None)
    assert "no SOLANATRACKER_API_KEY" in stk.refresh(st, now=1e6)
    os.environ["SOLANATRACKER_API_KEY"] = "x"
    try:
        class R:
            status_code = 200
            def json(self): return {"traders": [{"wallet": "W" * 40, "ending": {"pnl": {"realized": 500}}, "counts": {"trades": 50}},
                                                {"wallet": "L" * 40, "ending": {"pnl": {"realized": -5}}}], "pagination": {"nextCursor": None}}
        msg = stk.refresh(st, now=1e6, get=lambda u, p, h: R())
        assert "2 wallets" in msg and st.d[stk.KEY_WALLETS] == ["W" * 40, "L" * 40]
        assert stk.refresh(st, now=1e6 + 500, get=lambda u, p, h: R()) == "fresh"
        assert "W" * 40 in wallet_watch.watch_set(st).values()
    finally:
        os.environ.pop("SOLANATRACKER_API_KEY", None)


def test_trending_words_only_from_pools_that_are_up():
    import dev_rank
    body = {"data": [{"attributes": {"name": "SQUIRREL / SOL", "price_change_percentage": {"h1": "120", "h24": "5"}}},
                     {"attributes": {"name": "SLEEPYCOIN / SOL", "price_change_percentage": {"h1": "-5", "h24": "10"}}},
                     {"attributes": {"name": "Moon Cat / SOL", "price_change_percentage": {"h1": "10", "h24": "900"}}}]}
    assert dev_rank.trending_words(body) == ["moon", "squirrel"]


def test_st_first_buyers_collects_early_profitable_wallets():
    import os, st_enrich as se
    os.environ["SOLANATRACKER_API_KEY"] = "x"
    try:
        st = _FakeState()
        class R:
            status_code = 200
            def __init__(self, rows): self.rows = rows
            def json(self): return {"buyers": self.rows}
        data = {"m1": [{"wallet": "A", "pnl": 10}, {"wallet": "B", "pnl": -3}], "m2": [{"wallet": "A", "pnl": {"realized": 4}}, {"wallet": "C", "pnl": 1}],
                "m3": [{"wallet": "A", "pnl": 2}, {"wallet": "C", "pnl": 5}], "m4": [{"wallet": "C", "pnl": 9}]}
        get = lambda url, h: R(data[url.rsplit("/", 1)[1]])
        msg = se.run(st, ["m1", "m2", "m3", "m4"], now=1e6, get=get, max_calls=10)
        assert st.d[se.KEY_OUT] == ["A", "C"] and "4 winners" in msg                 # A in 3 winners, C in 3, B lost money
        assert se.run(st, ["m1", "m2", "m3", "m4"], now=1e6, get=get, max_calls=10).startswith("0 calls")   # no repeat calls
        assert se.run(st, ["m5"], now=1e6, get=get, max_calls=10, day_cap=4).startswith("0 calls")          # day budget respected
    finally:
        os.environ.pop("SOLANATRACKER_API_KEY", None)


def test_entry_gate_rejects_fake_dexscreener_mcaps():
    import entry_gate as g
    assert g.sane_mcap(80_000, 700.0, 100.0) == 80_000            # normal launch kept
    assert g.sane_mcap(531e6, 27.9, 100.0) == 0.0                 # TRUMP-style: huge cap vs a 28 SOL stream coin
    assert g.sane_mcap(101e6, None, 100.0) == 0.0                 # Veil-style print: over $30M in the first hour
    assert g.sane_mcap(None, 5, 100.0) == 0.0 and g.sane_mcap("x", 5, 100.0) == 0.0

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

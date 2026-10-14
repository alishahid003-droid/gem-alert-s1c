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


def _dec(mint, age, mcap, conf=("pressure_free", "velocity_free")):
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

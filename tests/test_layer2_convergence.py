import json
import os

from layers.layer2_convergence import detect_convergence

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_convergence_detected_within_window_for_roster_members():
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = detect_convergence(trades)
    token_a_events = [e for e in events if e["token"] == "TOKEN_A"]
    assert len(token_a_events) == 1
    ev = token_a_events[0]
    # Unipcs (14:00) and Avast (14:20) are both Tier 1, within 1hr -> convergence
    assert set(ev["wallets"]) >= {"Unipcs", "Avast"}
    assert ev["count"] >= 2


def test_no_convergence_when_only_one_roster_member():
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = detect_convergence(trades)
    # TOKEN_B has NotTracked (not in roster) + Aurelius (Tier 2, alone) -> no convergence
    token_b_events = [e for e in events if e["token"] == "TOKEN_B"]
    assert token_b_events == []


def test_out_of_window_buy_not_counted_with_earlier_pair():
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = detect_convergence(trades)
    ev = next(e for e in events if e["token"] == "TOKEN_A")
    # frank buys at 16:10, more than 1hr after Unipcs (14:00) -- should not
    # inflate the Unipcs-anchored window, though it may form its own window
    # with Avast (14:20 -> 16:10 is also > 1hr, so frank should join no one)
    assert "frank" not in ev["wallets"] or ev["count"] == 2


def test_poll_layer2_accepts_prefetched_trades_no_network_call(monkeypatch):
    from layers.layer2_convergence import poll_layer2
    import layers.layer2_convergence as l2

    def boom(*a, **k):
        raise AssertionError("should not fetch when trades are pre-supplied")

    monkeypatch.setattr(l2, "fetch_kol_feed", boom)
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    result = poll_layer2("solana", trades=trades)
    assert result["ok"] is True
    assert len(result["events"]) >= 1
    assert len(result["mc_points"]) == len(trades)


# --- Single-trader buy alerts (Ali, Sept 24 2026 -- "for these 38 people
# buy should also work...as that is when we know they enter...not the sell
# side later") ---
from layers.layer2_convergence import single_trader_buy_events

def test_single_trader_event_fires_for_lone_roster_member():
    """TOKEN_B has only Aurelius (Tier 2) -- detect_convergence() finds
    nothing there (needs 2+), but single_trader_buy_events() must still
    report it: this is exactly the gap Ali flagged."""
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = single_trader_buy_events(trades)
    token_b = [e for e in events if e["token"] == "TOKEN_B" and e["name"] == "Aurelius"]
    assert len(token_b) == 1
    assert token_b[0]["tier"] == "Tier 2"


def test_single_trader_event_ignores_untracked_name():
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = single_trader_buy_events(trades)
    assert not any(e["name"] == "NotTracked" for e in events)


def test_single_trader_event_also_fires_for_convergence_pairs():
    """A wallet that's part of a 2+ convergence should STILL show up in the
    single-trader feed too -- both signals are independent, not mutually
    exclusive (matches pump.fun Layer 2b's same design)."""
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = single_trader_buy_events(trades)
    token_a_names = {e["name"] for e in events if e["token"] == "TOKEN_A"}
    assert {"Unipcs", "Avast"} <= token_a_names


def test_single_trader_event_respects_custom_roster():
    trades = [{"action": "buy", "kol_name": "SomeNewFollow", "token_mint": "TOKEN_X"}]
    assert single_trader_buy_events(trades, roster=set()) == []
    assert len(single_trader_buy_events(trades, roster={"SomeNewFollow"})) == 1


# --- Large untracked buys / possible-insider signal (Ali, Sept 24 2026 --
# "maybe a new person has joined fomo and has good cash balance...maybe he
# can be an insider entering") ---
from layers.layer2_convergence import large_untracked_buys, LARGE_UNTRACKED_BUY_MIN_SOL

def test_large_untracked_buy_detected():
    """NotTracked's 9.0 SOL buy on TOKEN_B is exactly the scenario Ali
    described -- must be surfaced even though the name isn't on roster."""
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = large_untracked_buys(trades)
    assert len(events) == 1
    assert events[0]["name"] == "NotTracked"
    assert events[0]["wallet"] == "W4"
    assert events[0]["token"] == "TOKEN_B"
    assert events[0]["sol_amount"] == 9.0


def test_tracked_names_never_appear_even_if_large():
    """A tracked person's buy, however large, belongs to the other alert
    paths (single/convergence), not this one -- avoids double-tagging."""
    trades = _load("madeonsol_kol_feed_buy_sample.json")["trades"]
    events = large_untracked_buys(trades, min_sol=1.0)  # lowered to include everyone by size
    names = {e["name"] for e in events}
    assert "Unipcs" not in names and "Avast" not in names and "Aurelius" not in names


def test_small_untracked_buy_not_flagged():
    trades = [{"action": "buy", "kol_name": "RandomWallet", "wallet_address": "Wx",
               "sol_amount": 0.5, "token_mint": "TOKEN_Z"}]
    assert large_untracked_buys(trades) == []


def test_threshold_is_configurable():
    trades = [{"action": "buy", "kol_name": "RandomWallet", "wallet_address": "Wx",
               "sol_amount": 2.0, "token_mint": "TOKEN_Z"}]
    assert large_untracked_buys(trades, min_sol=5.0) == []
    assert len(large_untracked_buys(trades, min_sol=1.0)) == 1

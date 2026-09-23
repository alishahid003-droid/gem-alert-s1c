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

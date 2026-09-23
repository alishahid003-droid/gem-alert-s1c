import json
import os

from layers.layer9_sell_mirror import detect_sell_events

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_deployer_sell_detected_even_without_kol_name():
    trades = _load("madeonsol_kol_feed_sell_sample.json")["trades"]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"},
                                 deployer_wallet_by_token={"TOKEN_A": "DEPLOYER1"})
    deployer_events = [e for e in events if e["is_deployer_sell"]]
    assert len(deployer_events) == 1
    assert deployer_events[0]["who"] == "deployer"


def test_roster_member_sell_detected_and_untracked_ignored():
    trades = _load("madeonsol_kol_feed_sell_sample.json")["trades"]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"})
    names = {e["who"] for e in events}
    assert "Unipcs" in names
    assert "NotTracked" not in names  # not in SELL_WATCH_ROSTER, must be ignored


def test_sell_outside_relevant_tokens_ignored():
    trades = _load("madeonsol_kol_feed_sell_sample.json")["trades"]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"})
    assert all(e["token"] != "TOKEN_OUTSIDE_FEED" for e in events)


def test_pct_of_position_unknown_when_no_prior_balance_never_faked():
    trades = _load("madeonsol_kol_feed_sell_sample.json")["trades"]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"})
    unipcs_event = next(e for e in events if e["who"] == "Unipcs")
    assert unipcs_event["pct_of_position"] is None  # no prior_balances given -- must not guess


def test_pct_of_position_computed_when_prior_balance_known():
    trades = _load("madeonsol_kol_feed_sell_sample.json")["trades"]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"},
                                 prior_balances={("W1", "TOKEN_A"): 10.0})
    unipcs_event = next(e for e in events if e["who"] == "Unipcs")
    assert unipcs_event["pct_of_position"] == 50.0


def test_pct_prefers_token_amount_over_sol_amount_for_unit_correctness():
    from layers.layer9_sell_mirror import detect_sell_events
    trades = [{"wallet_address": "W1", "kol_name": "Unipcs", "action": "sell",
               "sol_amount": 999.0, "token_amount": 5.0, "token_mint": "TOKEN_A"}]
    events = detect_sell_events(trades, relevant_tokens={"TOKEN_A"},
                                 prior_balances={("W1", "TOKEN_A"): 10.0})
    # 5.0/10.0 = 50%, NOT whatever sol_amount=999 would have produced
    assert events[0]["pct_of_position"] == 50.0
    assert events[0]["amount_for_pct"] == 5.0
    assert events[0]["amount"] == 999.0  # display value still prefers sol_amount


def test_update_balance_after_sell_is_purely_arithmetic():
    from layers.layer9_sell_mirror import update_balance_after_sell
    new_bal = update_balance_after_sell("W1", "TOKEN_A", 4.0, {("W1", "TOKEN_A"): 10.0})
    assert new_bal == 6.0


def test_update_balance_after_sell_never_negative():
    from layers.layer9_sell_mirror import update_balance_after_sell
    new_bal = update_balance_after_sell("W1", "TOKEN_A", 15.0, {("W1", "TOKEN_A"): 10.0})
    assert new_bal == 0.0


def test_update_balance_after_sell_none_without_prior():
    from layers.layer9_sell_mirror import update_balance_after_sell
    assert update_balance_after_sell("W1", "TOKEN_A", 4.0, {}) is None


def test_poll_layer9_accepts_prefetched_trades_no_network_call(monkeypatch):
    from layers.layer9_sell_mirror import poll_layer9
    import layers.layer9_sell_mirror as l9

    def boom(*a, **k):
        raise AssertionError("should not fetch when trades are pre-supplied")

    monkeypatch.setattr(l9, "fetch_sell_feed", boom)
    trades = [{"wallet_address": "W1", "kol_name": "Unipcs", "action": "sell",
               "token_amount": 5.0, "token_mint": "TOKEN_A"}]
    result = poll_layer9("solana", {"TOKEN_A"}, trades=trades)
    assert result["ok"] is True
    assert len(result["events"]) == 1

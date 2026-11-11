"""Isolated tests for dexscreener_momentum_gate.py. No network, read-only against live repo."""
import sys
import dexscreener_momentum_gate as mg


def check_1_healthy_coin_no_override():
    pair = {"priceChange": {"m5": 2.1, "h1": 15.3, "h6": 40.0, "h24": 120.0}}
    r = mg.momentum_verdict_from_pair(pair)
    return r["drawdown_pct"] == 15.3 and r["window_used"] == "h1"


def check_2_collapsed_coin_triggers_override_threshold():
    pair = {"priceChange": {"m5": -5.0, "h1": -72.4, "h6": -80.0}}
    r = mg.momentum_verdict_from_pair(pair)
    return r["drawdown_pct"] == -72.4 and r["drawdown_pct"] <= mg.DRAWDOWN_OVERRIDE_PCT


def check_3_h1_missing_falls_back_to_h6():
    pair = {"priceChange": {"m5": 1.0, "h6": -65.0}}
    r = mg.momentum_verdict_from_pair(pair)
    return r["drawdown_pct"] == -65.0 and r["window_used"] == "h6"


def check_4_no_data_returns_none_never_fabricated():
    pair = {"priceChange": {}}
    r = mg.momentum_verdict_from_pair(pair)
    return r["drawdown_pct"] is None and r["window_used"] is None


def check_5_missing_pricechange_key_entirely():
    pair = {"liquidity": {"usd": 5000}}
    r = mg.momentum_verdict_from_pair(pair)
    return r["drawdown_pct"] is None


def check_6_real_incident_shape_robinhood_stall():
    pair = {"priceChange": {"m5": -1.0, "h1": -4.0}}
    r = mg.momentum_verdict_from_pair(pair)
    return r["drawdown_pct"] == -4.0 and r["drawdown_pct"] > mg.DRAWDOWN_OVERRIDE_PCT


def check_7_out_of_scope_chain_wrapper_shortcircuits():
    r = mg.dexscreener_momentum_verdict("solana", "somefakeaddress")
    return r["drawdown_pct"] is None and "not in scope" in r["reason"]


if __name__ == "__main__":
    results = [
        check_1_healthy_coin_no_override(),
        check_2_collapsed_coin_triggers_override_threshold(),
        check_3_h1_missing_falls_back_to_h6(),
        check_4_no_data_returns_none_never_fabricated(),
        check_5_missing_pricechange_key_entirely(),
        check_6_real_incident_shape_robinhood_stall(),
        check_7_out_of_scope_chain_wrapper_shortcircuits(),
    ]
    print(f"TOTAL: {sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)

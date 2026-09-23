import json
import os

from layers.layer1_deployer import parse_deployer_alerts, chain_for_cycle

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parses_and_filters_to_elite_and_good_only():
    with open(os.path.join(FIXTURES, "madeonsol_deployer_alerts_sample.json")) as f:
        payload = json.load(f)
    alerts = parse_deployer_alerts(payload)
    tiers = {a["deployer_tier"] for a in alerts}
    assert tiers == {"elite", "good"}
    assert len(alerts) == 2  # the "unranked" one must be dropped
    assert all("token_address" in a for a in alerts)


def test_chain_for_cycle_alternates_each_bucket():
    # Consecutive 10-min buckets must alternate, not repeat or coincide.
    assert chain_for_cycle(0, cycle_seconds=600) == "solana"
    assert chain_for_cycle(600, cycle_seconds=600) == "robinhood_chain"
    assert chain_for_cycle(1200, cycle_seconds=600) == "solana"
    assert chain_for_cycle(1800, cycle_seconds=600) == "robinhood_chain"


def test_chain_for_cycle_is_pure_and_deterministic():
    # Same timestamp -> same answer every time, no hidden state/counter.
    ts = 1_725_000_123.456
    assert chain_for_cycle(ts) == chain_for_cycle(ts)


def test_chain_for_cycle_within_one_bucket_is_stable():
    # Two timestamps inside the same 10-min bucket must pick the same chain
    # (a run that starts a few seconds late shouldn't flip the answer).
    assert chain_for_cycle(605, cycle_seconds=600) == chain_for_cycle(1195, cycle_seconds=600)

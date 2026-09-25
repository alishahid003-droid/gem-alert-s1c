import json
import os

import pytest

import state
from layers.layer1_deployer import parse_deployer_alerts, chain_for_cycle, poll_layer1

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    # Added Sept 25 2026: fetch_deployer_alerts now checks/records real
    # MadeOnSol call-budget state (see state.py's madeonsol_budget_remaining
    # docstring) -- without this, every test run here would write into the
    # real on-device state file and falsely deplete the live system's daily
    # budget tracker. Same isolation pattern as tests/test_madeonsol_gating.py.
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setattr(state.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(state.CONFIG, "upstash_redis_rest_token", None)


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

def test_poll_layer1_surfaces_real_http_error_not_generic_fetch_failed(monkeypatch):
    # Live-tested Sept 24 2026: MadeOnSol's free key hit its own rate limit
    # (HTTP 429, ip_rotation) and poll_layer1's failure reason came back as
    # the literal string "fetch failed" -- exactly this bug, just in
    # layer1_deployer.py instead of kol_feed.py (see describe_fetch_failure
    # in utils/http.py, now shared by both).
    import layers.layer1_deployer as layer1_deployer

    def fake_get_json(url, headers=None, params=None, timeout=20):
        return {"ok": False, "status_code": 429, "url": url,
                "json": {"error": "rate_limit_exceeded", "error_kind": "ip_rotation"}}

    monkeypatch.setattr(layer1_deployer, "get_json", fake_get_json)
    monkeypatch.setattr(layer1_deployer.CONFIG, "madeonsol_api_key", "msk_test")

    result = poll_layer1("solana")
    assert result["ok"] is False
    assert "429" in result["reason"]
    assert "ip_rotation" in result["reason"]


def test_fetch_deployer_alerts_sends_tier_as_repeated_param_not_comma_joined(monkeypatch):
    # Regression test for the Sept 25 2026 production bug: MadeOnSol's
    # /deployer-hunter/alerts endpoint returns 400 "Invalid query parameters"
    # for a comma-joined tier value ("tier=elite,good") -- confirmed live
    # against the real API. It wants the tier param repeated once per value
    # instead, which requests encodes automatically from a list value. This
    # bug meant Layer 1's elite/good deployer alerts were silently 400ing
    # on every single poll-fast.yml cycle in production. Locks in the list
    # shape so this can't silently regress back to a comma-joined string.
    import layers.layer1_deployer as layer1_deployer

    captured = {}

    def fake_get_json(url, headers=None, params=None, timeout=20):
        captured["params"] = params
        return {"ok": True, "status_code": 200, "url": url, "json": {"alerts": []}}

    monkeypatch.setattr(layer1_deployer, "get_json", fake_get_json)
    monkeypatch.setattr(layer1_deployer.CONFIG, "madeonsol_api_key", "msk_test")

    layer1_deployer.fetch_deployer_alerts("solana")

    tier_value = captured["params"]["tier"]
    assert isinstance(tier_value, (list, tuple)), (
        "tier must be a list/tuple so requests encodes it as repeated params "
        "(tier=elite&tier=good), not a single comma-joined string -- MadeOnSol "
        "rejects the comma-joined form with 400 Invalid query parameters"
    )
    assert set(tier_value) == {"elite", "good"}
    assert "," not in "".join(tier_value)


def test_fetch_deployer_alerts_fails_closed_when_daily_budget_exhausted(monkeypatch):
    import layers.layer1_deployer as l1
    state.record_madeonsol_calls(state.MADEONSOL_DAILY_BUDGET)  # exhaust it

    def unexpected_call(*a, **kw):
        raise AssertionError("get_json should not be called when budget is exhausted")

    monkeypatch.setattr(l1, "get_json", unexpected_call)
    monkeypatch.setattr(l1.CONFIG, "madeonsol_api_key", "msk_test")

    result = l1.fetch_deployer_alerts("solana")
    assert result["ok"] is False
    assert "budget" in result["reason"].lower()


def test_fetch_deployer_alerts_records_a_call_on_success(monkeypatch):
    import layers.layer1_deployer as l1

    monkeypatch.setattr(l1, "get_json", lambda *a, **kw: {"ok": True, "status_code": 200, "json": {"alerts": []}})
    monkeypatch.setattr(l1.CONFIG, "madeonsol_api_key", "msk_test")

    assert state.madeonsol_calls_today() == 0
    l1.fetch_deployer_alerts("solana")
    assert state.madeonsol_calls_today() == 1

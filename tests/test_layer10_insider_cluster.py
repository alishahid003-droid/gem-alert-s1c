"""Tests for the parts of layer10 that don't need a live network: kol_name
resolution and the persisted cluster-link cache. The actual funding-chain
trace (_first_inbound_transfer) needs a real Mobula call and is not unit
tested here -- same honest gap as every other live-network path in this
codebase until a real run confirms the endpoint/response shape."""
import pytest

import state
import layers.layer10_insider_cluster as layer10


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def test_resolves_via_known_kol_name():
    result = layer10.resolve_identity("solana", "SomeWallet111", kol_name="Unipcs")
    assert result["tier"] == "Tier 1"
    assert result["source"] == "kol_name_match"


def test_untracked_name_falls_through_to_funding_trace_path():
    # No MOBULA_API_KEY configured in test env -> fetch_wallet_transactions
    # returns ok=False -> _first_inbound_transfer returns None -> untracked.
    result = layer10.resolve_identity("solana", "RandomWallet999", kol_name="NobodyKnown")
    assert result["tier"] == "untracked"
    assert result["source"] == "no_funding_history"


def test_register_known_wallet_then_cached_lookup():
    layer10.register_known_wallet("solana", "KnownDeployerABC", tier="elite", source="madeonsol_tier")
    result = layer10.resolve_identity("solana", "KnownDeployerABC")
    assert result["tier"] == "elite"
    assert result["source"] == "cached:madeonsol_tier"


def test_insider_tag_skips_deployer_itself():
    tags = layer10.tag_insider_holders("solana", "DeployerXYZ", ["DeployerXYZ"])
    assert tags == {}  # deployer excluded from its own holder list

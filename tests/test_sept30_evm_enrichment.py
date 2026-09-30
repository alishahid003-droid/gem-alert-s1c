"""Sept 30 2026: Base/BSC (GeckoTerminal path) enrichment -- free GoPlus
holder_count -> holder growth, free GeckoTerminal txn counts -> activity-
collapse override, daily-capped Birdeye drawdown only for would-be A/B."""
import pytest

import state
import layers.layer0_scoring as l0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def test_flatten_extracts_txn_totals():
    gt = {"data": [{"attributes": {"reserve_in_usd": "20000", "pool_created_at": "2026-09-30T10:00:00Z",
                                   "transactions": {"h1": {"buys": 3, "sells": 2}, "h24": {"buys": 300, "sells": 250}}},
                    "relationships": {"base_token": {"data": {"id": "bsc_0xabc"}}}}]}
    items = l0.flatten_geckoterminal_pools(gt)
    assert items[0]["txns_h1_total"] == 5 and items[0]["txns_h24_total"] == 550


GOOD_GP = {"is_mintable": "0", "is_blacklisted": "0", "is_honeypot": "0", "holder_count": "120"}


def _item(**kw):
    base = {"address": "0xabc", "volume_24h_usd": 40000.0, "liquidity_usd": 20000.0,
            "pool_created_at": "2026-09-30T08:00:00Z", "txns_h1_total": 40, "txns_h24_total": 400}
    base.update(kw)
    return base


def test_activity_collapse_demotes_to_d(monkeypatch):
    monkeypatch.setattr(l0, "fetch_goplus_security", lambda c, a: {"ok": True, "data": GOOD_GP})
    monkeypatch.setattr(l0, "_evm_launch_drawdown", lambda c, i: None)
    out = l0.score_geckoterminal_pools("bsc", [_item(txns_h1_total=1, txns_h24_total=600)])
    assert out[0]["score"].band == "D"


def test_birdeye_only_called_for_a_or_b(monkeypatch):
    calls = []
    monkeypatch.setattr(l0, "_evm_launch_drawdown", lambda c, i: calls.append(i["address"]) or -80.0)
    monkeypatch.setattr(l0, "fetch_goplus_security", lambda c, a: {"ok": True, "data": GOOD_GP})
    out = l0.score_geckoterminal_pools("base", [_item()])
    assert calls == ["0xabc"]
    assert out[0]["score"].band == "D"  # confirmed -80% launch collapse overrides
    calls.clear()
    monkeypatch.setattr(l0, "fetch_goplus_security",
                        lambda c, a: {"ok": True, "data": {"is_mintable": "1", "is_honeypot": "1"}})
    l0.score_geckoterminal_pools("base", [_item(volume_24h_usd=10.0)])
    assert calls == []  # C/D first pass -> no Birdeye spend


def test_birdeye_daily_cap(monkeypatch):
    monkeypatch.setenv("BIRDEYE_EVM_DAILY_CAP", "2")
    assert l0._birdeye_evm_allowance() and l0._birdeye_evm_allowance()
    assert l0._birdeye_evm_allowance() is False


def test_goplus_holder_count_recorded(monkeypatch):
    monkeypatch.setattr(l0, "fetch_goplus_security", lambda c, a: {"ok": True, "data": GOOD_GP})
    monkeypatch.setattr(l0, "_evm_launch_drawdown", lambda c, i: None)
    l0.score_geckoterminal_pools("bsc", [_item()])
    assert state.get_holder_history("0xabc")

"""Checklist 4.5: when a roster trader we copied sells, we sell."""
import pytest

import state
import executor.position_state as ps
from executor import copy_exit
from executor.swap_executor import ExecutionResult


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    sold = []
    monkeypatch.setattr(copy_exit.swap_executor, "execute_sell",
                        lambda **kw: sold.append(kw) or ExecutionResult(True, "sold", filled_usd=30.0))
    yield sold


def _hold(chain="solana", mint="MINT1", reason="fomoapi_roster_convergence"):
    ps.record_stage_entry(chain, mint, "stage2", 20.0, 100_000, reason)
    ps.record_fill(chain, mint, 1000.0)


def test_copied_trader_sells_we_sell(iso):
    _hold()
    state.record_fomo_roster_buy("solana", "MINT1", "Alice")
    out = copy_exit.mirror_sells([{"chain": "solana", "mint": "MINT1", "trader": "Alice"}])
    assert out[0]["ok"] and iso[0]["amount_tokens"] == pytest.approx(1000.0)
    assert ps.get_position("solana", "MINT1")["status"] == "closed"


def test_unrelated_trader_sell_ignored(iso):
    _hold()
    state.record_fomo_roster_buy("solana", "MINT1", "Alice")
    assert copy_exit.mirror_sells([{"chain": "solana", "mint": "MINT1", "trader": "Bob"}]) == []
    assert iso == []


def test_no_position_no_sell(iso):
    state.record_fomo_roster_buy("solana", "MINT2", "Alice")
    assert copy_exit.mirror_sells([{"chain": "solana", "mint": "MINT2", "trader": "Alice"}]) == []


def test_trader_named_in_entry_reason(iso):
    _hold(reason="2 roster traders (Alice, Carol)")
    out = copy_exit.mirror_sells([{"chain": "solana", "mint": "MINT1", "trader": "Carol"},
                                  {"chain": "solana", "mint": "MINT1", "trader": "Alice"}])
    assert len(out) == 1 and len(iso) == 1


def test_detector_reports_roster_sells(monkeypatch):
    from layers import layer13_fomo_copytrade as l13
    monkeypatch.setattr(l13, "_match_roster", lambda h, d, learned=None, promoted=None: "Alice" if h == "alice" else None)
    res = l13.detect_roster_buys_and_theses(
        [{"alertType": "sell", "trader": "alice", "chain": "solana", "tokenAddress": "MINT1"},
         {"alertType": "sell", "trader": "zed", "chain": "solana", "tokenAddress": "MINT1"}],
        allow_paid_scoring=False)
    assert res["roster_sells"] == [{"chain": "solana", "mint": "MINT1", "trader": "Alice"}]

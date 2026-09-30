"""Phase 4 exit discipline (Sept 30 2026) + the two sell-side fixes."""
import pytest

import state
import executor.exit_rules as er
import executor.position_state as position_state
import executor.swap_executor as swap_executor
import executor.defensive_sell as defensive_sell
from executor.swap_executor import ExecutionResult

T0 = 1_790_700_000.0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def ev(mult, peak=None, age_min=10, remaining=1.0, locked=False):
    return er.evaluate_exit(100_000, 100_000 * mult, 100_000 * (peak or mult), T0, T0 + age_min * 60,
                            remaining, locked, 0.03)


def test_hold_in_normal_range():
    assert ev(1.1).action == "hold"


def test_stop_loss():
    d = ev(0.74)
    assert d.action == "exit_all" and d.exit_type == "stop_loss"


def test_breakeven_lock_recovers_stake():
    d = ev(1.6)
    assert d.action == "sell_partial" and d.exit_type == "breakeven_lock"
    assert abs(d.pct_of_original * 1.6 - 1.03) < 1e-9  # sale covers stake + costs


def test_breakeven_fires_once():
    assert ev(1.6, locked=True).action == "hold"


def test_trailing_stop_after_big_run():
    d = ev(2.5, peak=4.0, locked=True)
    assert d.action == "exit_all" and d.exit_type == "trailing_stop"
    assert ev(3.5, peak=4.0, locked=True).action == "hold"


def test_time_stop_on_dead_coin():
    d = ev(1.05, peak=1.1, age_min=120)
    assert d.action == "exit_all" and d.exit_type == "time_stop"
    assert ev(1.05, peak=1.3, age_min=120).action == "hold"  # it did move; not dead


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("STOP_LOSS_PCT", "0.10")
    assert ev(0.89).exit_type == "stop_loss"


def _open(amount=1000.0):
    position_state.record_stage_entry("solana", "M", "stage1", 30.0, 100_000, "test")
    position_state.record_fill("solana", "M", amount, tx_signature="sig")


def test_check_and_exit_breakeven_then_stop(monkeypatch):
    _open()
    sells = []
    monkeypatch.setattr(swap_executor, "execute_sell", lambda chain, token_address, amount_tokens, reason:
                        sells.append(amount_tokens) or ExecutionResult(True, "ok", filled_usd=31.0))
    r = er.check_and_exit("solana", "M", 160_000, 50_000, now_ts=T0)
    assert r["decision"].exit_type == "breakeven_lock"
    pos = position_state.get_position("solana", "M")
    assert pos["breakeven_locked"] and 0 < pos["ladder_scale"] < 1
    r2 = er.check_and_exit("solana", "M", 70_000, 50_000, now_ts=T0 + 60)
    assert r2["decision"].exit_type == "stop_loss"
    closed = position_state.get_position("solana", "M")
    assert closed["status"] == "closed"
    # sold only what remained, and P&L counts the earlier lock proceeds
    from executor.compound_scalper import estimate_round_trip_cost_pct
    lock_pct = (1 + estimate_round_trip_cost_pct("solana", 30.0, 50_000)) / 1.6
    assert sells[0] == pytest.approx(1000.0 * lock_pct)
    assert sells[1] == pytest.approx(1000.0 * (1 - lock_pct))  # only what remained
    assert closed["pnl_usd"] == pytest.approx(31.0 + 31.0 - 30.0)


def test_no_action_without_real_fill():
    position_state.record_stage_entry("solana", "N", "stage1", 30.0, 100_000, "test")
    assert er.check_and_exit("solana", "N", 10_000) is None


def test_close_pnl_includes_trim_proceeds():
    _open()
    position_state.record_moonbag_trim("solana", "M", 3.0, 0.35, 40.0)
    closed = position_state.close_position("solana", "M", "manual", exit_usd=5.0)
    assert closed["pnl_usd"] == pytest.approx(40.0 + 5.0 - 30.0)


def test_defensive_sell_sells_only_remaining(monkeypatch):
    _open(1000.0)
    position_state.record_moonbag_trim("solana", "M", 3.0, 0.35, 40.0)
    sold = []
    monkeypatch.setattr(defensive_sell, "detect_exit_risk", lambda a, b, c=False: ["liquidity pulled"])
    monkeypatch.setattr(swap_executor, "execute_sell", lambda chain, token_address, amount_tokens, reason:
                        sold.append(amount_tokens) or ExecutionResult(True, "ok", filled_usd=1.0))
    defensive_sell.check_and_defend("solana", "M", None, None)
    assert sold == [pytest.approx(650.0)]

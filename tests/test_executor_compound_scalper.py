import pytest

import state
import executor.compound_scalper as compound_scalper
import executor.swap_executor as swap_executor
from executor.swap_executor import ExecutionResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")  # never accidentally live in a test
    # fresh, deterministic config for every test regardless of env vars
    # already set in whatever shell runs pytest
    monkeypatch.setattr(compound_scalper, "SCALPER_CONFIG", compound_scalper.CompoundScalperConfig(
        enabled=True, seed_usd=30.0, risk_pct=0.75, take_profit_multiple=2.5,
        partial_tp_pct=0.60, trail_stop_pct=0.20, hard_stop_pct=0.30,
        time_stop_minutes=20.0, max_round_trip_cost_pct=0.12, min_score_band="B",
        max_consecutive_losses=3, max_session_loss_pct=0.50, floor_usd=5.0,
        session_hours=48.0, max_trades_per_session=80,
    ))
    compound_scalper.reset_pool()
    yield


# --- cost model -----------------------------------------------------------

def test_solana_cost_reasonable_with_deep_liquidity():
    cost = compound_scalper.estimate_round_trip_cost_pct("solana", 20.0, liquidity_usd=500_000)
    assert 0.03 < cost < 0.07  # slippage+fee floor only, no impact penalty


def test_solana_cost_rises_with_thin_liquidity():
    deep = compound_scalper.estimate_round_trip_cost_pct("solana", 20.0, liquidity_usd=500_000)
    thin = compound_scalper.estimate_round_trip_cost_pct("solana", 20.0, liquidity_usd=300)
    assert thin > deep


def test_unknown_liquidity_penalized_not_treated_as_zero():
    known_deep = compound_scalper.estimate_round_trip_cost_pct("solana", 20.0, liquidity_usd=500_000)
    unknown = compound_scalper.estimate_round_trip_cost_pct("solana", 20.0, liquidity_usd=None)
    assert unknown > known_deep


def test_bsc_cost_uses_wide_conservative_ceiling():
    cost = compound_scalper.estimate_round_trip_cost_pct("bsc", 20.0, liquidity_usd=500_000)
    assert cost > 0.40  # deliberately conservative -- see module docstring


# --- entry_gate -------------------------------------------------------------

def test_disabled_by_default_config():
    compound_scalper.SCALPER_CONFIG.enabled = False
    d = compound_scalper.entry_gate("solana", "MintA", "A")
    assert d.should_fire is False
    assert "disabled" in d.reason


def test_pool_not_started_refuses_entry():
    d = compound_scalper.entry_gate("solana", "MintA", "A")
    assert d.should_fire is False
    assert "not started" in d.reason


def test_band_below_minimum_rejected():
    compound_scalper.init_pool()
    d = compound_scalper.entry_gate("solana", "MintA", "C", liquidity_usd=500_000)
    assert d.should_fire is False
    assert "below minimum" in d.reason


def test_band_a_fires_with_correct_position_size():
    compound_scalper.init_pool(seed_usd=30.0)
    d = compound_scalper.entry_gate("solana", "MintA", "A", liquidity_usd=500_000)
    assert d.should_fire is True
    assert d.position_usd == pytest.approx(30.0 * 0.75)


def test_band_b_also_fires():
    compound_scalper.init_pool(seed_usd=30.0)
    d = compound_scalper.entry_gate("solana", "MintA", "B", liquidity_usd=500_000)
    assert d.should_fire is True


def test_thin_liquidity_exceeds_cost_cap_and_is_rejected():
    compound_scalper.init_pool(seed_usd=30.0)
    d = compound_scalper.entry_gate("solana", "MintA", "A", liquidity_usd=200)
    assert d.should_fire is False
    assert "round-trip cost" in d.reason


def test_already_open_position_blocks_new_entry():
    compound_scalper.init_pool(seed_usd=30.0)
    pool = compound_scalper._get_pool()
    pool["open_position"] = {"chain": "solana", "token": "MintOpen", "entry_mcap": 100000,
                              "position_usd": 20.0, "amount_tokens": 100.0, "opened_ts": 0,
                              "peak_multiple": 1.0, "partial_tp_done": False}
    compound_scalper._save_pool(pool)
    d = compound_scalper.entry_gate("solana", "MintB", "A", liquidity_usd=500_000)
    assert d.should_fire is False
    assert "already open" in d.reason


def test_balance_below_floor_stops_entries():
    compound_scalper.init_pool(seed_usd=30.0)
    pool = compound_scalper._get_pool()
    pool["balance_usd"] = 3.0  # below default floor of 5.0
    compound_scalper._save_pool(pool)
    d = compound_scalper.entry_gate("solana", "MintA", "A", liquidity_usd=500_000)
    assert d.should_fire is False
    assert "floor" in d.reason


def test_tripped_circuit_blocks_entry():
    compound_scalper.init_pool(seed_usd=30.0)
    pool = compound_scalper._get_pool()
    pool["tripped"] = True
    pool["tripped_reason"] = "test trip"
    compound_scalper._save_pool(pool)
    d = compound_scalper.entry_gate("solana", "MintA", "A", liquidity_usd=500_000)
    assert d.should_fire is False
    assert "tripped" in d.reason


def test_expired_session_blocks_entry():
    compound_scalper.init_pool(seed_usd=30.0)
    pool = compound_scalper._get_pool()
    pool["session_start_ts"] = pool["session_start_ts"] - (49 * 3600)  # 49h ago, past 48h default
    compound_scalper._save_pool(pool)
    d = compound_scalper.entry_gate("solana", "MintA", "A", liquidity_usd=500_000)
    assert d.should_fire is False
    assert "session window elapsed" in d.reason


# --- open_scalp --------------------------------------------------------

def test_open_scalp_success_updates_pool(monkeypatch):
    compound_scalper.init_pool(seed_usd=30.0)
    ok_result = ExecutionResult(True, "ok", tx_signature="sig123", filled_usd=22.5, filled_amount_tokens=1000.0)
    monkeypatch.setattr(swap_executor, "execute_buy_solana", lambda token, usd: ok_result)
    monkeypatch.setitem(compound_scalper._BUY_FN, "solana", swap_executor.execute_buy_solana)

    result = compound_scalper.open_scalp("solana", "MintA", 22.5, entry_mcap=100_000, reason="test")
    assert result["ok"] is True

    pool = compound_scalper._get_pool()
    assert pool["open_position"]["token"] == "MintA"
    assert pool["open_position"]["amount_tokens"] == 1000.0
    assert pool["balance_usd"] == pytest.approx(30.0 - 22.5)
    assert pool["trades_this_session"] == 1


def test_open_scalp_failure_leaves_pool_untouched(monkeypatch):
    compound_scalper.init_pool(seed_usd=30.0)
    bad_result = ExecutionResult(False, "jupiter quote failed")
    monkeypatch.setattr(swap_executor, "execute_buy_solana", lambda token, usd: bad_result)
    monkeypatch.setitem(compound_scalper._BUY_FN, "solana", swap_executor.execute_buy_solana)

    result = compound_scalper.open_scalp("solana", "MintA", 22.5, entry_mcap=100_000, reason="test")
    assert result["ok"] is False

    pool = compound_scalper._get_pool()
    assert pool["open_position"] is None
    assert pool["balance_usd"] == 30.0  # untouched -- nothing was actually bought
    assert pool["trades_this_session"] == 1  # attempt still counted


# --- evaluate_exit / check_and_manage ------------------------------------

def _open_test_position(entry_mcap=100_000, opened_ts=None, amount_tokens=1000.0, position_usd=22.5,
                         peak_multiple=1.0, partial_tp_done=False):
    import time
    compound_scalper.init_pool(seed_usd=30.0)
    pool = compound_scalper._get_pool()
    pool["balance_usd"] = 30.0 - position_usd
    pool["open_position"] = {
        "chain": "solana", "token": "MintScalp", "entry_mcap": entry_mcap,
        "position_usd": position_usd, "amount_tokens": amount_tokens,
        "opened_ts": opened_ts if opened_ts is not None else time.time(),
        "peak_multiple": peak_multiple, "partial_tp_done": partial_tp_done,
        "reason": "test",
    }
    compound_scalper._save_pool(pool)


def test_no_exit_when_holding_mid_range():
    _open_test_position(entry_mcap=100_000)
    d = compound_scalper.evaluate_exit(current_mcap_usd=150_000)  # 1.5x -- above stop, below target
    assert d.should_exit is False


def test_hard_stop_fires():
    _open_test_position(entry_mcap=100_000)
    d = compound_scalper.evaluate_exit(current_mcap_usd=65_000)  # 0.65x, past 30% hard stop
    assert d.should_exit is True
    assert d.exit_type == "hard_stop"
    assert d.pct_to_sell == 1.0


def test_take_profit_partial_fires_at_target():
    _open_test_position(entry_mcap=100_000)
    d = compound_scalper.evaluate_exit(current_mcap_usd=260_000)  # 2.6x, past 2.5x target
    assert d.should_exit is True
    assert d.exit_type == "take_profit_partial"
    assert d.pct_to_sell == pytest.approx(0.60)


def test_trail_stop_fires_after_partial_tp():
    _open_test_position(entry_mcap=100_000, peak_multiple=3.0, partial_tp_done=True)
    d = compound_scalper.evaluate_exit(current_mcap_usd=230_000)  # 2.3x, 23% off the 3.0x peak
    assert d.should_exit is True
    assert d.exit_type == "trail_stop"
    assert d.pct_to_sell == 1.0


def test_rides_remainder_when_pullback_below_trail_threshold():
    _open_test_position(entry_mcap=100_000, peak_multiple=3.0, partial_tp_done=True)
    d = compound_scalper.evaluate_exit(current_mcap_usd=270_000)  # 2.7x, only 10% off peak
    assert d.should_exit is False


def test_time_stop_fires_after_window_with_no_target_hit():
    _open_test_position(entry_mcap=100_000, opened_ts=__import__("time").time() - 25 * 60)  # 25 min ago
    d = compound_scalper.evaluate_exit(current_mcap_usd=140_000)  # 1.4x -- never hit target or stop
    assert d.should_exit is True
    assert d.exit_type == "time_stop"


def test_check_and_manage_full_close_reopens_pool_for_next_entry(monkeypatch):
    _open_test_position(entry_mcap=100_000, position_usd=22.5, amount_tokens=1000.0)
    sell_result = ExecutionResult(True, "ok", tx_signature="sig456", filled_usd=10.0, filled_amount_tokens=None)
    monkeypatch.setattr(swap_executor, "execute_sell", lambda **kwargs: sell_result)

    result = compound_scalper.check_and_manage(current_mcap_usd=65_000)  # hard stop
    assert result["sell_attempted"] is True

    pool = compound_scalper._get_pool()
    assert pool["open_position"] is None  # fully closed, ready for next entry
    assert pool["balance_usd"] == pytest.approx((30.0 - 22.5) + 10.0)
    assert pool["consecutive_losses"] == 1  # a loss (10.0 filled vs 22.5 cost basis)
    assert len(pool["trades"]) == 1

    # pool is open for a fresh entry again now
    d = compound_scalper.entry_gate("solana", "MintB", "A", liquidity_usd=500_000)
    assert d.should_fire is True


def test_check_and_manage_partial_tp_leaves_position_open(monkeypatch):
    _open_test_position(entry_mcap=100_000, position_usd=22.5, amount_tokens=1000.0)
    sell_result = ExecutionResult(True, "ok", tx_signature="sig789", filled_usd=35.0, filled_amount_tokens=None)
    monkeypatch.setattr(swap_executor, "execute_sell", lambda **kwargs: sell_result)

    result = compound_scalper.check_and_manage(current_mcap_usd=260_000)  # 2.6x, take-profit partial
    assert result["exit_decision"].exit_type == "take_profit_partial"

    pool = compound_scalper._get_pool()
    assert pool["open_position"] is not None  # remainder still riding
    assert pool["open_position"]["partial_tp_done"] is True
    assert pool["open_position"]["amount_tokens"] == pytest.approx(1000.0 * 0.40)
    assert pool["balance_usd"] == pytest.approx((30.0 - 22.5) + 35.0)


def test_consecutive_losses_trip_circuit(monkeypatch):
    loss_result = ExecutionResult(True, "ok", tx_signature="s", filled_usd=5.0, filled_amount_tokens=None)
    monkeypatch.setattr(swap_executor, "execute_sell", lambda **kwargs: loss_result)

    for _ in range(3):
        _open_test_position(entry_mcap=100_000, position_usd=22.5, amount_tokens=1000.0)
        compound_scalper.check_and_manage(current_mcap_usd=65_000)  # hard stop, a loss each time

    pool = compound_scalper._get_pool()
    assert pool["tripped"] is True
    assert "consecutive losses" in pool["tripped_reason"]

    d = compound_scalper.entry_gate("solana", "MintNext", "A", liquidity_usd=500_000)
    assert d.should_fire is False
    assert "tripped" in d.reason


# --- status / lifecycle -----------------------------------------------

def test_status_before_start():
    s = compound_scalper.status()
    assert s["started"] is False
    assert s["balance_usd"] is None


def test_status_after_start_and_a_win(monkeypatch):
    compound_scalper.init_pool(seed_usd=30.0)
    _open_test_position(entry_mcap=100_000, position_usd=22.5, amount_tokens=1000.0)
    win_result = ExecutionResult(True, "ok", tx_signature="s", filled_usd=58.5, filled_amount_tokens=None)
    monkeypatch.setattr(swap_executor, "execute_sell", lambda **kwargs: win_result)
    compound_scalper.check_and_manage(current_mcap_usd=260_000)  # take-profit partial, a real win

    s = compound_scalper.status()
    assert s["started"] is True
    assert s["realized_pnl_usd"] > 0
    assert s["consecutive_losses"] == 0


def test_reset_pool_clears_everything():
    compound_scalper.init_pool(seed_usd=30.0)
    compound_scalper.reset_pool()
    s = compound_scalper.status()
    assert s["started"] is False
    assert s["balance_usd"] is None


def test_init_pool_is_idempotent_without_force():
    compound_scalper.init_pool(seed_usd=30.0)
    first = compound_scalper._get_pool()["session_start_ts"]
    compound_scalper.init_pool(seed_usd=99.0)  # should be ignored -- session already live
    second_pool = compound_scalper._get_pool()
    assert second_pool["session_start_ts"] == first
    assert second_pool["seed_usd"] == 30.0


def test_scalper_defaults_are_the_backtested_settings():
    from executor.compound_scalper import CompoundScalperConfig as ScalperConfig
    import os
    for k in ("COMPOUND_TAKE_PROFIT_MULTIPLE", "COMPOUND_TRAIL_STOP_PCT",
              "COMPOUND_HARD_STOP_PCT", "COMPOUND_TIME_STOP_MINUTES"):
        os.environ.pop(k, None)
    c = ScalperConfig()
    assert (c.hard_stop_pct, c.take_profit_multiple, c.time_stop_minutes, c.trail_stop_pct) == (0.55, 1.5, 180.0, 0.35)

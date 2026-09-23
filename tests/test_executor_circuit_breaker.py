import pytest

import state
import executor.circuit_breaker as circuit_breaker
from executor.config import EXECUTOR_CONFIG


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def test_not_tripped_initially():
    assert circuit_breaker.is_tripped()["tripped"] is False


def test_trips_on_consecutive_losses():
    for _ in range(EXECUTOR_CONFIG.max_consecutive_losses):
        circuit_breaker.record_trade_result(-3.0)
    status = circuit_breaker.is_tripped()
    assert status["tripped"] is True
    assert "consecutive losses" in status["reason"]


def test_win_resets_consecutive_loss_counter():
    circuit_breaker.record_trade_result(-3.0)
    circuit_breaker.record_trade_result(-3.0)
    circuit_breaker.record_trade_result(5.0)  # win resets counter
    circuit_breaker.record_trade_result(-3.0)
    circuit_breaker.record_trade_result(-3.0)
    # only 2 consecutive losses since the win -- should not trip on count alone
    assert circuit_breaker.is_tripped()["tripped"] is False


def test_trips_on_session_loss_pct():
    # total_wallet_usd defaults to 100.0 -- 30% cap means -30 trips it
    circuit_breaker.record_trade_result(-15.0)
    circuit_breaker.record_trade_result(-16.0)
    status = circuit_breaker.is_tripped()
    assert status["tripped"] is True
    assert "session loss" in status["reason"]


def test_reset_session_clears_trip():
    for _ in range(EXECUTOR_CONFIG.max_consecutive_losses):
        circuit_breaker.record_trade_result(-3.0)
    assert circuit_breaker.is_tripped()["tripped"] is True
    circuit_breaker.reset_session()
    assert circuit_breaker.is_tripped()["tripped"] is False

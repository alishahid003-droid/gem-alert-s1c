"""Checklist 5.3 retry-once, 5.5 Telegram trade/stall alerts, 5.6 daily summary."""
import time
import pytest

import state
from executor import trade_ops
from executor.swap_executor import ExecutionResult


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    sent = []
    monkeypatch.setattr(trade_ops, "_send", lambda text: sent.append(text) or {"sent": True})
    monkeypatch.setattr(trade_ops.time, "sleep", lambda s: None)
    yield sent


def test_transient_classification():
    assert trade_ops.is_transient(ExecutionResult(False, "jupiter quote failed: status 503"))
    assert trade_ops.is_transient(ExecutionResult(False, "jupiter swap-tx build failed: status 500"))
    assert not trade_ops.is_transient(ExecutionResult(False, "sent but could not confirm (check signature manually): x",
                                                      tx_signature="sig"))
    assert not trade_ops.is_transient(ExecutionResult(False, "refused before buying: honeypot"))
    assert not trade_ops.is_transient(ExecutionResult(False, "sendTransaction failed: timeout"))
    assert not trade_ops.is_transient(ExecutionResult(True, "ok"))


def test_retry_once_then_notify(iso):
    calls = []

    def buy(token, usd):
        calls.append(1)
        return ExecutionResult(len(calls) == 2, "jupiter quote failed: status 503" if len(calls) == 1 else "ok",
                               tx_signature="S" if len(calls) == 2 else None, filled_usd=usd)
    r = trade_ops.hardened("buy", "solana")(buy)("MINT", 2.0)
    assert r.ok and len(calls) == 2
    assert "REAL BUY solana" in iso[0] and "retried once" in iso[0] and "MINT" in iso[0]


# ---- 15 Nov 2026 (Ali, explicit instruction -- real-time execution-speed visibility: "we
# might lose the edge" on slow memecoin execution) -----------------------------------------

def test_hardened_sets_elapsed_seconds_from_real_wall_clock(iso, monkeypatch):
    """hardened() must time the REAL wall-clock duration of the wrapped call and stamp it
    onto the result as elapsed_seconds, so the dashboard can show actual on-chain
    submit-to-confirm latency per trade instead of nothing."""
    fake_now = [1000.0]
    def fake_time():
        fake_now[0] += 0.5  # each call() to time.time() advances the clock by 0.5s
        return fake_now[0]
    monkeypatch.setattr(trade_ops.time, "time", fake_time)

    def buy(token, usd):
        return ExecutionResult(True, "ok", tx_signature="S", filled_usd=usd)
    r = trade_ops.hardened("buy", "robinhood_chain")(buy)("TOKEN", 2.0)
    assert r.ok
    assert r.elapsed_seconds is not None and r.elapsed_seconds > 0
    assert "⏱" in iso[0]  # the timer emoji -- confirms elapsed time reached the Telegram message too


def test_hardened_does_not_overwrite_an_already_set_elapsed_seconds(iso):
    """If a wrapped function ever sets its own, more precise elapsed_seconds (none currently
    do, but this is the documented contract), hardened() must not clobber it with its own
    coarser outer-call timing."""
    def buy(token, usd):
        return ExecutionResult(True, "ok", tx_signature="S", filled_usd=usd, elapsed_seconds=3.33)
    r = trade_ops.hardened("buy", "base")(buy)("TOKEN", 2.0)
    assert r.elapsed_seconds == 3.33


def test_execute_buy_base_is_hardened():
    """15 Nov 2026: execute_buy_base was the one real buy path never wrapped with hardened()
    (solana/bsc/robinhood_chain all were, since Sept 30 2026 -- base was added later, 3 Nov
    2026, and missed it). A transient failure on a Base buy was silently never retried the
    way every other chain's buy already is. Fixed alongside the timing instrumentation since
    the fix lives in the same wrapping call -- this test guards it stays fixed."""
    from executor import swap_executor
    assert getattr(swap_executor.execute_buy_base, "__wrapped_once__", False) is True


def test_never_retries_after_send(iso):
    calls = []

    def buy(token, usd):
        calls.append(1)
        return ExecutionResult(False, "sent but could not confirm (check signature manually): x", tx_signature="S")
    trade_ops.hardened("buy", "bsc")(buy)("0xT", 2.0)
    assert len(calls) == 1 and "FAILED" in iso[0]


def test_disabled_refusal_is_quiet(iso):
    fn = trade_ops.hardened("buy", "solana")(lambda t, u: ExecutionResult(False, "EXECUTION_ENABLED is not 'true' -- x"))
    fn("M", 1)
    assert iso == []


def test_sell_kwargs_token(iso):
    fn = trade_ops.hardened("sell")(lambda chain, token_address, amount_tokens, reason:
                                    ExecutionResult(True, "sold", filled_usd=3.0))
    fn(chain="solana", token_address="MINTX", amount_tokens=5, reason="tp")
    assert "REAL SELL solana" in iso[0] and "MINTX" in iso[0]


def test_stalled_runner_alert_throttled(iso):
    now = time.time()
    state.record_runner_heartbeat("fast-watch", "pc", "x", ts=now - 900)
    state.record_runner_heartbeat("poll-fast", "gh", "x", ts=now - 60)
    assert trade_ops.check_stalled_runners(now) == ["fast-watch"]
    assert len(iso) == 1 and "fast-watch" in iso[0]
    trade_ops.check_stalled_runners(now + 600)          # within 2 h: no repeat
    assert len(iso) == 1


def test_daily_summary_once_per_day(iso):
    now = time.time()
    assert trade_ops.maybe_send_daily_summary(now) is True
    assert trade_ops.maybe_send_daily_summary(now + 60) is False
    assert "Daily summary" in iso[0] and "MadeOnSol" in iso[0]

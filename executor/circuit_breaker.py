"""
Session circuit breaker -- stops new auto-buys after a bad run, so one rough
night doesn't burn through the whole automation wallet in back-to-back bad
entries before Ali wakes up. Replaces the "bounded wallet" idea Ali correctly
pointed out is redundant when the wallet's already only $50-100 -- this is
the safeguard that's actually still useful regardless of wallet size.

"Session" = one calendar day (UTC), resets at midnight. Simple on purpose --
a more elaborate rolling window isn't needed at this trade volume.
"""
import time
from datetime import datetime, timezone

import state
from executor.config import EXECUTOR_CONFIG


def _session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session_key() -> str:
    return f"circuit_breaker:{_session_id()}"


def _get_session() -> dict:
    return state.get_value(_session_key()) or {
        "session_id": _session_id(), "trades": [], "consecutive_losses": 0,
        "realized_pnl_usd": 0.0, "tripped": False, "tripped_reason": None,
    }


def _save_session(session: dict):
    state.set_value(_session_key(), session)


def record_trade_result(pnl_usd: float):
    """Call this once a position closes (win or loss) so the breaker can
    react. Positive pnl_usd resets the consecutive-loss counter; negative
    increments it."""
    session = _get_session()
    session["trades"].append({"pnl_usd": pnl_usd, "ts": time.time()})
    session["realized_pnl_usd"] += pnl_usd
    if pnl_usd < 0:
        session["consecutive_losses"] += 1
    else:
        session["consecutive_losses"] = 0
    _evaluate_trip(session)
    _save_session(session)
    return session


def _evaluate_trip(session: dict):
    if session["consecutive_losses"] >= EXECUTOR_CONFIG.max_consecutive_losses:
        session["tripped"] = True
        session["tripped_reason"] = (
            f"{session['consecutive_losses']} consecutive losses "
            f"(cap: {EXECUTOR_CONFIG.max_consecutive_losses})"
        )
        return
    loss_pct = -session["realized_pnl_usd"] / EXECUTOR_CONFIG.total_wallet_usd if EXECUTOR_CONFIG.total_wallet_usd else 0
    if loss_pct >= EXECUTOR_CONFIG.max_session_loss_pct:
        session["tripped"] = True
        session["tripped_reason"] = (
            f"session loss {loss_pct*100:.1f}% of wallet "
            f"(cap: {EXECUTOR_CONFIG.max_session_loss_pct*100:.0f}%)"
        )


def is_tripped() -> dict:
    """Returns {"tripped": bool, "reason": str or None}. Callers (triggers.py)
    must check this before firing any new auto-buy."""
    session = _get_session()
    return {"tripped": session.get("tripped", False), "reason": session.get("tripped_reason")}


def reset_session():
    """Manual override -- not called automatically. Ali reviewing a tripped
    breaker and deciding to resume is a deliberate action, not something
    that resets itself even at UTC midnight rollover beyond starting a fresh
    session key (yesterday's trip doesn't carry into today, but today's own
    losses still trip it independently)."""
    state.set_value(_session_key(), {
        "session_id": _session_id(), "trades": [], "consecutive_losses": 0,
        "realized_pnl_usd": 0.0, "tripped": False, "tripped_reason": None,
    })

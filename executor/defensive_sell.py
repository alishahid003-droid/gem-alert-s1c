"""
Defensive auto-sell -- the "point 2" piece Ali confirmed he wants: if
Layer 6's own rug-in-progress detection fires on a position THIS EXECUTOR
opened, sell it immediately instead of waiting for a manual decision made
possibly hours later while Ali's asleep. Profit-taking stays manual --
this only ever fires on the defensive/exit-risk side, never to lock in
gains.

Reuses layers.layer6_exit_realizable.detect_exit_risk exactly as built for
the alert system -- no new rug-detection logic, just a new consumer of it
that calls execute_sell instead of only sending a Telegram message.
"""
from typing import Optional

import executor.position_state as position_state
import executor.swap_executor as swap_executor
import executor.circuit_breaker as circuit_breaker
from layers.layer6_exit_realizable import detect_exit_risk


def check_and_defend(chain: str, token: str, prev_signals, curr_signals,
                      is_high_risk_momentum: bool = False) -> Optional[dict]:
    """Call this each poll cycle for every open position this executor
    holds (position_state.list_open_positions()), passing that token's
    prev/curr RawSignals the same way Layer 6's alert path already gets
    them. Returns None if nothing fired, or a dict describing what happened
    if a defensive sell was attempted."""
    pos = position_state.get_position(chain, token)
    if not pos or pos.get("status") != "open":
        return None  # not a position this executor opened, or already closed

    reasons = detect_exit_risk(prev_signals, curr_signals, is_high_risk_momentum)
    if not reasons:
        return None

    # Rug signal fired -- attempt an immediate sell of the full position.
    result = swap_executor.execute_sell(
        chain=chain, token_address=token,
        amount_tokens=pos.get("amount_tokens", 0),  # populated once buy execution actually records fills
        reason="; ".join(reasons),
    )

    if result.ok:
        position_state.close_position(chain, token, reason="defensive_sell: " + "; ".join(reasons),
                                       exit_usd=result.filled_usd)
        if result.filled_usd is not None:
            circuit_breaker.record_trade_result(result.filled_usd - pos.get("total_usd", 0))

    return {
        "chain": chain, "token": token, "rug_reasons": reasons,
        "sell_attempted": True, "sell_result": result,
    }

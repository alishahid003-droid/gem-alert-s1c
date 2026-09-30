"""
Tracked-trader exit mirror -- GO_LIVE_CHECKLIST 4.5 (Sept 30 2026).

When a Fomo roster trader whose buy we copied (Layer 13 roster convergence
-> Stage 2) sells that coin, we sell what we still hold of it. The trader's
exit is the thesis ending; holding on after the people we followed have left
is how a copy trade turns into a bag.

Only positions this executor opened are touched, and only when the seller
is one of the roster traders recorded buying that exact coin (or named in
the position's entry reason) -- a random roster trader selling some coin we
happen to hold for another reason does not trigger it.
"""
from typing import List, Optional

import state
import executor.position_state as position_state
import executor.swap_executor as swap_executor
import executor.circuit_breaker as circuit_breaker


def _buyers_of(chain: str, mint: str) -> set:
    data = state.get_value(state.FOMO_ROSTER_BUYS_KEY) or {}
    return {e.get("trader") for e in data.get(f"{chain}:{mint}", []) if e.get("trader")}


def mirror_sells(roster_sells: List[dict]) -> List[dict]:
    """roster_sells: [{chain, mint, trader}] from Layer 13's detector.
    Returns one record per sell attempted."""
    out = []
    done = set()
    for s in roster_sells or []:
        chain, mint, trader = s.get("chain"), s.get("mint"), s.get("trader")
        if not (chain and mint and trader) or (chain, mint) in done:
            continue
        pos = position_state.get_position(chain, mint)
        if not pos or pos.get("status") != "open":
            continue
        reasons = " ".join(str(st.get("reason") or "") for st in (pos.get("stages") or {}).values())
        if trader not in _buyers_of(chain, mint) and trader not in reasons:
            continue
        done.add((chain, mint))
        why = f"copy-exit: {trader} (a trader we copied) sold"
        amount = (pos.get("amount_tokens") or 0) * position_state.remaining_pct(chain, mint)
        if amount <= 0:
            out.append({"chain": chain, "mint": mint, "trader": trader, "sell_attempted": False,
                        "reason": "no token amount recorded for this position"})
            continue
        result = swap_executor.execute_sell(chain=chain, token_address=mint, amount_tokens=amount, reason=why)
        if result.ok:
            position_state.close_position(chain, mint, reason=why, exit_usd=result.filled_usd)
            if result.filled_usd is not None:
                circuit_breaker.record_trade_result(result.filled_usd - pos.get("total_usd", 0))
        out.append({"chain": chain, "mint": mint, "trader": trader, "sell_attempted": True,
                    "ok": result.ok, "reason": result.reason})
    return out

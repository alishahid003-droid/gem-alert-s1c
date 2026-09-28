"""
Sequential real-dollar campaign milestones -- built live Sept 28 2026 from
Ali's direct instruction: "if you see any of my moonshot position in the
next one week reaching 25k$ u auto close that...then once this is
acheived if you again see any of my posiiton reaching 150k$ you autoclose
and take the amount...then after this 2 targets achieved in future if my
any moonshot position reaches band of 500k to 1m$ profit u set a fire of
alerts to me and we close that position together."

This is deliberately a DIFFERENT mechanism from moonbag.py's per-position
trim ladders. Moonbag trims a FRACTION of one position at MULTIPLE
checkpoints (3x/10x/50x) and always leaves a moonbag riding uncapped --
it never fully closes a position on its own. This module instead tracks
ONE GLOBAL, ACCOUNT-WIDE, SEQUENTIAL campaign across ALL open positions:

  1. The first open position (any chain/token) whose current USD value
     reaches $25,000 gets FULLY closed automatically -- 100% of the
     remaining tokens sold, not a trim. This target can only be reached
     once; after it fires, target 2 unlocks.
  2. Only once target 1 has fired: the first open position whose current
     USD value reaches $150,000 gets FULLY closed automatically the same
     way. This can also only fire once; after it fires, target 3 unlocks.
  3. Only once targets 1 and 2 have both fired: any open position whose
     current USD value enters the $500,000-$1,000,000 band triggers a
     Telegram alert ONLY -- no auto-close. Ali explicitly wants to be in
     the loop for that decision ("we close that position together"), so
     this never sells anything at that tier. Each qualifying position is
     only alerted once (not re-alerted every cycle while it sits in the
     band), but a DIFFERENT position later reaching the band gets its own
     alert.

These are truly global/account-level flags, not per-position state, so
they live under their own state.py keys (same get_value/set_value backend
executor/position_state.py already uses -- Upstash when configured, local
JSON otherwise) rather than inside any one position's record.

Position "current USD value" is computed the same way moonbag.py computes
a position's achieved multiple -- entry_mcap (Stage 1's if it fired, else
Stage 2's) versus a current_mcap_usd the caller supplies each cycle --
scaled by what fraction of the original stake is still open
(position_state.remaining_pct, which already accounts for any moonbag
trims fired earlier in the SAME cycle, since scheduler.py runs
moonbag.check_and_trim before this module each cycle -- see
scheduler.py's _run_position_management_cycle). This deliberately reuses
entry_mcap/current_mcap_usd rather than inventing a second "how big is
this position" notion.

Fail-closed throughout: any missing/unparseable data (no entry_mcap, no
current_mcap_usd) means this cycle can't tell whether a target was
reached, so it does nothing rather than guessing -- exactly the posture
the rest of this codebase already uses everywhere else.
"""
from typing import Optional

import executor.position_state as position_state
import executor.swap_executor as swap_executor
import executor.circuit_breaker as circuit_breaker
import telegram_alert
import state

TARGET_1_USD = 25_000.0
TARGET_2_USD = 150_000.0
ALERT_BAND_LOW_USD = 500_000.0
ALERT_BAND_HIGH_USD = 1_000_000.0

_STATE_KEY_TARGET_1 = "campaign_milestone_25k_achieved"
_STATE_KEY_TARGET_2 = "campaign_milestone_150k_achieved"
_STATE_KEY_BAND_ALERTED = "campaign_milestone_500k_1m_alerted"  # dict: "chain:token" -> True


def _entry_mcap(pos: dict) -> Optional[float]:
    """Same baseline rule as moonbag._entry_mcap -- duplicated (not
    imported) because it's three lines and this module should stay
    readable standalone; both must stay in sync if that rule ever
    changes."""
    stages = pos.get("stages", {})
    if "stage1" in stages and stages["stage1"].get("entry_mcap"):
        return stages["stage1"]["entry_mcap"]
    if "stage2" in stages and stages["stage2"].get("entry_mcap"):
        return stages["stage2"]["entry_mcap"]
    return None


def position_value_usd(chain: str, token: str, current_mcap_usd: Optional[float]) -> Optional[float]:
    """What this open position is worth right now, in real dollars --
    original stake * multiple achieved * fraction still un-trimmed.
    Returns None (not a guess) if there's no open position or no usable
    entry_mcap/current_mcap_usd to compute a multiple from."""
    pos = position_state.get_position(chain, token)
    if not pos or pos.get("status") != "open":
        return None
    entry_mcap = _entry_mcap(pos)
    if not entry_mcap or not current_mcap_usd or entry_mcap <= 0:
        return None
    multiple = current_mcap_usd / entry_mcap
    stake_usd = pos.get("total_usd", 0.0)
    remaining = position_state.remaining_pct(chain, token)
    return stake_usd * multiple * remaining


def _full_close(chain: str, token: str, pos: dict, value_usd: float, label: str) -> dict:
    amount_tokens = pos.get("amount_tokens", 0)
    result = swap_executor.execute_sell(
        chain=chain, token_address=token, amount_tokens=amount_tokens,
        reason=f"campaign milestone {label}: position reached ~${value_usd:,.0f}",
    )
    if result.ok:
        position_state.close_position(chain, token, reason=f"campaign_milestone_{label}",
                                       exit_usd=result.filled_usd)
        if result.filled_usd is not None:
            cost_basis_remaining = pos.get("total_usd", 0.0) * position_state.remaining_pct(chain, token)
            circuit_breaker.record_trade_result(result.filled_usd - cost_basis_remaining)
    telegram_alert.send_telegram_message(
        f"*Campaign milestone {label} hit*\n"
        f"`{token}` [{chain}] reached ~${value_usd:,.0f} -- auto-close "
        f"{'executed' if result.ok else 'ATTEMPTED but failed: ' + (result.reason or 'unknown')}."
    )
    return {"chain": chain, "token": token, "action": f"auto_close_{label}",
            "value_usd": value_usd, "sell_result": result}


def check_and_apply(chain: str, token: str, current_mcap_usd: Optional[float]) -> Optional[dict]:
    """Call once per open position per cycle (scheduler.py's
    _run_position_management_cycle does, alongside moonbag.check_and_trim
    and defensive_sell.check_and_defend). Returns None if no milestone
    action fired this cycle, otherwise a dict describing what happened."""
    pos = position_state.get_position(chain, token)
    if not pos or pos.get("status") != "open":
        return None

    value_usd = position_value_usd(chain, token, current_mcap_usd)
    if value_usd is None:
        return None

    target_1 = state.get_value(_STATE_KEY_TARGET_1)
    if target_1 is None:
        if value_usd >= TARGET_1_USD:
            outcome = _full_close(chain, token, pos, value_usd, "target1_25k")
            state.set_value(_STATE_KEY_TARGET_1, {
                "chain": chain, "token": token, "value_usd": value_usd,
            })
            return outcome
        return None

    target_2 = state.get_value(_STATE_KEY_TARGET_2)
    if target_2 is None:
        if value_usd >= TARGET_2_USD:
            outcome = _full_close(chain, token, pos, value_usd, "target2_150k")
            state.set_value(_STATE_KEY_TARGET_2, {
                "chain": chain, "token": token, "value_usd": value_usd,
            })
            return outcome
        return None

    if ALERT_BAND_LOW_USD <= value_usd <= ALERT_BAND_HIGH_USD:
        alerted = state.get_value(_STATE_KEY_BAND_ALERTED) or {}
        pos_key = f"{chain}:{token}"
        if not alerted.get(pos_key):
            telegram_alert.send_telegram_message(
                f"*Moonshot in the $500k-$1M band*\n"
                f"`{token}` [{chain}] is now worth ~${value_usd:,.0f}. Both earlier "
                f"campaign targets ($25k, $150k) already hit -- this one is HELD, no "
                f"auto-close. Decide together and close manually when ready."
            )
            alerted[pos_key] = True
            state.set_value(_STATE_KEY_BAND_ALERTED, alerted)
            return {"chain": chain, "token": token, "action": "alert_500k_1m_band", "value_usd": value_usd}
    return None

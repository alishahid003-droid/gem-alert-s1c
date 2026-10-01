"""
Exit discipline (GO_LIVE_CHECKLIST Phase 4, Sept 30 2026).

Before this module, a Stage 1/2 position could only leave via the moonbag
ladder (3x/10x/50x profit trims) or defensive_sell (a detected rug). A coin
that simply bled out -90% with no rug signal was held forever, a dead coin
tied up budget indefinitely, and a coin that ran +80% and round-tripped to
zero ended as a full loss. That exit gap -- not entry quality -- is the
single biggest thing standing between this system and a high win rate.

Four rules, evaluated every management cycle, in priority order:

  1. HARD STOP-LOSS  -- mcap <= entry * (1 - STOP_LOSS_PCT): exit everything
     left. Caps the loss on any single trade.
  2. BREAKEVEN LOCK  -- first time mcap >= entry * BREAKEVEN_TRIGGER_MULT:
     sell just enough of the ORIGINAL position to recover the whole stake
     plus round-trip costs. The trade can no longer finish as a loss; the
     rest rides for free under the moonbag ladder (rescaled to what's left).
  3. TRAILING STOP   -- once the peak reached TRAIL_ARM_MULT, exit everything
     left if mcap falls TRAIL_GIVEBACK_PCT below that peak. Keeps most of a
     big move instead of round-tripping it.
  4. TIME STOP       -- after TIME_STOP_MINUTES, if the peak never reached
     TIME_STOP_MIN_MULT, exit: dead coins free their budget slot.

Everything here is PURE (no network, no state writes): evaluate_exit takes
a position snapshot and returns a decision. executor/position_state
positions (real money) and executor/paper_ledger positions (paper) both
apply the SAME decisions -- so paper win rates measure exactly the logic
real money will run. All thresholds are env-tunable and will be tuned from
paper-ledger data (Phase 2), not guessed forever.
"""
import os
from dataclasses import dataclass
from typing import Optional


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


# Defaults from backtest_trades.py's grid on the 24 labeled coins (Sept 30
# 2026): stop -55% / lock 1.5x / trail 50% scored 75% wins (79% with the
# entry filter at +15 min) vs 42% for the first -25% / 35% settings --
# memecoins routinely dip 30-50% before running, and the tight stop sold
# those dips. 24 coins overfit easily: the paper ledger runs these same
# numbers live and is the real verdict (checklist 2.8).
def stop_loss_pct() -> float:            return _f("STOP_LOSS_PCT", 0.55)
def breakeven_trigger_mult() -> float:   return _f("BREAKEVEN_TRIGGER_MULT", 1.5)
def trail_arm_mult() -> float:           return _f("TRAIL_ARM_MULT", 2.0)
def trail_giveback_pct() -> float:       return _f("TRAIL_GIVEBACK_PCT", 0.50)
def time_stop_minutes() -> float:        return _f("TIME_STOP_MINUTES", 90.0)
def time_stop_min_mult() -> float:       return _f("TIME_STOP_MIN_MULT", 1.2)

# Moonshot runner (Sept 30 2026, Ali: "1500 dollars cashed out a million --
# how are we going to be the ones benefiting from that?"). Before this, the
# 50% trailing stop sold EVERYTHING left -- including the moonbag -- at the
# first 50% pullback, and every 100x-1000x memecoin run has several of those
# on the way up. Once the breakeven lock has taken the stake back, a runner
# slice (MOONSHOT_RUNNER_PCT of the original) is exempt from the normal trail
# and time stop. It only leaves via: rug detection / liquidity collapse, the
# moonbag ladder's 10x/50x trims, a DEEP trail once it has really run
# (RUNNER_TRAIL_ARM_MULT, give back RUNNER_TRAIL_GIVEBACK_PCT from peak), or
# falling to RUNNER_FLOOR_MULT of our entry (thesis dead; stake already safe).
def moonshot_runner_pct() -> float:      return _f("MOONSHOT_RUNNER_PCT", 0.25)
def runner_trail_arm_mult() -> float:    return _f("RUNNER_TRAIL_ARM_MULT", 5.0)
def runner_trail_giveback() -> float:    return _f("RUNNER_TRAIL_GIVEBACK_PCT", 0.75)
def runner_floor_mult() -> float:        return _f("RUNNER_FLOOR_MULT", 0.5)


# Exit profile for Layer 15 moonshot entries: the stake comes back at 2x
# (not 1.5x), then HALF the original position rides as the runner.
def _profile_breakeven_mult(profile: Optional[str]) -> float:
    return _f("MOONSHOT_BREAKEVEN_MULT", 2.0) if profile == "moonshot" else breakeven_trigger_mult()


def runner_pct_for(profile: Optional[str]) -> float:
    return _f("MOONSHOT_SLEEVE_RUNNER_PCT", 0.5) if profile == "moonshot" else moonshot_runner_pct()


@dataclass
class ExitDecision:
    action: str                 # "hold" | "sell_partial" | "exit_all"
    exit_type: str = ""         # "stop_loss" | "breakeven_lock" | "trailing_stop" | "time_stop"
    pct_of_original: float = 0  # for sell_partial
    reason: str = ""


def breakeven_sell_fraction(multiple: float, round_trip_cost_pct: float) -> float:
    """Fraction of the ORIGINAL position whose sale at `multiple` returns the
    whole stake plus costs. Capped at 0.9 so something always rides."""
    if multiple <= 0:
        return 0.0
    return min(0.9, (1.0 + round_trip_cost_pct) / multiple)


def evaluate_exit(entry_mcap: Optional[float], current_mcap: Optional[float],
                  peak_mcap: Optional[float], opened_ts: float, now_ts: float,
                  remaining_pct: float, breakeven_locked: bool,
                  round_trip_cost_pct: float = 0.03, profile: Optional[str] = None) -> ExitDecision:
    if not entry_mcap or not current_mcap or entry_mcap <= 0 or remaining_pct <= 0:
        return ExitDecision("hold", reason="no price or nothing left")
    mult = current_mcap / entry_mcap
    peak_mult = max(mult, (peak_mcap or current_mcap) / entry_mcap)

    if mult <= 1.0 - stop_loss_pct():
        return ExitDecision("exit_all", "stop_loss",
                            reason=f"{(mult - 1) * 100:.0f}% from entry hit the -{stop_loss_pct() * 100:.0f}% stop")

    if not breakeven_locked and mult >= _profile_breakeven_mult(profile):
        frac = min(breakeven_sell_fraction(mult, round_trip_cost_pct), remaining_pct)
        return ExitDecision("sell_partial", "breakeven_lock", pct_of_original=frac,
                            reason=f"{mult:.2f}x: sold {frac * 100:.0f}% to recover the full stake -- rest rides free")

    runner = min(runner_pct_for(profile), remaining_pct) if breakeven_locked else 0.0
    tradeable = remaining_pct - runner
    if runner > 0 and tradeable <= 1e-6:
        # Only the free-ride runner is left.
        if peak_mult >= runner_trail_arm_mult() and mult <= peak_mult * (1.0 - runner_trail_giveback()):
            return ExitDecision("exit_all", "runner_trail",
                                reason=f"runner fell {(1 - mult / peak_mult) * 100:.0f}% from its {peak_mult:.1f}x peak")
        if mult <= runner_floor_mult():
            return ExitDecision("exit_all", "runner_faded",
                                reason=f"runner back to {mult:.2f}x of entry -- move is over (stake was already recovered)")
        return ExitDecision("hold", reason=f"runner riding {mult:.2f}x (peak {peak_mult:.2f}x)")

    if peak_mult >= trail_arm_mult() and mult <= peak_mult * (1.0 - trail_giveback_pct()):
        why = f"fell {(1 - mult / peak_mult) * 100:.0f}% from its {peak_mult:.1f}x peak"
        if runner > 0:
            return ExitDecision("sell_partial", "trailing_stop", pct_of_original=tradeable,
                                reason=why + f" -- sold the rest, {runner * 100:.0f}% runner keeps riding")
        return ExitDecision("exit_all", "trailing_stop", reason=why)

    age_min = (now_ts - opened_ts) / 60.0
    if age_min >= time_stop_minutes() and peak_mult < time_stop_min_mult():
        if runner > 0:
            return ExitDecision("sell_partial", "time_stop", pct_of_original=tradeable,
                                reason=f"{age_min:.0f} min without reaching {time_stop_min_mult():.1f}x -- runner kept")
        return ExitDecision("exit_all", "time_stop",
                            reason=f"{age_min:.0f} min without reaching {time_stop_min_mult():.1f}x (peak {peak_mult:.2f}x)")

    return ExitDecision("hold", reason=f"{mult:.2f}x (peak {peak_mult:.2f}x)")


def check_and_exit(chain: str, token: str, current_mcap: Optional[float],
                   liquidity_usd: Optional[float] = None, now_ts: Optional[float] = None) -> Optional[dict]:
    """Real-money application for an executor position (same shape as
    moonbag.check_and_trim / defensive_sell.check_and_defend). Returns None
    when nothing fired. Sells go through swap_executor.execute_sell, which
    stays inert until EXECUTION_ENABLED -- exactly like every other sell path."""
    import time
    import executor.position_state as position_state
    import executor.swap_executor as swap_executor
    import executor.circuit_breaker as circuit_breaker
    from executor.compound_scalper import estimate_round_trip_cost_pct
    from executor.moonbag import _entry_mcap

    pos = position_state.get_position(chain, token)
    if not pos or pos.get("status") != "open" or not pos.get("amount_tokens"):
        return None  # nothing actually held (execution off, or buy never filled)
    now_ts = now_ts if now_ts is not None else time.time()
    from executor.price_sanity import accept
    if not accept(chain, token, current_mcap):
        return None             # data glitch filtered -- never sell on a fake spike or dip
    peak = position_state.update_peak_mcap(chain, token, current_mcap)
    remaining = position_state.remaining_pct(chain, token)
    cost = estimate_round_trip_cost_pct(chain, pos.get("total_usd") or 0, liquidity_usd)
    decision = evaluate_exit(_entry_mcap(pos), current_mcap, peak, pos.get("opened_ts") or now_ts,
                             now_ts, remaining, bool(pos.get("breakeven_locked")), cost,
                             profile=pos.get("exit_profile"))
    if decision.action == "hold":
        return None

    pct = remaining if decision.action == "exit_all" else decision.pct_of_original
    result = swap_executor.execute_sell(chain=chain, token_address=token,
                                        amount_tokens=pos["amount_tokens"] * pct,
                                        reason=f"{decision.exit_type}: {decision.reason}")
    if result.ok:
        cost_basis = (pos.get("total_usd") or 0) * pct
        if decision.action == "exit_all":
            position_state.close_position(chain, token, reason=f"{decision.exit_type}: {decision.reason}",
                                          exit_usd=result.filled_usd)
        else:
            position_state.record_moonbag_trim(chain, token, f"{decision.exit_type}@{int(now_ts)}", pct,
                                               result.filled_usd)
            if decision.exit_type == "breakeven_lock":
                position_state.mark_breakeven_locked(chain, token, pct)
        if result.filled_usd is not None:
            circuit_breaker.record_trade_result(result.filled_usd - cost_basis)
    return {"chain": chain, "token": token, "decision": decision, "sell_result": result}

"""
Moonbag trimming -- the piece the pure downside-only executor was missing
(Ali, Sept 22, 2026): S1c's actual purpose isn't to compound a fixed dollar
target in a fixed window, it's to take many small, cheap, filtered shots and
be sized right on the one that runs to a real outlier (hundreds of millions
mcap). defensive_sell.py already protects the downside (rug detected -> full
exit). This module is the upside counterpart: as a position multiplies, sell
off pre-defined slices to recover capital and lock in gains, while leaving a
deliberately un-touched remainder -- the "moonbag" -- to keep riding with no
cap, for as long as the position stays open.

The moonbag itself is never sold by this module. It only ever exits via
defensive_sell.py's rug detection, or a manual close. That's the point --
this module de-risks, it does not take profit on the whole position.

Ladder is a fixed list of (multiple_vs_entry, pct_of_ORIGINAL_position) pairs,
evaluated in ascending order. "Original" means the position's total_usd /
token amount at entry -- so tiers are additive and don't compound off each
other, and re-checking after a restart still knows exactly which rungs have
already fired (state lives in position_state's moonbag_trims, not here).

Default ladder recovers more than the original stake by 3x, keeps de-risking
through 10x and 50x, and leaves a 20% moonbag riding uncapped past that:
  3x  -> sell 35% of original (well past cost basis back in hand)
  10x -> sell another 25% of original
  50x -> sell another 20% of original
  (80% total trimmed across the ladder; 20% moonbag rides forever)

UPDATE (Sept 22, 2026): that fixed 35/25/20 split treats every runner the
same, which throws away exactly the position most worth holding when
several independent signals actually point to a real outlier. moonbag.py
now supports a SECOND, lighter ladder (HIGH_CONVICTION_LADDER, 15/15/15,
55% moonbag) chosen once at entry via assess_conviction() -- a pure scoring
function over signals other layers already compute (deployer tier, wallet
convergence strength, Stage1+Stage2 double-confirmation, Layer 10 insider
ratio, a news catalyst). The heaviest single weight is double-confirmation,
because that's Ali's own stated logic, not an invented heuristic: if the
same coin gets picked independently on both pump.fun AND Fomo, that's the
strongest signal this system has. Every score weight is otherwise a
heuristic, not backtested -- same honesty flag as the rest of this file.

Like every other live-network-dependent path in this codebase, the actual
sell in check_and_trim() goes through swap_executor.execute_sell, which is
still UNTESTED/inert until EXECUTION_ENABLED is turned on -- so this module
can be exercised end-to-end in tests today without any risk of a real trade.

Known gap, same as defensive_sell.py: amount_tokens on a position isn't
populated yet because swap_executor's buy paths don't record real fills
(they return UNTESTED, not a filled quantity). Trim sizing here is correct
in principle (pct of original) but won't produce a real token quantity to
sell until that fill-recording gap is closed alongside turning execution on.
"""
import time
from dataclasses import dataclass
from typing import Optional

import executor.position_state as position_state
import executor.swap_executor as swap_executor
import executor.circuit_breaker as circuit_breaker
from executor.config import EXECUTOR_CONFIG

# (multiple vs entry mcap, pct of ORIGINAL position to sell at this rung)
DEFAULT_TRIM_LADDER = [
    (3.0, 0.35),
    (10.0, 0.25),
    (50.0, 0.20),
]  # trims 80% total -> 20% moonbag rides

# Lighter ladder for a position multiple corroborating signals flagged as
# real moonshot material at entry (Ali, Sept 22, 2026: "there must be
# something which should identify a coin as a potential moonshot by seeing
# multiple things" -- trimming 35% off the top the same way for every
# runner throws away exactly the position you most want to hold). Still
# takes SOME risk off the table at each rung -- this isn't "never sell
# anything" -- but leaves more than double the moonbag riding.
HIGH_CONVICTION_LADDER = [
    (3.0, 0.15),
    (10.0, 0.15),
    (50.0, 0.15),
]  # trims 45% total -> 55% moonbag rides

LADDERS_BY_NAME = {
    "default": DEFAULT_TRIM_LADDER,
    "high_conviction": HIGH_CONVICTION_LADDER,
}

# Score weights -- ASSUMED/heuristic, not derived from any backtest (same
# honesty flag as the rest of this file's outcome assumptions). The one
# weight that ISN'T a guess is double-confirmed: that's Ali's own stated
# logic ("if one coin is highlighted from both [pump.fun and Fomo], it
# might be the actual gem for us") turned into a number, not something I
# invented independently.
CONVICTION_THRESHOLD = 4


def compute_moonshot_score(deployer_tier: Optional[str] = None, convergence_count: int = 0,
                            double_confirmed: bool = False, insider_ratio: Optional[float] = None,
                            has_news_catalyst: bool = False) -> int:
    """Pure scoring function, no network -- combines signals that are
    ALREADY computed elsewhere in this codebase (Layer 1 deployer tier,
    Layer 2 wallet convergence, Stage1+Stage2 double-confirmation, Layer 10
    insider tagging, Layer 4 news) into one number. Higher = more reasons
    to believe this specific position is worth holding through, not just
    trading. A single strong signal alone (e.g. just an elite deployer)
    should NOT be enough to flip the ladder -- the point is corroboration
    across independent signals, not any one filter being confident."""
    score = 0

    if deployer_tier == "elite":
        score += 2
    elif deployer_tier == "good":
        score += 1

    if convergence_count >= 3:
        score += 2
    elif convergence_count == 2:
        score += 1

    if double_confirmed:
        score += 3  # pump.fun AND Fomo independently converged on the same coin

    if insider_ratio is not None:
        if insider_ratio <= 0.15:
            score += 1   # mostly organic holder base -- healthier, more likely to sustain a run
        elif insider_ratio >= 0.50:
            score -= 2   # heavily insider-held -- looks more like an orchestrated pump than a real gem

    if has_news_catalyst:
        score += 1

    return score


def ladder_name_for_score(score: int) -> str:
    return "high_conviction" if score >= CONVICTION_THRESHOLD else "default"


def assess_conviction(chain: str, token: str, deployer_tier: Optional[str] = None,
                       convergence_count: int = 0, double_confirmed: bool = False,
                       insider_ratio: Optional[float] = None, has_news_catalyst: bool = False) -> dict:
    """Call this once, right after a Stage 1 or Stage 2 fire records the
    position, with whatever signals are known at that moment. Computes the
    score, picks the ladder, and locks it onto the position via
    position_state.set_trim_ladder -- see that function's docstring for why
    this is a one-time decision rather than something re-evaluated every
    poll cycle."""
    score = compute_moonshot_score(deployer_tier, convergence_count, double_confirmed,
                                    insider_ratio, has_news_catalyst)
    ladder_name = ladder_name_for_score(score)

    # Never downgrade an already-locked high_conviction ladder -- this can
    # legitimately get called a second time on the same position (Stage 1
    # scores it first, Stage 2 confirming later re-scores with the new
    # double_confirmed=True signal). A second call should only ever be able
    # to upgrade a position's ladder, never take back conviction the first
    # call already earned it.
    existing_ladder = position_state.get_trim_ladder(chain, token)
    if existing_ladder == "high_conviction" and ladder_name == "default":
        ladder_name = "high_conviction"

    position_state.set_trim_ladder(chain, token, ladder_name, score)
    return {"score": score, "ladder": ladder_name}


@dataclass
class TrimDecision:
    should_fire: bool
    reason: str
    tier_multiple: Optional[float] = None
    pct_of_original: Optional[float] = None
    achieved_multiple: Optional[float] = None


def multiple_achieved(entry_mcap: Optional[float], current_mcap_usd: Optional[float]) -> Optional[float]:
    if not entry_mcap or not current_mcap_usd or entry_mcap <= 0:
        return None
    return current_mcap_usd / entry_mcap


def _entry_mcap(pos: dict) -> Optional[float]:
    """Baseline is the earliest stage's entry_mcap -- Stage 1 if it fired,
    otherwise Stage 2's (matches how triggers.py already treats Stage 1 as
    the reference entry for the Stage 2 mcap-gate multiplier)."""
    stages = pos.get("stages", {})
    if "stage1" in stages and stages["stage1"].get("entry_mcap"):
        return stages["stage1"]["entry_mcap"]
    if "stage2" in stages and stages["stage2"].get("entry_mcap"):
        return stages["stage2"]["entry_mcap"]
    return None


def evaluate_trim(chain: str, token: str, current_mcap_usd: Optional[float]) -> TrimDecision:
    """Pure decision logic, no network -- same split as triggers.py between
    deciding and executing."""
    if not EXECUTOR_CONFIG.moonbag_enabled:
        return TrimDecision(False, "moonbag trimming disabled in config")

    pos = position_state.get_position(chain, token)
    if not pos or pos.get("status") != "open":
        return TrimDecision(False, "no open position")

    entry_mcap = _entry_mcap(pos)
    mult = multiple_achieved(entry_mcap, current_mcap_usd)
    if mult is None:
        return TrimDecision(False, "no entry_mcap or current_mcap_usd available to compute a multiple")

    ladder_name = position_state.get_trim_ladder(chain, token)
    ladder = LADDERS_BY_NAME.get(ladder_name, DEFAULT_TRIM_LADDER)

    fired = pos.get("moonbag_trims", {})
    for tier_multiple, tier_pct in ladder:
        if str(tier_multiple) in fired:
            continue  # this rung already trimmed
        if mult >= tier_multiple:
            return TrimDecision(
                True, f"{mult:.1f}x reached the {tier_multiple}x trim tier ({ladder_name} ladder)",
                tier_multiple=tier_multiple, pct_of_original=tier_pct, achieved_multiple=mult,
            )
        break  # ladder is ascending -- if this rung isn't reached yet, none higher are either

    return TrimDecision(False, f"{mult:.1f}x -- no untrimmed tier reached yet", achieved_multiple=mult)


def check_and_trim(chain: str, token: str, current_mcap_usd: Optional[float]) -> Optional[dict]:
    """Call this each poll cycle for every open position (same shape as
    defensive_sell.check_and_defend). Fires at most one rung per call --
    if price has jumped past multiple untrimmed tiers since the last check,
    the next tier fires on the following cycle rather than dumping several
    slices at once."""
    decision = evaluate_trim(chain, token, current_mcap_usd)
    if not decision.should_fire:
        return None

    pos = position_state.get_position(chain, token)
    amount_tokens_to_sell = pos.get("amount_tokens", 0) * decision.pct_of_original

    result = swap_executor.execute_sell(
        chain=chain, token_address=token, amount_tokens=amount_tokens_to_sell,
        reason=f"moonbag trim: {decision.reason}",
    )

    if result.ok:
        position_state.record_moonbag_trim(
            chain, token, decision.tier_multiple, decision.pct_of_original, result.filled_usd,
        )
        if result.filled_usd is not None:
            cost_basis_for_trim = pos.get("total_usd", 0) * decision.pct_of_original
            circuit_breaker.record_trade_result(result.filled_usd - cost_basis_for_trim)

    return {
        "chain": chain, "token": token, "trim_decision": decision,
        "sell_attempted": True, "sell_result": result,
    }

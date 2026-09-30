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
    (3.0, 0.20),
    (10.0, 0.20),
    (50.0, 0.15),
]  # Revised Sept 28 2026 (Ali, live): "u r trimming way too much percentage
# on short levels...leaving nothing for a massive gain n hold...only 20%
# wont suit my purpose if coin explodes." Old ladder trimmed 80% by 50x,
# leaving only a 20% moonbag -- fine for de-risking a merely-good trade,
# but self-defeating for the actual goal (memory: catch one real 500x+
# moonshot off a small deployed stake). New ladder trims 55% total across
# the same three checkpoints -> 45% moonbag rides uncapped. Recovery math
# still holds: by the 10x checkpoint alone (20%+20% fired) the position has
# already returned 0.20*3 + 0.20*10 = 2.6x the original stake in realized
# cash, so real capital is back well before any explosive move even starts
# -- the remaining 45% is genuinely free-roll size for a 100x/500x/1000x
# run, not capital still at risk. This is the LOW-conviction ladder --
# assess_conviction() already routes any position that scores >= 4
# (elite/good deployer, 2-3+ wallet convergence, double-confirmed Stage1+
# Stage2, low insider ratio, news catalyst) onto HIGH_CONVICTION_LADDER
# below instead, which was already far more moonbag-friendly (55%) even
# before this change, or onto compute_hard_target_ladder's real-dollar
# milestones (10%/rung at each of hard_target_usd_levels, ~70% riding
# after all three) -- so a position the system is actually CONFIDENT about
# was never the 20%-moonbag problem Ali flagged; this default ladder is
# the fallback for lower-conviction entries and is the one that needed
# widening.

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
]  # trims 45% total -> 55% moonbag rides. Left unchanged Sept 28 2026 --
# already well past what Ali flagged as too small on the default ladder;
# a position confident enough to earn this ladder deserves to ride most
# of its size toward the moonshot target already.

LADDERS_BY_NAME = {
    "default": DEFAULT_TRIM_LADDER,
    "high_conviction": HIGH_CONVICTION_LADDER,
}


def compute_hard_target_ladder(entry_usd: float) -> list:
    """Turns EXECUTOR_CONFIG.hard_target_usd_levels (real dollar milestones,
    e.g. $7,000 / $20,000 / $95,000) into a per-position price-multiple
    ladder, sized to THIS position's actual entry_usd -- a $20 entry and a
    $50 entry need very different multiples to reach the same dollar level.
    Same (multiple, pct_of_original) shape as DEFAULT_TRIM_LADDER/
    HIGH_CONVICTION_LADDER, so evaluate_trim doesn't need to know the
    difference. Trims EXECUTOR_CONFIG.hard_target_trim_pct (10% by default)
    at each level -- real cash banked at each real milestone crossed,
    without capping the position: with 3 levels at 10% each, 70% of the
    ORIGINAL position keeps riding uncapped past the highest level, same
    "de-risk in stages, never force a full exit" philosophy as the other
    two ladders, just tuned to real dollar levels Ali named instead of
    generic price multiples.

    Returns [] (falls back to the caller's default) if entry_usd isn't a
    real positive number -- never divides by zero or invents a multiple."""
    if not entry_usd or entry_usd <= 0:
        return []
    pct = EXECUTOR_CONFIG.hard_target_trim_pct
    return [(level / entry_usd, pct) for level in sorted(EXECUTOR_CONFIG.hard_target_usd_levels)]

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

    # A position that earns high_conviction rides the real hard-dollar-target
    # ladder instead of the generic 3x/10x/50x one (see
    # compute_hard_target_ladder's docstring) -- computed fresh off this
    # position's own total_usd each time this runs, so a Stage 2 upgrade
    # that adds more capital to the same token recomputes the right
    # multiples for the new combined entry size, not the Stage 1-only one.
    rungs = None
    if ladder_name == "high_conviction":
        pos = position_state.get_position(chain, token)
        entry_usd = (pos or {}).get("total_usd", 0.0)
        rungs = compute_hard_target_ladder(entry_usd) or None  # None -> falls back to HIGH_CONVICTION_LADDER

    position_state.set_trim_ladder(chain, token, ladder_name, score, rungs=rungs)
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
    # Any other entry path (e.g. Layer 15 "moonshot") -- without this its
    # exits never fired (no baseline -> no multiple). Fixed Sept 30 2026.
    for st in stages.values():
        if st.get("entry_mcap"):
            return st["entry_mcap"]
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
    # A high_conviction position with real hard-target rungs locked in at
    # entry (see assess_conviction) uses THOSE instead of the generic
    # HIGH_CONVICTION_LADDER multiples -- pos.get here rather than a second
    # position_state call since `pos` is already in hand below.
    custom_rungs = pos.get("trim_ladder_rungs")
    ladder = custom_rungs if custom_rungs else LADDERS_BY_NAME.get(ladder_name, DEFAULT_TRIM_LADDER)

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
    # After a breakeven lock (executor/exit_rules.py) only part of the
    # position rides, so each rung is scaled to it and never exceeds what's
    # actually left (Sept 30 2026).
    scaled_pct = min(decision.pct_of_original * float(pos.get("ladder_scale", 1.0)),
                     position_state.remaining_pct(chain, token))
    if scaled_pct <= 0:
        return None
    decision.pct_of_original = scaled_pct
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

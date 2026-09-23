"""
Integration entrypoint -- the single call a live poll loop makes per
candidate, instead of having to know the right order to call triggers.py,
position_state.py, and moonbag.py itself. This is glue, not new decision
logic: everything it does is delegate to those three modules in the correct
sequence, and it makes no network calls of its own.

STATUS: built and tested, but NOT called from scheduler.py's actual poll
loop yet -- same as the rest of executor/ (see README.md's "Before this can
ever go live" list). Nothing here executes a trade; it only decides whether
a position WOULD be opened and which moonbag ladder it would use if/when
swap_executor is turned on. Wiring this into scheduler.py's live loop is a
separate step, deliberately left until after the Part A backtest and Ali's
review -- calling handle_stage1_candidate/handle_stage2_candidate today has
no effect on anything live because nothing in the poll loop calls it yet.

Usage (once wired): for each candidate Layers 0/0b, 1, 2 have already
scored on a poll cycle, call handle_stage1_candidate with whatever's known
at launchpad level. For a coin graduating with Fomo convergence, call
handle_stage2_candidate. Both return a dict describing what happened either
way (fired or not, and why) so the caller can log or alert on the outcome
without re-deriving it.
"""
from typing import Optional

import executor.position_state as position_state
import executor.triggers as triggers
import executor.moonbag as moonbag


def handle_stage1_candidate(chain: str, token: str, score_band: Optional[str],
                             deployer_tier: Optional[str], convergence_count: int,
                             entry_mcap: Optional[float], insider_ratio: Optional[float] = None,
                             has_news_catalyst: bool = False) -> dict:
    """Evaluates the Stage 1 trigger and, if it fires, records the position
    and locks in its moonbag ladder in the same step. entry_mcap is the
    launchpad-level mcap at the moment of firing -- the caller (a future
    scheduler integration) already has this from Layer 0/0b's own scan, so
    it isn't re-fetched here."""
    decision = triggers.evaluate_stage1(chain, token, score_band, deployer_tier, convergence_count)
    if not decision.should_fire:
        return {"fired": False, "stage": "stage1", "reason": decision.reason}

    position_state.record_stage_entry(chain, token, "stage1", decision.position_usd, entry_mcap, decision.reason)
    conviction = moonbag.assess_conviction(
        chain, token, deployer_tier=deployer_tier, convergence_count=convergence_count,
        double_confirmed=False,  # a lone Stage 1 fire can't be double-confirmed yet
        insider_ratio=insider_ratio, has_news_catalyst=has_news_catalyst,
    )
    return {
        "fired": True, "stage": "stage1", "position_usd": decision.position_usd,
        "reason": decision.reason, "conviction_score": conviction["score"],
        "trim_ladder": conviction["ladder"],
    }


def handle_stage2_candidate(chain: str, token: str, current_mcap_usd: Optional[float],
                             fomo_convergence_count: int, graduated: bool,
                             deployer_tier: Optional[str] = None, insider_ratio: Optional[float] = None,
                             has_news_catalyst: bool = False) -> dict:
    """Evaluates the Stage 2 trigger and, if it fires, records the position
    and (re-)assesses conviction -- picking up double-confirmation if a
    Stage 1 entry already exists on this token. See moonbag.assess_conviction's
    docstring for why a second scoring call can only upgrade a position's
    ladder, never downgrade one Stage 1 already locked in."""
    decision = triggers.evaluate_stage2(chain, token, current_mcap_usd, fomo_convergence_count, graduated)
    if not decision.should_fire:
        return {"fired": False, "stage": "stage2", "reason": decision.reason}

    position_state.record_stage_entry(chain, token, "stage2", decision.position_usd, current_mcap_usd, decision.reason)
    double_confirmed = triggers.is_double_confirmed(chain, token)
    conviction = moonbag.assess_conviction(
        chain, token, deployer_tier=deployer_tier, convergence_count=fomo_convergence_count,
        double_confirmed=double_confirmed, insider_ratio=insider_ratio, has_news_catalyst=has_news_catalyst,
    )
    return {
        "fired": True, "stage": "stage2", "position_usd": decision.position_usd,
        "reason": decision.reason, "double_confirmed": double_confirmed,
        "conviction_score": conviction["score"], "trim_ladder": conviction["ladder"],
    }

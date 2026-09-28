"""
Trigger evaluation -- decides whether a Stage 1 or Stage 2 auto-buy should
fire, given signals the existing alert layers already compute. This module
makes NO network calls itself -- it's pure decision logic over inputs the
caller (a future scheduler integration, not wired in yet) already has from
Layers 0/0b, 1, 2, and the per-chain launchpad/graduation state.

Kept deliberately separate from swap_executor.py: this decides WHETHER to
buy, that module decides HOW to actually send the transaction. Testable
without any live API or wallet key.
"""
from dataclasses import dataclass
from typing import Optional

import executor.position_state as position_state
import executor.circuit_breaker as circuit_breaker
from executor.config import EXECUTOR_CONFIG

# Band-scaled Stage 1 position sizing (Ali, Sept 29 2026: "with abcd bands
# pricing or entry must be managed" -- band was previously fire/no-fire
# only, with every Stage 1 entry betting the exact same flat
# stage1_position_usd regardless of whether it was a strong band-A signal
# or barely scraped in on a 2-wallet convergence with a D-band score.
# Multiplier is applied to EXECUTOR_CONFIG.stage1_position_usd (today's
# default $20) for EVERY fire path -- score, deployer, or convergence --
# not just score-triggered fires, since a token's band is a real read on
# its structural quality regardless of which condition actually fired.
# None (no score available yet at this point in the caller) gets the same
# mid-tier treatment as C, deliberately conservative rather than assuming
# quality it hasn't been shown.
STAGE1_BAND_SIZE_MULTIPLIER = {"A": 1.0, "B": 0.75, "C": 0.4, "D": 0.25}
STAGE1_UNKNOWN_BAND_MULTIPLIER = 0.4


def stage1_position_usd_for_band(score_band) -> float:
    mult = STAGE1_BAND_SIZE_MULTIPLIER.get(score_band, STAGE1_UNKNOWN_BAND_MULTIPLIER)
    return round(EXECUTOR_CONFIG.stage1_position_usd * mult, 2)


LAUNCHPAD_BY_CHAIN = {
    "solana": "pump.fun",
    "robinhood_chain": "pons/flap.sh",
    "bsc": "four.meme",
}
GRADUATED_VENUE_BY_CHAIN = {
    "solana": "jupiter/raydium",
    "robinhood_chain": "uniswap_v4",
    "bsc": "pancakeswap",
}


@dataclass
class TriggerDecision:
    should_fire: bool
    stage: Optional[str]  # "stage1" | "stage2" | None
    reason: str
    position_usd: float = 0.0


def evaluate_stage1(chain: str, token: str, score_band: Optional[str],
                     deployer_tier: Optional[str], convergence_count: int) -> TriggerDecision:
    """Fires on: score A/B, OR a known-good/elite deployer, OR 2+ tracked
    wallets converging on the same coin. Any one of the three is sufficient,
    matching the alert-layer logic these mirror (Layers 0/0b, 1, 2)."""
    breaker = circuit_breaker.is_tripped()
    if breaker["tripped"]:
        return TriggerDecision(False, None, f"circuit breaker tripped: {breaker['reason']}")

    if position_state.has_stage(chain, token, "stage1"):
        return TriggerDecision(False, None, "stage1 already fired for this token")

    position_usd = stage1_position_usd_for_band(score_band)
    committed = position_state.stage_committed_usd("stage1")
    budget = EXECUTOR_CONFIG.stage1_budget_usd()
    if committed + position_usd > budget:
        return TriggerDecision(
            False, None,
            f"stage1 budget exhausted: ${committed:.2f} committed of ${budget:.2f} budget "
            f"(a ${position_usd:.2f} band-{score_band or '?'}-sized entry would exceed it)"
        )

    if score_band in ("A", "B"):
        return TriggerDecision(True, "stage1", f"structural score band {score_band}",
                                position_usd)
    if deployer_tier in ("elite", "good"):
        return TriggerDecision(True, "stage1", f"deployer tier {deployer_tier} (band {score_band or '?'})",
                                position_usd)
    if convergence_count >= 2:
        return TriggerDecision(True, "stage1", f"{convergence_count}-wallet convergence (band {score_band or '?'})",
                                position_usd)

    return TriggerDecision(False, None, "no stage1 trigger condition met")


def evaluate_stage2(chain: str, token: str, current_mcap_usd: Optional[float],
                     fomo_convergence_count: int, graduated: bool) -> TriggerDecision:
    """Fires on Fomo-roster convergence (2+ tracked traders) once the coin
    has graduated to real pool liquidity AND cleared the mcap floor. Does
    NOT require Stage 1 to have fired first -- an independent late
    confirmation is still a valid entry per Ali's direction."""
    breaker = circuit_breaker.is_tripped()
    if breaker["tripped"]:
        return TriggerDecision(False, None, f"circuit breaker tripped: {breaker['reason']}")

    if position_state.has_stage(chain, token, "stage2"):
        return TriggerDecision(False, None, "stage2 already fired for this token")

    committed = position_state.stage_committed_usd("stage2")
    budget = EXECUTOR_CONFIG.stage2_budget_usd()
    if committed + EXECUTOR_CONFIG.stage2_position_usd > budget:
        return TriggerDecision(
            False, None,
            f"stage2 budget exhausted: ${committed:.2f} committed of ${budget:.2f} budget "
            f"(a ${EXECUTOR_CONFIG.stage2_position_usd:.2f} entry would exceed it)"
        )

    if not graduated:
        return TriggerDecision(False, None, "not yet graduated to pool liquidity")

    if fomo_convergence_count < 2:
        return TriggerDecision(False, None, f"only {fomo_convergence_count} tracked trader(s), need 2+")

    mcap_floor_ok = _stage2_mcap_gate(chain, token, current_mcap_usd)
    if not mcap_floor_ok["ok"]:
        return TriggerDecision(False, None, mcap_floor_ok["reason"])

    return TriggerDecision(True, "stage2",
                            f"{fomo_convergence_count}-wallet Fomo convergence, {mcap_floor_ok['reason']}",
                            EXECUTOR_CONFIG.stage2_position_usd)


def _stage2_mcap_gate(chain: str, token: str, current_mcap_usd: Optional[float]) -> dict:
    if current_mcap_usd is None:
        return {"ok": False, "reason": "current mcap unknown, can't evaluate floor"}

    stage1_pos = position_state.get_position(chain, token)
    stage1_mcap = None
    if stage1_pos and "stage1" in stage1_pos.get("stages", {}):
        stage1_mcap = stage1_pos["stages"]["stage1"].get("entry_mcap")

    if stage1_mcap:
        floor = stage1_mcap * EXECUTOR_CONFIG.stage2_mcap_multiplier
        if current_mcap_usd >= floor:
            return {"ok": True, "reason": f"mcap ${current_mcap_usd:,.0f} >= {EXECUTOR_CONFIG.stage2_mcap_multiplier}x stage1 entry (${floor:,.0f})"}
        return {"ok": False, "reason": f"mcap ${current_mcap_usd:,.0f} below {EXECUTOR_CONFIG.stage2_mcap_multiplier}x stage1 floor (${floor:,.0f})"}

    # no stage1 entry on record -- use the flat floor instead
    if current_mcap_usd >= EXECUTOR_CONFIG.stage2_mcap_floor_usd:
        return {"ok": True, "reason": f"mcap ${current_mcap_usd:,.0f} >= flat floor (${EXECUTOR_CONFIG.stage2_mcap_floor_usd:,.0f})"}
    return {"ok": False, "reason": f"mcap ${current_mcap_usd:,.0f} below flat floor (${EXECUTOR_CONFIG.stage2_mcap_floor_usd:,.0f})"}


def is_double_confirmed(chain: str, token: str) -> bool:
    pos = position_state.get_position(chain, token)
    return bool(pos and pos.get("double_confirmed"))

"""
Synthetic simulation of the Part B pipeline (Stage 1 trigger -> moonbag
ladder -> circuit breaker -> capital tracking) end-to-end, using the REAL
decision logic from triggers.py / moonbag.py / circuit_breaker.py against
FAKE candidate coins and FAKE price outcomes.

WHAT THIS IS: a mechanics test. It proves the pieces wire together
correctly -- sizing, budget tracking, trim firing in the right order, the
circuit breaker actually stopping new entries after a bad run -- using the
exact same pure-logic functions the real system will call.

WHAT THIS IS NOT: a return forecast. Two numbers below are ASSUMED, not
measured, because the real backtest (Part A, backtest.yml) hasn't run yet:
  1. Stage 1 fire rate -- how often a scanned candidate actually passes the
     score/deployer/convergence filter. Set to ~15% here as a placeholder.
  2. Outcome distribution -- how a fired position's price actually moves.
     The tiers below (rug/flat/runner/big/moonshot) and their probabilities
     are illustrative guesses about memecoin outcome shape, not derived from
     any of S1c's own data. Until the real backtest returns a hit rate,
     nothing here should be read as "this is what S1c will do."

UPDATE (Sept 22, 2026): the budget-cap gap this simulation originally
surfaced -- triggers.py firing with no regard for capital already committed
to open positions -- is now fixed in executor/triggers.py itself
(position_state.stage_committed_usd() checked against each stage's budget).
This script's own budget bookkeeping below is kept for clarity but now
matches what the real trigger logic actually enforces.

Also models Layer 6 / defensive_sell.py's early-exit effect on "rug"
outcomes (see LAYER6_DETECTION_RATE below) so a --compare-defense run can
show what the rug filter is actually worth versus flying blind, on the
same synthetic world.
"""
import argparse
import random
from dataclasses import dataclass, field
from typing import Optional

import state
import executor.position_state as position_state
import executor.triggers as triggers
import executor.circuit_breaker as circuit_breaker
import executor.moonbag as moonbag
from executor.config import EXECUTOR_CONFIG

# --- ASSUMED, not measured -- see module docstring ---
STAGE1_FIRE_RATE = 0.15
OUTCOME_TIERS = [
    # (name, probability, peak_multiple_range, description)
    ("rug",      0.65, (0.0, 0.3),   "dies / rugged -- defensive-sell-equivalent exit"),
    ("flat",     0.22, (0.3, 1.5),   "never reaches first moonbag tier, closed at sim end"),
    ("runner",   0.09, (3.0, 10.0),  "hits the 3x moonbag tier only"),
    ("big",      0.03, (10.0, 50.0), "hits 3x and 10x tiers"),
    ("moonshot", 0.01, (50.0, 500.0), "hits all three tiers, moonbag rides the rest of the sim"),
]

# --- ALSO ASSUMED, not measured -- Layer 6 / defensive_sell.py modeling ---
# Layer 6 watches for LP withdrawal, top-holder dumps, and mint/freeze
# re-enabling -- real signals, but whether they fire IN TIME to save most of
# the position depends on how fast a given rug happens (instant full-LP-pull
# rugs can outrun any polling-based detection; slower "soft rug" drains
# usually don't). No real data exists yet on what fraction of S1c's actual
# rug outcomes are the fast kind vs the slow kind, so this is a placeholder,
# not a measured detection rate.
LAYER6_DETECTION_RATE = 0.55          # fraction of "rug" outcomes caught early
LAYER6_CAUGHT_EXIT_RANGE = (0.55, 0.85)  # exit multiple when caught early (vs 0.0-0.3 if not)


@dataclass
class SimResult:
    candidates_seen: int = 0
    stage1_fired: int = 0
    stage1_skipped_budget: int = 0
    stage1_skipped_breaker: int = 0
    outcome_counts: dict = field(default_factory=dict)
    trim_counts: dict = field(default_factory=lambda: {"3.0": 0, "10.0": 0, "50.0": 0})
    breaker_trip_events: list = field(default_factory=list)
    realized_pnl_usd: float = 0.0
    still_riding: list = field(default_factory=list)
    best_position: Optional[dict] = None
    defense_enabled: bool = True
    rugs_caught_early: int = 0
    rugs_undetected: int = 0


def _random_candidate_attrs(rng: random.Random):
    """Draws a synthetic candidate's score band / deployer tier / convergence
    such that it fires Stage 1 roughly STAGE1_FIRE_RATE of the time --
    calibrated to hit that rate, not drawn from real MadeOnSol distributions."""
    if rng.random() < STAGE1_FIRE_RATE:
        # make it qualify via one of the three real OR conditions
        pick = rng.choice(["score", "deployer", "convergence"])
        if pick == "score":
            return rng.choice(["A", "B"]), None, 0
        if pick == "deployer":
            return "D", rng.choice(["elite", "good"]), 0
        return "D", None, rng.choice([2, 3])
    return "D", "spammer", 0  # deliberately fails all three conditions


def _pick_outcome(rng: random.Random):
    r = rng.random()
    cum = 0.0
    for name, prob, mult_range, desc in OUTCOME_TIERS:
        cum += prob
        if r <= cum:
            return name, rng.uniform(*mult_range), desc
    return OUTCOME_TIERS[-1][0], rng.uniform(*OUTCOME_TIERS[-1][2]), OUTCOME_TIERS[-1][3]


def run_simulation(wallet_usd: float, num_candidates: int, seed: Optional[int] = None,
                    defense_enabled: bool = True) -> SimResult:
    # Two independent RNG streams: rng drives the "world" (which candidates
    # appear, what they'd do) -- identical between a defense-on and
    # defense-off run at the same seed. rng_defense only decides whether
    # Layer 6 catches a given rug in time -- so toggling defense_enabled
    # doesn't perturb the world and the two runs are a clean comparison.
    rng = random.Random(seed)
    rng_defense = random.Random(seed + 1 if seed is not None else None)
    result = SimResult(defense_enabled=defense_enabled)

    EXECUTOR_CONFIG.total_wallet_usd = wallet_usd
    committed_usd = 0.0
    stage1_budget = EXECUTOR_CONFIG.stage1_budget_usd()

    for i in range(num_candidates):
        result.candidates_seen += 1
        token = f"SimMint{i:04d}"
        score_band, deployer_tier, convergence = _random_candidate_attrs(rng)

        decision = triggers.evaluate_stage1("solana", token, score_band, deployer_tier, convergence)
        if not decision.should_fire:
            if "circuit breaker" in (decision.reason or ""):
                result.stage1_skipped_breaker += 1
            continue

        if committed_usd + decision.position_usd > stage1_budget:
            result.stage1_skipped_budget += 1
            continue  # extra guard -- triggers.py's own budget check should already have refused this

        entry_mcap = 100000.0  # arbitrary fixed baseline -- only the multiple matters, not the absolute number
        position_state.record_stage_entry("solana", token, "stage1", decision.position_usd, entry_mcap, decision.reason)
        committed_usd += decision.position_usd
        result.stage1_fired += 1

        outcome_name, peak_multiple, desc = _pick_outcome(rng)
        result.outcome_counts[outcome_name] = result.outcome_counts.get(outcome_name, 0) + 1

        if outcome_name == "rug":
            # Always draw both rng_defense values, whether or not defense is
            # enabled, so the rng_defense stream stays in lockstep between a
            # defense_enabled=True and defense_enabled=False run at the same
            # seed -- otherwise the OFF run short-circuits past these draws
            # and every rug event AFTER the first desyncs from the ON run,
            # silently breaking the "same world, only defense toggles"
            # comparison this script exists to make.
            would_catch = rng_defense.random() < LAYER6_DETECTION_RATE
            early_exit_sample = rng_defense.uniform(*LAYER6_CAUGHT_EXIT_RANGE)
            caught_early = defense_enabled and would_catch
            if caught_early:
                exit_mult = early_exit_sample
                close_reason = "sim: Layer 6 caught it -- defensive_sell exited early"
                result.rugs_caught_early += 1
            else:
                exit_mult = peak_multiple  # undetected, or defense off -- rides to the terminal price
                close_reason = f"sim: {desc} (undetected by Layer 6)" if defense_enabled else f"sim: {desc} (no defense modeled)"
                result.rugs_undetected += 1
            exit_usd = decision.position_usd * exit_mult
            position_state.close_position("solana", token, reason=close_reason, exit_usd=exit_usd)
            pnl = exit_usd - decision.position_usd
            circuit_breaker.record_trade_result(pnl)
            result.realized_pnl_usd += pnl
            committed_usd -= decision.position_usd
            _check_breaker(result, token)
            continue

        if outcome_name == "flat":
            exit_usd = decision.position_usd * peak_multiple
            position_state.close_position("solana", token, reason=f"sim: {desc}", exit_usd=exit_usd)
            pnl = exit_usd - decision.position_usd
            circuit_breaker.record_trade_result(pnl)
            result.realized_pnl_usd += pnl
            committed_usd -= decision.position_usd
            _check_breaker(result, token)
            continue

        # runner / big / moonshot -- walk the mcap up through the ladder,
        # firing each moonbag tier the real evaluate_trim() logic says to.
        current_mult = 1.0
        step = max(peak_multiple / 6, 0.5)
        while current_mult < peak_multiple:
            current_mult = min(current_mult + step, peak_multiple)
            current_mcap = entry_mcap * current_mult
            trim = moonbag.evaluate_trim("solana", token, current_mcap)
            if trim.should_fire:
                trim_exit_usd = decision.position_usd * trim.pct_of_original * current_mult
                position_state.record_moonbag_trim("solana", token, trim.tier_multiple, trim.pct_of_original, trim_exit_usd)
                cost_basis = decision.position_usd * trim.pct_of_original
                pnl = trim_exit_usd - cost_basis
                circuit_breaker.record_trade_result(pnl)
                result.realized_pnl_usd += pnl
                committed_usd -= cost_basis
                result.trim_counts[str(trim.tier_multiple)] = result.trim_counts.get(str(trim.tier_multiple), 0) + 1
                _check_breaker(result, token)

        remaining = position_state.remaining_pct("solana", token)
        if remaining > 0:
            unrealized_usd = decision.position_usd * remaining * peak_multiple
            result.still_riding.append({
                "token": token, "outcome": outcome_name, "peak_multiple": round(peak_multiple, 1),
                "remaining_pct": round(remaining, 2), "unrealized_usd": round(unrealized_usd, 2),
            })
            if not result.best_position or unrealized_usd > result.best_position.get("unrealized_usd", 0):
                result.best_position = result.still_riding[-1]

    return result


def _check_breaker(result: SimResult, last_token: str):
    status = circuit_breaker.is_tripped()
    if status["tripped"] and not result.breaker_trip_events:
        result.breaker_trip_events.append({"after_token": last_token, "reason": status["reason"]})


def print_report(result: SimResult, wallet_usd: float):
    print("=" * 70)
    print("S1c EXECUTOR SIMULATION -- SYNTHETIC, NOT A RETURN FORECAST")
    print("=" * 70)
    print(f"Starting wallet:        ${wallet_usd:.2f}")
    print(f"Candidates scanned:     {result.candidates_seen}")
    print(f"Stage 1 fired:          {result.stage1_fired}")
    print(f"Skipped (budget maxed): {result.stage1_skipped_budget}")
    print(f"Skipped (breaker):      {result.stage1_skipped_breaker}")
    print()
    print("Outcome breakdown (of positions that fired):")
    for name, _, _, desc in OUTCOME_TIERS:
        count = result.outcome_counts.get(name, 0)
        print(f"  {name:10s} x{count:3d}  -- {desc}")
    print()
    rug_total = result.rugs_caught_early + result.rugs_undetected
    if rug_total:
        defense_label = "ON" if result.defense_enabled else "OFF (baseline -- no early exit modeled)"
        print(f"Layer 6 / defensive_sell: {defense_label}")
        print(f"  caught early:  {result.rugs_caught_early}/{rug_total} "
              f"(exited at {int(LAYER6_CAUGHT_EXIT_RANGE[0]*100)}-{int(LAYER6_CAUGHT_EXIT_RANGE[1]*100)}% of stake)")
        print(f"  undetected:    {result.rugs_undetected}/{rug_total} (rode to the terminal 0-30% outcome)")
        print()
    print("Moonbag trims fired:")
    for tier, count in result.trim_counts.items():
        print(f"  {tier}x tier: {count} times")
    print()
    if result.breaker_trip_events:
        print(f"Circuit breaker tripped: YES -- {result.breaker_trip_events[0]['reason']}")
    else:
        print("Circuit breaker tripped: no")
    print()
    ending_wallet = wallet_usd + result.realized_pnl_usd
    print(f"Realized P&L:            ${result.realized_pnl_usd:+.2f}")
    print(f"Ending wallet (realized):${ending_wallet:.2f}")
    print()
    if result.still_riding:
        total_unrealized = sum(p["unrealized_usd"] for p in result.still_riding)
        print(f"Still-riding moonbags:   {len(result.still_riding)}  (${total_unrealized:.2f} unrealized, not in wallet total above)")
        for p in sorted(result.still_riding, key=lambda x: -x["unrealized_usd"])[:5]:
            print(f"  {p['token']}: {p['outcome']}, peaked {p['peak_multiple']}x, "
                  f"{p['remaining_pct']*100:.0f}% of stake still riding, ${p['unrealized_usd']:.2f} unrealized")
    else:
        print("Still-riding moonbags:   none this run")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet-usd", type=float, default=50.0)
    parser.add_argument("--candidates", type=int, default=200)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--compare-defense", action="store_true",
                         help="run the SAME synthetic world twice -- once with Layer 6 / "
                              "defensive_sell modeled, once without -- to show what it's worth")
    args = parser.parse_args()

    import tempfile, os

    if args.compare_defense:
        seed = args.seed if args.seed is not None else random.randint(0, 1_000_000)
        print(f"(using seed={seed} for both runs -- identical world, only defense toggles)\n")

        state.LOCAL_STATE_FILE = os.path.join(tempfile.mkdtemp(), "sim_state_on.json")
        result_on = run_simulation(args.wallet_usd, args.candidates, seed, defense_enabled=True)
        print_report(result_on, args.wallet_usd)

        print()
        state.LOCAL_STATE_FILE = os.path.join(tempfile.mkdtemp(), "sim_state_off.json")
        result_off = run_simulation(args.wallet_usd, args.candidates, seed, defense_enabled=False)
        print_report(result_off, args.wallet_usd)

        print()
        print("=" * 70)
        diff = result_on.realized_pnl_usd - result_off.realized_pnl_usd
        print(f"Defense ON realized P&L:  ${result_on.realized_pnl_usd:+.2f}")
        print(f"Defense OFF realized P&L: ${result_off.realized_pnl_usd:+.2f}")
        print(f"Difference this run: ${diff:+.2f}")
        print()
        print("NOTE: the two runs see the same candidates only UNTIL the circuit breaker")
        print("trips in one of them. Catching a rug early changes realized P&L, which")
        print("changes whether/when the breaker trips, which changes how many MORE")
        print("candidates that run even gets to evaluate. So a single run isn't a clean")
        print("A/B test -- run with --seed unset (or loop over many seeds) and look at the")
        print("AVERAGE difference, not any one run, for an honest read on what Layer 6 is worth.")
        print("=" * 70)
    else:
        state.LOCAL_STATE_FILE = os.path.join(tempfile.mkdtemp(), "sim_state.json")
        result = run_simulation(args.wallet_usd, args.candidates, args.seed)
        print_report(result, args.wallet_usd)

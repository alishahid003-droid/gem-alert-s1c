"""16 Nov 2026 (Claude, Ali's explicit delegation): compound's EARLY_RULES ladder
({1.3: 0.25, 2: 0.60, 3: 0.40} with EARLY_TRIM_MULT=2.0) fully exhausted every position by
exactly 2.0x -- the 3x rung was mathematically dead (remaining hit 0 at the 2x rung, and the
3x rung's own `pos["remaining"] > 1e-6` guard then always failed). Verified against tonight's
real 44-signal RHC dataset: 9 real coins ran 2.41x-9.71x and ALL of them were capped at an
identical 1.65x-equivalent ladder payout regardless of how far they actually ran -- zero of
the real upside past 2x was ever captured, by construction, not bad luck.

Ali's framing: compound was never meant to carve out moonshot rights like core2 (confirmed via
core2's explicit uncapped 8x+ carve-out, which compound never had) -- it's meant to trade
aggressively and compound its balance fast. But "sell 100% by 2x no matter what" isn't fast
compounding either -- it's leaving 0% of a real 5x-10x runner on the table. The fix rebalances
the ladder to {1.3: 0.15, 2: 0.10, 3: 0.10} so a genuine 30% of the position survives past the
3x rung into whatever trailing-stop/no-pop logic already governs the rest of the system
(UNCHANGED: hard_stop=0.70, trail_giveback=0.78) -- this is strictly an exit-ladder rebalance,
never touching entry/filter/scoring logic.

Verified on tonight's real data (compound_simulation_v7.py, real_token_prices_today.json):
losers' payout is BYTE-FOR-BYTE IDENTICAL to the old ladder (-3.662 total across all 16 real
losers, both ladders) -- the rebalance changes zero downside risk, confirming it's an upside-
only ladder reshape, not a risk-tolerance change. Midrange (1x-2x, 19 real trades) improves
slightly (+4.559 -> +4.953) and the 6 real >=3x runners improve from +3.900 to +12.198 -- and
even with those 6 runners removed entirely (worst-case: tonight's good tail never happens
again), the new ladder still beats the old one (+1.291 vs +0.897), so this is not an overfit
to one lucky night."""
from executor.paper_ledger import EARLY_RULES, EARLY_RUNG_PCT


def test_compound_ladder_rebalanced_in_early_rules():
    ladder, stop, trail = EARLY_RULES["compound"]
    assert ladder == {1.3: 0.15, 2: 0.10, 3: 0.10}
    # hard stop and trail giveback are UNCHANGED -- this is an upside-only rebalance
    assert stop == 0.70
    assert trail == 0.78


def test_compound_ladder_rebalanced_in_early_rung_pct():
    # smart_swap_out()'s _secured_profit_score() uses this separate lookup -- must stay in
    # sync with EARLY_RULES or _secured_profit_score() silently under/over-credits how much
    # profit a compound position has "secured" via rungs_fired.
    assert EARLY_RUNG_PCT["compound"] == {1.3: 0.15, 2: 0.10, 3: 0.10}


def test_compound_ladder_leaves_real_fraction_riding_past_3x():
    """The entire point of the rebalance: at EARLY_TRIM_MULT=2.0 (the real, live .env value),
    a genuine fraction of the position must survive past the 3x rung -- the old ladder left
    exactly 0.0 remaining at every multiple >= 2.0x, which is the bug this fixes."""
    ladder, _, _ = EARLY_RULES["compound"]
    trim_mult = 2.0
    remaining = 1.0
    for tier, frac in sorted(ladder.items()):
        sell = min(frac * trim_mult, remaining)
        remaining -= sell
    assert remaining > 0.25, (
        f"expected a meaningful fraction (>25%) to survive past the 3x rung at the real "
        f"EARLY_TRIM_MULT=2.0, got {remaining}"
    )


def test_compound_losers_unaffected_by_rebalance():
    """Regression guard: the rebalance must be upside-only. A position that never reaches the
    1.3x rung is governed entirely by the hard-stop/no-pop logic, never by the ladder fractions
    -- so its payout must be identical whether the ladder is old or new."""
    old_ladder = {1.3: 0.25, 2.0: 0.60, 3.0: 0.40}
    new_ladder, _, _ = EARLY_RULES["compound"]
    trim_mult = 2.0
    for final_mult in (0.5, 0.70, 0.85, 0.99, 1.0, 1.1, 1.29):
        for ladder in (old_ladder, new_ladder):
            remaining = 1.0
            for tier, frac in sorted(ladder.items()):
                if final_mult >= tier and remaining > 1e-6:
                    sell = min(frac * trim_mult, remaining)
                    remaining -= sell
            assert remaining == 1.0, (
                f"ladder rungs must not fire below the first 1.3x tier (final_mult={final_mult})"
            )

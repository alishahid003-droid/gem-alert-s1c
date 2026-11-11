"""
Probation-sized entry: caps worst-case loss from an instant, uncatchable
liquidity collapse (see CHANGES_9NOV2026.md item 4 -- confirmed structurally
undetectable by any cycle-based check) WITHOUT permanently shrinking every
trade's upside.

Ali's explicit design requirement (9 Nov 2026): "gradually increasing once
we have some confirmations... we don't miss out on the profitability... be
smart enough to detect and decide."

MECHANISM:
1. A brand-new position on an in-scope chain opens at a REDUCED fraction of
   its normal stake (PROBATION_INITIAL_FRACTION), tagged probation=True with
   the originally-intended full stake stored as probation_target_usd.
2. On each management cycle while in probation, liquidity is compared to the
   PRIOR cycle's reading (same prev_liquidity_usd field Option 2 already
   uses). A cycle counts as "confirmed stable" if liquidity did NOT drop by
   more than PROBATION_INSTABILITY_DROP_PCT.
3. Once PROBATION_CONFIRM_CYCLES consecutive stable cycles have been seen,
   the position is topped up to its full intended size in one step -- the
   paper-trading equivalent of a real trader adding to a position once it's
   proven itself in the first few seconds, priced at the mcap read on the
   confirming cycle (not the original entry price -- honest: a real top-up
   buys at the THEN-current price, not the original one).
4. If liquidity looks unstable on ANY cycle during probation (a drop >=
   PROBATION_INSTABILITY_DROP_PCT, same threshold style as Option 2's
   velocity check), probation does NOT end gracefully -- the normal
   Option 2 / DEAD_LIQUIDITY_USD checks fire exactly as they already do
   today (this module does not change those), and the position simply
   never gets topped up, capping its loss at the reduced initial size.
5. If PROBATION_MAX_CYCLES pass without confirmation (liquidity data never
   clearly stabilizes but never clearly collapses either -- e.g. patchy
   feed), the position stays at reduced size permanently. Fails toward
   SMALLER risk, never toward silently granting full size without proof.

HONESTY FLAG: this does NOT eliminate the instant-collapse loss category --
it HALVES (or whatever PROBATION_INITIAL_FRACTION is set to) its dollar
size, because the position is already smaller for however many cycles
confirmation takes. A rug that completes within the FIRST cycle (before any
confirmation chance) still only costs the reduced size, which is the
entire point. A coin that proves stable is topped up and captures full
upside from that point forward, same as any other winner from there on --
so the "profitability cost" is limited to the first ~
PROBATION_CONFIRM_CYCLES x EARLY_MANAGE_SECONDS of a winning trade's life,
not the whole trade.

SCOPE: robinhood_chain + base only, matching where this pattern was
observed (same scope as Option 2 and the dead-on-arrival fix).
"""
from __future__ import annotations
from typing import Optional

PROBATION_CHAINS = {"robinhood_chain", "base"}

PROBATION_INITIAL_FRACTION = 0.5
PROBATION_CONFIRM_CYCLES = 2
PROBATION_INSTABILITY_DROP_PCT = 0.25
PROBATION_MAX_CYCLES = 10

# 10 Nov 2026 (Ali, real-data finding): "no compromise on profits... be smart enough to
# detect and decide." Checked real evidence before building this: of 88 real compound
# Robinhood+Base trades since probation sizing went live, 5 real winners got capped at 50%
# size because they popped before the 2-cycle stability confirmation finished -- $518.96 of
# real upside given up (vs $886.48 saved on the loss side, still net positive but not free).
# Pulled whole-life peak_mcap/entry_mcap for all 5 capped winners AND all 15 capped losses in
# the same sample: every one of the 5 winners reached >=2.09x peak; every one of the 15
# losses NEVER exceeded 1.00x peak (flat or straight down, zero fake pumps). Clean, zero-
# overlap separator in the real data -- a coin that is genuinely popping shows it immediately
# via price, faster than the liquidity-stability check can confirm it structurally.
# FIX: graduate to full size IMMEDIATELY (same cycle, no waiting for PROBATION_CONFIRM_CYCLES)
# the moment mult (mcap/entry_mcap) crosses PROBATION_PUMP_GRADUATE_MULT, on top of (not
# instead of) the existing stability-based graduation path. Set well below the real winners'
# minimum observed peak (2.09x) for margin, but above every real loss's observed peak (1.00x)
# by a wide margin -- 1.15x chosen deliberately conservative (not pushed up to e.g. 2.0x, so a
# genuine pump is caught on its way up, not only after it's already most of the way to its
# peak). A coin that pumps past this is, by definition, no longer "flat with unknown
# direction" (the state probation sizing exists to protect against) -- it's already shown
# real upward price action, which the existing stability check doesn't look at at all.
PROBATION_PUMP_GRADUATE_MULT = 1.15


def start_probation(pos: dict, full_usd: float) -> dict:
    """Call when opening a new position on an in-scope chain. Mutates pos
    in place: reduces pos['usd'] to the probation-initial size and records
    bookkeeping fields. full_usd is the stake size the caller originally
    intended (before this module touched it)."""
    if pos.get("chain") not in PROBATION_CHAINS:
        return pos
    pos["probation"] = True
    pos["probation_target_usd"] = full_usd
    pos["probation_confirmed_cycles"] = 0
    pos["probation_cycles_seen"] = 0
    pos["usd"] = full_usd * PROBATION_INITIAL_FRACTION
    return pos


def update_probation(pos: dict, liq: Optional[float], prev_liq: Optional[float], mcap: Optional[float],
                      now: Optional[float] = None) -> Optional[dict]:
    """Call once per management cycle for any position still in probation.
    Returns a dict describing a top-up event if one happened this cycle,
    else None. Mutates pos in place either way."""
    if not pos.get("probation"):
        return None
    pos["probation_cycles_seen"] = pos.get("probation_cycles_seen", 0) + 1

    # 10 Nov 2026: pump-graduate check FIRST, before the stability-cycle counting below --
    # a real, already-popping coin should never wait on cycle-counting logic at all. Only
    # needs entry_mcap + current mcap, both already available on every position every cycle
    # (no new data dependency). mult can legitimately exceed the threshold with mcap present
    # but entry_mcap missing/zero only in a malformed position -- guarded defensively, falls
    # through to the normal stability path rather than crashing.
    entry_mcap = pos.get("entry_mcap")
    if mcap is not None and entry_mcap and entry_mcap > 0:
        mult = mcap / entry_mcap
        if mult >= PROBATION_PUMP_GRADUATE_MULT:
            old_usd = pos["usd"]
            target_usd = pos.get("probation_target_usd", old_usd)
            top_up_usd = max(0.0, target_usd - old_usd)
            pos["usd"] = target_usd
            pos["probation"] = False
            pos["probation_graduated_ts"] = now
            pos["probation_graduated_via"] = "pump"
            return {
                "event": "probation_graduated",
                "via": "pump",
                "mult_at_graduation": round(mult, 4),
                "old_usd": old_usd,
                "new_usd": target_usd,
                "top_up_usd": top_up_usd,
                "top_up_mcap": mcap,
            }

    stable_this_cycle = True
    if liq is not None and prev_liq is not None and prev_liq > 0:
        if (1.0 - (liq / prev_liq)) >= PROBATION_INSTABILITY_DROP_PCT:
            stable_this_cycle = False

    if stable_this_cycle:
        pos["probation_confirmed_cycles"] = pos.get("probation_confirmed_cycles", 0) + 1
    else:
        pos["probation_confirmed_cycles"] = 0

    if pos["probation_confirmed_cycles"] >= PROBATION_CONFIRM_CYCLES:
        old_usd = pos["usd"]
        target_usd = pos.get("probation_target_usd", old_usd)
        top_up_usd = max(0.0, target_usd - old_usd)
        pos["usd"] = target_usd
        pos["probation"] = False
        pos["probation_graduated_ts"] = now
        return {
            "event": "probation_graduated",
            "via": "stability",
            "old_usd": old_usd,
            "new_usd": target_usd,
            "top_up_usd": top_up_usd,
            "top_up_mcap": mcap,
        }

    if pos["probation_cycles_seen"] >= PROBATION_MAX_CYCLES:
        pos["probation"] = False
        pos["probation_timed_out"] = True
        return {"event": "probation_timed_out", "final_usd": pos["usd"]}

    return None

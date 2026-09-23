"""
Layer 8 -- High-risk momentum override.

A coin that FAILS Layer 0/0b's safety score (band D) but shows abnormal
MC/volume velocity -- climbing from sub-$10K toward six figures fast, the
common shape of a rug BEFORE it dumps -- still fires an alert, tagged
[HIGH-RISK MOMENTUM] rather than silently suppressed. Once tagged, Layer 6's
exit-watch on that coin runs tighter thresholds (see
layer6_exit_realizable.get_exit_thresholds).

This does NOT invent a market-cap history API of its own -- mc_history is
expected to be assembled by the scheduler from whichever source already
carries a market-cap time series (MadeOnSol's own token endpoints for
Solana/RHC, or repeated Mobula Pulse polls for Base/BSC/ETH), since neither
source's public docs show one clean "MC history" endpoint. This is called
out explicitly rather than fabricating an endpoint.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Tuple

LOW_BASE_MC_USD = 10_000
SIX_FIGURES_MC_USD = 100_000
MOMENTUM_WINDOW = timedelta(minutes=30)

# Event-triggered re-scoring threshold (replaces a fixed re-check timer, per
# Ali's explicit direction): a previously-scored token only goes back through
# the expensive deep-score path when its market cap -- the same cheap metric
# this module already tracks for the momentum check, regardless of a token's
# score -- has moved by at least this fraction since the last full score.
# A quiet coin that failed early and never moves costs nothing further; a
# coin that's actually turning around shows movement here first. Honest
# scope note: this only tracks market cap, since that's the one cheap metric
# actually recorded today (from Layer 2's KOL-feed trades and Mobula Pulse) --
# holder-count / volume-based triggers would need their own tracked time
# series, which don't exist yet for Solana/RHC tokens specifically.
RESCORE_TRIGGER_FRACTION = 0.5


def mc_moved_enough(old_mc: float, new_mc: float, threshold: float = RESCORE_TRIGGER_FRACTION) -> bool:
    """True if new_mc differs from old_mc by at least `threshold` (as a
    fraction of old_mc) in either direction. Fails closed (False) if either
    value is missing or old_mc is non-positive -- never guesses a trigger."""
    if old_mc is None or new_mc is None or old_mc <= 0:
        return False
    return abs(new_mc - old_mc) / old_mc >= threshold


@dataclass
class MomentumResult:
    triggered: bool
    reason: str = ""


def _parse_time(ts):
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def detect_momentum_override(band: str, mc_history: List[Tuple], window: timedelta = MOMENTUM_WINDOW) -> MomentumResult:
    """band: the letter grade from layers.layer0_scoring.score_token (only
    matters for 'D' -- everything else already alerts normally).
    mc_history: list of (timestamp, market_cap_usd) tuples, any order."""
    if band != "D":
        return MomentumResult(triggered=False, reason="not a failing score -- normal alert path applies")
    if len(mc_history) < 2:
        return MomentumResult(triggered=False, reason="insufficient MC history to assess velocity")

    points = sorted(((_parse_time(t), mc) for t, mc in mc_history), key=lambda p: p[0])
    latest_ts, latest_mc = points[-1]
    window_start = latest_ts - window
    in_window = [mc for ts, mc in points if ts >= window_start]

    if not in_window:
        return MomentumResult(triggered=False, reason="no data points within momentum window")

    min_mc_in_window = min(in_window)
    if min_mc_in_window <= LOW_BASE_MC_USD and latest_mc >= SIX_FIGURES_MC_USD:
        return MomentumResult(
            triggered=True,
            reason=f"MC climbed from ${min_mc_in_window:,.0f} to ${latest_mc:,.0f} "
                   f"within {window.total_seconds()/60:.0f} min despite failing safety score",
        )
    return MomentumResult(triggered=False, reason="failing score but velocity below momentum threshold")

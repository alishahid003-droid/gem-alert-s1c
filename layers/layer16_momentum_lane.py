"""
Layer 16 -- Momentum lane for the 5-day sprint (Oct 1 2026).

Ali: "band A/B coins might not even come in a day -- is there a momentum
lane: go in a coin that's running, get out in 15-20 minutes, go into the
next one, multiple trades?"

A quick in/out lane on coins that are ALREADY trending (GeckoTerminal
trending + new pools, stored by poll-fast), re-priced every fast-watch tick
through one free DexScreener batch call per chain. A coin qualifies on a
live buying burst -- every gate below must pass:

  liquidity >= $30k, and >= 4% of market cap (not a thin, pushable pool)
  market cap $50k - $50M; pair at least 30 min old
  5-min change +2% .. +25% (rising now, not a vertical candle)
  1-h change >= +8%
  buys >= 1.3x sells in the last hour, >= 100 trades in the last hour
  1-h volume >= $20k

Exit profile "quick" (executor.compound_scalper.scalp_exit_decision):
take 70% at +12%, trail the rest 6% from its peak, stop at -8%, and leave
after 20 minutes whatever happens. UNTESTED ON HISTORY -- every qualifier
is paper-traded (source "momentum_lane") so its real win rate shows on the
dashboard, and the lane pauses itself if its first real trades lose
(executor/sprint.py). Buy-time safety (sell-leg quote, honeypot/tax checks)
runs inside every real buy as always.
"""
import os
import time
from typing import Optional

from layers.layer14_revival import momentum_metrics


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def burst(pair: dict, now: Optional[float] = None) -> tuple:
    """(qualifies, score 0-100, metrics, failures)."""
    now = now or time.time()
    m = momentum_metrics(pair)
    fails = []
    liq, mcap = m.get("liquidity_usd") or 0, m.get("mcap_usd") or 0
    m5, h1 = m.get("change_m5"), m.get("change_h1")
    buys, sells = m.get("buys_h1") or 0, m.get("sells_h1") or 0
    vol = m.get("volume_h1") or 0
    created = m.get("pair_created_at")
    age_min = (now - created / 1000) / 60 if isinstance(created, (int, float)) else None

    if liq < _f("LANE_MIN_LIQ", 30_000):
        fails.append(f"liquidity ${liq:,.0f}")
    if mcap and liq < mcap * _f("LANE_MIN_LIQ_RATIO", 0.04):
        fails.append("pool too thin for its mcap")
    if not (_f("LANE_MIN_MCAP", 50_000) <= mcap <= _f("LANE_MAX_MCAP", 50_000_000)):
        fails.append(f"mcap ${mcap:,.0f}")
    if age_min is None or age_min < _f("LANE_MIN_AGE_MIN", 30):
        fails.append("too new")
    if m5 is None or not (_f("LANE_MIN_M5", 2) <= m5 <= _f("LANE_MAX_M5", 25)):
        fails.append(f"5m {m5}%")
    if h1 is None or h1 < _f("LANE_MIN_H1", 8):
        fails.append(f"1h {h1}%")
    if buys < max(1, sells) * _f("LANE_BUY_SELL", 1.3):
        fails.append(f"buys {buys} vs sells {sells}")
    if buys + sells < _f("LANE_MIN_TXNS_H1", 100):
        fails.append(f"{buys + sells} trades/h")
    if vol < _f("LANE_MIN_VOL_H1", 20_000):
        fails.append(f"1h vol ${vol:,.0f}")

    score = 0.0
    if sells:
        score += min(35, (buys / sells - 1) * 35)
    score += min(25, (buys + sells) / 20)
    if m5 is not None:
        score += min(20, max(0, m5) * 2)
    if mcap:
        score += min(20, liq / mcap * 200)
    return not fails, int(min(100, score)), m, fails

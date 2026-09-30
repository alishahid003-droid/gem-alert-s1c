"""
Layer 15 -- Moonshot detector (Sept 30 2026).

Ali: "coins explode every week -- PAWNS, MARS, SHROOM -- people put in $1,500
and cashed out a million. What is our moonshot strategy, how do we identify
it, what size, is it a separate module?"

Honest framing, which shapes the design: nobody can tell at launch which of
~30,000 daily memecoins becomes the 1000x one -- the people who made 1000x
were early AND lucky, and most coins bought that early die. What IS
measurable is ESCAPE VELOCITY: a coin that has already survived the rug zone
and is being bought by a real, growing crowd. A coin at $300k-$5M that
becomes a $100M-$1B coin is still a 30x-1000x from there. So this layer:

  1. screens coins that are already RUNNING (not launching) on all four
     chains -- trending + new pools the scanner already fetches, re-priced in
     ONE free DexScreener batch call per chain;
  2. requires every escape-velocity gate below (sustained multi-hour run, a
     real crowd, real turnover, deep enough liquidity, not a single vertical
     candle);
  3. hands qualifiers to executor.entrypoint.handle_moonshot_candidate:
     always a paper trade + a Telegram alert; a real buy only with
     MOONSHOT_ENABLED=true, band B+ safety, and the normal entry guards;
  4. moonshot positions use the "moonshot" exit profile (exit_rules): take
     the stake back at 2x, then HALF the position rides as a runner that only
     a deep 75% trail (after 5x), a rug signal, or the 10x/50x ladder sells.

Gates (all env-tunable, MOONSHOT_*):
  mcap $150k-$5M, pair age 1-72 h, 1h change >= +25% AND 6h >= +80%,
  5m change between -15% and +40%, buys >= 1.3x sells in the last hour,
  >= 150 trades in the last hour, 1h volume >= $50k, 24h volume >= 50% of
  mcap, liquidity >= $40k and >= 6% of mcap.
Everything here is pure except the batch fetch it reuses from Layer 14.
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


def metrics(pair: dict) -> dict:
    m = momentum_metrics(pair)
    pc = pair.get("priceChange") or {}
    try:
        m["change_h6"] = float(pc["h6"]) if pc.get("h6") is not None else None
    except (TypeError, ValueError):
        m["change_h6"] = None
    try:
        m["change_h24"] = float(pc["h24"]) if pc.get("h24") is not None else None
    except (TypeError, ValueError):
        m["change_h24"] = None
    return m


def evaluate(m: dict, now: Optional[float] = None) -> tuple:
    """(qualifies, score 0-100, reasons/failures list)."""
    now = now or time.time()
    fails, score = [], 0
    mcap = m.get("mcap_usd") or 0
    liq = m.get("liquidity_usd") or 0
    created = m.get("pair_created_at")
    age_h = (now - created / 1000) / 3600 if isinstance(created, (int, float)) else None
    h1, h6, m5 = m.get("change_h1"), m.get("change_h6"), m.get("change_m5")
    buys, sells = m.get("buys_h1") or 0, m.get("sells_h1") or 0
    vol_h1, vol_h24 = m.get("volume_h1") or 0, m.get("volume_h24") or 0

    def gate(ok, why):
        if not ok:
            fails.append(why)
        return ok

    gate(_f("MOONSHOT_MIN_MCAP", 150_000) <= mcap <= _f("MOONSHOT_MAX_MCAP", 5_000_000),
         f"mcap ${mcap:,.0f} outside ${_f('MOONSHOT_MIN_MCAP', 150_000):,.0f}-${_f('MOONSHOT_MAX_MCAP', 5_000_000):,.0f}")
    gate(age_h is not None and _f("MOONSHOT_MIN_AGE_H", 1) <= age_h <= _f("MOONSHOT_MAX_AGE_H", 72),
         f"age {age_h if age_h is None else round(age_h, 1)} h outside 1-72 h")
    gate(h1 is not None and h1 >= _f("MOONSHOT_MIN_H1_PCT", 25), f"1h {h1}% < +25%")
    gate(h6 is not None and h6 >= _f("MOONSHOT_MIN_H6_PCT", 80), f"6h {h6}% < +80% (not a sustained run)")
    gate(m5 is not None and _f("MOONSHOT_MIN_M5_PCT", -15) <= m5 <= _f("MOONSHOT_MAX_M5_PCT", 40),
         f"5m {m5}% (dumping, or a vertical candle)")
    gate(buys >= max(1, sells) * _f("MOONSHOT_BUY_SELL_RATIO", 1.3), f"buys {buys} vs sells {sells}")
    gate(buys + sells >= _f("MOONSHOT_MIN_TXNS_H1", 150), f"only {buys + sells} trades in 1h")
    gate(vol_h1 >= _f("MOONSHOT_MIN_VOL_H1", 50_000), f"1h volume ${vol_h1:,.0f} < $50k")
    gate(mcap > 0 and vol_h24 >= mcap * _f("MOONSHOT_MIN_TURNOVER", 0.5), "24h volume < 50% of mcap")
    gate(liq >= _f("MOONSHOT_MIN_LIQ", 40_000) and (mcap <= 0 or liq >= mcap * _f("MOONSHOT_MIN_LIQ_RATIO", 0.06)),
         f"liquidity ${liq:,.0f} too thin for ${mcap:,.0f} mcap")

    # Score (for ranking / the alert): how strong the run is beyond the gates.
    if h6:
        score += min(30, h6 / 10)
    if sells:
        score += min(20, (buys / sells - 1) * 20)
    score += min(20, (buys + sells) / 50)
    if mcap:
        score += min(15, vol_h24 / mcap * 5)
        score += min(15, liq / mcap * 100)
    return not fails, int(min(100, score)), fails

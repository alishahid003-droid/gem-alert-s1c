"""
Entry guards -- GO_LIVE_CHECKLIST 3.1 / 3.3 / 3.4 / 3.5 / 3.6 (Sept 30 2026).

Quality filters applied to every REAL buy after the trigger fires (Stage 1
and the compound scalper). Pure, no network: the caller passes what it
already knows about the coin in `ctx`:
  liquidity_usd, change_m5 / change_h1 (percent), sniper_pct (0-1),
  signals (int: independent confirming signals, for confluence).

  3.3 liquidity   pool >= GUARD_MIN_LIQ_USD and our buy <= GUARD_MAX_POOL_SHARE
                  of it (a big bite into a thin pool is the move, not a trade).
  3.4 late entry  not after a vertical candle: 5-min change <= GUARD_MAX_M5_PCT
                  and (non-momentum only) 1-h change <= GUARD_MAX_H1_PCT.
  3.5 snipers     sniper/bundler share < GUARD_MAX_SNIPER_PCT when known.
  3.6 band        real money needs at least REAL_MONEY_MIN_BAND.
  9.3/9.4         dev+sniper share < GUARD_MAX_DEV_SNIPER_PCT; bot_farm demand verdict blocks.
  3.1 confluence  optional (REQUIRE_CONFLUENCE=true): >= 2 independent signals.

Unknown data never blocks (it's already weighed by the scorer's real-data
gate); only a confirmed bad value does. Every threshold is env-tunable and
the paper ledger tags each paper trade with the guard verdict, so the
scoreboard shows whether a guard actually improves the win rate.
"""
import os
from typing import Optional, Tuple

BAND_RANK = {"D": 0, "C": 1, "B": 2, "A": 3}


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def check(score_band: Optional[str], position_usd: float, ctx: Optional[dict] = None,
          momentum: bool = False) -> Tuple[bool, str]:
    ctx = ctx or {}
    min_band = os.environ.get("REAL_MONEY_MIN_BAND", "B").strip().upper() or "B"
    if momentum:
        # Replay of 24 h of live alerts (Sept 30): band C lost on 12 of 13
        # decided trades (mostly -64..-66% rug candles) while band B won 4 of 5.
        # So real money needs B even for momentum entries; band-C momentum
        # keeps paper-trading (compound_scalper.signal_qualifies) to earn it back.
        min_band = os.environ.get("REAL_MONEY_MOMENTUM_MIN_BAND", "B").strip().upper() or "B"
    # The band floor applies to score-driven buys only: a trusted-deployer or
    # wallet-convergence fire is an independent signal that is allowed below
    # band B by design (executor.triggers.evaluate_stage1).
    independent = int(ctx.get("signals") or 1) >= 2
    # The sprint momentum lane (layers/layer16) trades a live buying burst,
    # not a structural score; its own gates (liquidity, depth, crowd) plus
    # the buy-time sell-leg / honeypot checks stand in for the band floor.
    lane = ctx.get("lane") == "momentum"
    if not independent and not lane and BAND_RANK.get(score_band or "", -1) < BAND_RANK.get(min_band, 2):
        return False, f"band {score_band or '?'} below real-money minimum {min_band}"

    liq = ctx.get("liquidity_usd")
    if liq is not None:
        if liq < _f("GUARD_MIN_LIQ_USD", 10_000):
            return False, f"pool liquidity ${liq:,.0f} below ${_f('GUARD_MIN_LIQ_USD', 10_000):,.0f}"
        share = position_usd / liq if liq else 1.0
        if share > _f("GUARD_MAX_POOL_SHARE", 0.02):
            return False, f"buy would be {share * 100:.1f}% of the pool (max {_f('GUARD_MAX_POOL_SHARE', 0.02) * 100:.0f}%)"

    m5, h1 = ctx.get("change_m5"), ctx.get("change_h1")
    if m5 is not None and m5 > _f("GUARD_MAX_M5_PCT", 50):
        return False, f"already +{m5:.0f}% in 5 min -- buying the top of a vertical candle"
    if not momentum and h1 is not None and h1 > _f("GUARD_MAX_H1_PCT", 300):
        return False, f"already +{h1:.0f}% in 1 h -- late entry"

    sn = ctx.get("sniper_pct")
    if sn is not None and sn >= _f("GUARD_MAX_SNIPER_PCT", 0.25):
        return False, f"snipers/bundlers hold {sn * 100:.0f}%"

    # 9.4 combined dev + sniper cap, 9.3 bot-farm demand (layers/layer15_buyer_quality.py)
    dev = ctx.get("dev_pct")
    if dev is not None and sn is not None:
        if dev + sn >= _f("GUARD_MAX_DEV_SNIPER_PCT", 0.30):
            return False, f"dev + snipers hold {(dev + sn) * 100:.0f}% (cap {_f('GUARD_MAX_DEV_SNIPER_PCT', 0.30) * 100:.0f}%)"
    if ctx.get("demand_verdict") == "bot_farm":
        return False, "buyers look like one bot farm (few funders / identical sizes)"

    if os.environ.get("REQUIRE_CONFLUENCE", "").strip().lower() == "true":
        if int(ctx.get("signals") or 1) < 2:
            return False, "only one confirming signal (REQUIRE_CONFLUENCE on)"

    return True, "all entry guards pass"

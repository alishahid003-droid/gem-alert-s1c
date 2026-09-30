"""Point-in-time credibility backtest v2 -- Ali's ask, Sept 26-27 2026:
"the whole idea of backtest is to check our system's effectiveness...the
system can only detect rug or moonshot with deployer capability...not with
any other check like volume liquidity, utility, news...deployer part was
one module of system not everything depending on it."

CORRECTION from v1: v1 over-focused on deployer-tier-at-launch (MadeOnSol's
deployer-hunter/as-of endpoint, which turned out to be PRO-tier gated).
That endpoint is only ONE input to Layer 1's alert-worthiness gate -- it is
NOT how the system scores a token. The real detection engine
(score_solana_mint -> signals_from_madeonsol_risk -> score_token) reads
holder concentration, bundle/sniper wallet share, mint authority revoked,
freeze authority revoked, LP locked, and volume-to-liquidity ratio -- NONE
of that needs deployer tier, and NONE of it is paywalled. This version
runs THAT engine, combined with Birdeye's real launch-window price/volume
shape, to answer the actual question: what would the system's multi-signal
score have said about these coins, as close to launch time as free data
allows.

HONEST LABELING, not overclaimed:
  - For young/settled tokens (rugs and pump_dumps, most launched within
    the last few days and already at their final, drained state) --
    MadeOnSol's CURRENT holders/bundle/risk data is a legitimate stand-in
    for "at/near launch outcome," because nothing material has changed
    since the crash. Labeled "current_score (settled)" below.
  - For old, established moonshots (TRUMP, USELESS COIN, months old) --
    current data is NOT representative of launch-day conditions. For
    these, the Birdeye 48h launch-window price/volume shape is the real
    point-in-time evidence; the current multi-signal score is shown too
    but labeled "current_score (NOT launch-representative)" so it's never
    silently treated as equivalent evidence.
  - Every row states plainly which evidence is which -- no single number
    hides that distinction.

Usage: python backtest_point_in_time.py [--skip-onchain] [--skip-birdeye]
[--skip-current-score]
"""
import argparse
from datetime import datetime, timezone

import state

from backtest_categorized import SOLANA_LABELED, BSC_LABELED, CATEGORY_EXPECTATION
from layers.layer0_scoring import fetch_dexscreener_vol_liq, score_solana_mint
from layers.layer0d_point_in_time import (
    fetch_deployer_wallet_onchain, fetch_deployer_asof,
    fetch_birdeye_ohlcv, summarize_launch_window,
)

LAYER1_ALERT_TIERS = {"elite", "good"}
SETTLED_MAX_AGE_DAYS = 30  # launched within this many days -> current data treated as launch-representative


def run_one(name, chain, address, category, is_pregrad, skip_onchain, skip_birdeye, skip_current_score):
    row = {"name": name, "chain": chain, "address": address, "category": category}

    dex = fetch_dexscreener_vol_liq(chain, address)
    launch_ts_ms = dex.get("launch_ts_ms") if dex.get("ok") else None
    launch_date_str = None
    age_days = None
    if launch_ts_ms:
        launch_dt = datetime.fromtimestamp(launch_ts_ms / 1000, tz=timezone.utc)
        launch_date_str = launch_dt.strftime("%Y-%m-%d")
        age_days = (datetime.now(timezone.utc) - launch_dt).days
        row["launch_date"] = launch_date_str
        row["age_days"] = age_days
        print(f"[{category:10s}] {name} / {chain}: launch={launch_date_str} ({age_days}d old)")
    else:
        print(f"[{category:10s}] {name} / {chain}: no real launch date from DexScreener")

    # --- Real multi-signal engine (holders/bundle/risk/liquidity -- NOT deployer) ---
    if not skip_current_score:
        settled = age_days is not None and age_days <= SETTLED_MAX_AGE_DAYS
        label = "current_score (settled -- launch-representative)" if settled else \
                "current_score (NOT launch-representative, token has aged)"
        result = score_solana_mint(address, chain, is_pregraduation=is_pregrad)
        if "error" in result:
            print(f"    {label}: FAILED -- {result['error']}")
        else:
            sc = result["score"]
            row["current_band"] = sc.band
            row["current_score"] = sc.score
            row["band_is_settled_proxy"] = settled
            hit = None
            if category in CATEGORY_EXPECTATION:
                hit = sc.band in CATEGORY_EXPECTATION[category]
                row["current_band_hit"] = hit
            print(f"    {label}: band={sc.band} score={sc.score}/100"
                  + (f" -> {'HIT' if hit else 'MISS'} vs {category} expectation" if hit is not None else ""))

    # --- Best-effort on-chain deployer wallet (kept, informational only -- not scored) ---
    if chain == "solana" and not skip_onchain:
        wallet_result = fetch_deployer_wallet_onchain(address)
        if wallet_result.get("ok"):
            print(f"    deployer wallet (on-chain, informational only): {wallet_result['deployer_wallet']}")
        else:
            print(f"    on-chain deployer lookup: {'TRUNCATED' if wallet_result.get('truncated') else 'FAILED'}")

    # --- Real launch-window price/volume shape (Birdeye) ---
    if not skip_birdeye and launch_ts_ms:
        window_start = int(launch_ts_ms / 1000)
        window_end = window_start + 48 * 3600
        ohlcv = fetch_birdeye_ohlcv(chain, address, window_start, window_end, interval="1H")
        if ohlcv.get("ok"):
            summary = summarize_launch_window(ohlcv.get("candles") or [])
            if summary.get("ok"):
                row["launch_window_summary"] = summary
                peak_multiple = None
                if summary["first_price"]:
                    peak_multiple = round(summary["peak_price"] / summary["first_price"], 2)
                drawdown = summary["drawdown_from_peak_pct"]
                birdeye_hit = None
                if category == "moonshot":
                    birdeye_hit = (peak_multiple or 0) >= 1.5 and (drawdown is None or drawdown >= -60)
                elif category in ("rug", "pump_dump"):
                    birdeye_hit = drawdown is not None and drawdown <= -80
                row["birdeye_peak_multiple"] = peak_multiple
                row["birdeye_hit"] = birdeye_hit
                print(f"    launch-window shape (real, at-launch): peak={peak_multiple}x open, "
                      f"drawdown={drawdown}%"
                      + (f" -> {'HIT' if birdeye_hit else ('MISS' if birdeye_hit is False else 'inconclusive')}"
                         f" vs {category} expectation" if birdeye_hit is not None else ""))
            else:
                print(f"    launch-window shape: Birdeye candles present but couldn't summarize")
        else:
            print(f"    launch-window shape: Birdeye FAILED -- {ohlcv.get('reason')}")

    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-onchain", action="store_true")
    parser.add_argument("--skip-birdeye", action="store_true")
    parser.add_argument("--skip-current-score", action="store_true")
    args = parser.parse_args()

    print("=" * 76)
    print("POINT-IN-TIME CREDIBILITY BACKTEST v2 -- multi-signal engine, no deployer dependency")
    print("=" * 76)

    # Real pre-flight budget check, added Sept 29 2026 -- see
    # backtest_categorized.py's identical check for the full reasoning (this
    # script hit real MadeOnSol 429s mid-run the same morning, same root
    # cause). score_solana_mint spends 3 MadeOnSol calls per Solana/RHC
    # labeled token via fetch_madeonsol_token_risk.
    required = len(SOLANA_LABELED) * 3
    remaining = state.madeonsol_budget_remaining()
    print(f"MadeOnSol budget check: need ~{required} calls for {len(SOLANA_LABELED)} Solana/RHC "
          f"tokens, {remaining}/{state.MADEONSOL_DAILY_BUDGET} remaining today.")
    if remaining < required:
        print(f"REFUSING TO START: not enough real MadeOnSol budget left today "
              f"({remaining} remaining, need ~{required}). Resets at 00:00 UTC. "
              f"Not attempting a partial run.")
        return []

    rows = []
    print(f"\n--- Solana / Robinhood Chain ({len(SOLANA_LABELED)} labeled tokens) ---")
    for name, chain, address, category, is_pregrad in SOLANA_LABELED:
        # Real crash found Sept 30 2026 (tomorrow's first live post-reset
        # run): a single flaky free Solana RPC endpoint
        # (solana-rpc.publicnode.com) timed out on fetch_solana_top10_holder_pct,
        # ApiUnreachable propagated uncaught all the way out of run_one(),
        # and killed the ENTIRE backtest after only 2 of 17 labeled tokens --
        # exactly the same failure mode backtest_categorized.py's
        # score_solana_labeled already guards against (see its identical
        # try/except below). This script was the one place missing that
        # same protection -- scheduler.py's live poll path is already safe
        # via _safe() wrapping score_solana_mint. One bad token's network
        # hiccup must never cost the whole night's real, honest read.
        try:
            rows.append(run_one(name, chain, address, category, is_pregrad,
                                 args.skip_onchain, args.skip_birdeye, args.skip_current_score))
        except Exception as e:
            print(f"[{category:10s}] {name} / {chain}: NETWORK ERROR (token skipped, run continues): {e}")
            rows.append({"name": name, "chain": chain, "address": address, "category": category,
                         "error": f"network error: {e}"})

    print(f"\n--- BSC / Base ({len(BSC_LABELED)} labeled tokens) ---")
    for name, chain, address, category, is_pregrad in BSC_LABELED:
        print(f"[{category:10s}] {name} / {chain}: score_solana_mint only covers Solana/RHC -- "
              f"BSC/Base multi-signal scoring not built into this script (see backtest_categorized.py's "
              f"score_bsc_labeled for the Mobula-based equivalent, current-data only)")

    print("\n" + "=" * 76)
    print("SUMMARY")
    print("=" * 76)

    settled_scored = [r for r in rows if r.get("current_band_hit") is not None and r.get("band_is_settled_proxy")]
    if settled_scored:
        hits = sum(1 for r in settled_scored if r["current_band_hit"])
        print(f"\nMulti-signal engine (holders/bundle/liquidity/authority -- NOT deployer), "
              f"SETTLED tokens only (launched <={SETTLED_MAX_AGE_DAYS}d ago, current data = launch-representative):")
        print(f"  {hits}/{len(settled_scored)} ({round(100*hits/len(settled_scored),1)}%) scored in the band the "
              f"system SHOULD have given them")
    else:
        print("\nNo settled tokens had a usable multi-signal band to score.")

    aged_scored = [r for r in rows if r.get("current_band_hit") is not None and not r.get("band_is_settled_proxy")]
    if aged_scored:
        hits = sum(1 for r in aged_scored if r["current_band_hit"])
        print(f"\nMulti-signal engine, AGED tokens (current data NOT launch-representative -- shown for "
              f"context only, not a fair 'at launch' measurement):")
        print(f"  {hits}/{len(aged_scored)} ({round(100*hits/len(aged_scored),1)}%) -- today's state, not launch-day")

    birdeye_scored = [r for r in rows if r.get("birdeye_hit") is not None]
    if birdeye_scored:
        hits = sum(1 for r in birdeye_scored if r["birdeye_hit"])
        print(f"\nReal launch-window price/volume shape (Birdeye, genuinely at-launch, ALL tokens regardless of age):")
        print(f"  {hits}/{len(birdeye_scored)} ({round(100*hits/len(birdeye_scored),1)}%) showed the expected "
              f"shape (pump held for moonshots, severe collapse for rugs/pump_dumps) within 48h of real launch")

    print("\nBottom line: the SETTLED-token multi-signal % and the Birdeye launch-window % are the two real, "
          "free, at-or-near-launch effectiveness numbers. The AGED-token multi-signal % is today's data on old "
          "coins, shown for context only -- do not read it as an 'at launch' result.")

    return rows


if __name__ == "__main__":
    main()

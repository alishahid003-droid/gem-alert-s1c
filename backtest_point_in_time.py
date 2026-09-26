"""Point-in-time credibility backtest -- Ali's ask, Sept 26 2026: "we need
to run it on back data...that if our system was live when these coins
launched what would our system would have recommended and done...only
then we come to know the credibility and effectiveness of our system."

Reuses the exact same labeled coin list backtest_categorized.py already
has (SOLANA_LABELED/BSC_LABELED) -- no new labeling work, just a different
question asked of the same real tokens: not "what does this look like
today" but "what did this look like AT LAUNCH, and would Layer 1 have let
it through."

HONEST SCOPE, see layers/layer0d_point_in_time.py's docstring for the full
explanation of why: this does NOT reconstruct MadeOnSol's full risk/
holders/bundle score at launch time -- no provider wired into this
codebase exposes that historically. What it DOES check per token, all from
real point-in-time or launch-anchored data:

  1. Real launch date (DexScreener pairCreatedAt)
  2. Best-effort on-chain deployer wallet (may fail/truncate for old
     high-volume tokens -- see layer0d_point_in_time.py)
  3. IF a deployer wallet was found: MadeOnSol's real point-in-time
     deployer-tier snapshot AS OF the launch date -- would Layer 1's
     elite/good-tier filter have let this token's alert through at all
  4. IF BIRDEYE_API_KEY is set: real historical price/volume for the first
     48h after launch, reduced to peak price / end-of-window drawdown --
     lets you SEE whether a "moonshot" was already a moonshot in its first
     48h (a fair test) vs a "rug"/"pump_dump" already showing the
     dump-shape early, rather than just knowing today's outcome.

Categories and expectation, same convention as backtest_categorized.py:
  moonshot   -> deployer tier SHOULD be elite/good (a real one usually is)
  rug        -> deployer tier being unranked/low is a plausible catch;
                elite/good would mean Layer 1 wouldn't have flagged it,
                a real miss worth knowing about
  pump_dump  -> same logic as rug
  flat       -> no strong expectation either way, reported not scored

Usage: python backtest_point_in_time.py [--skip-onchain] [--skip-birdeye]
--skip-onchain skips the on-chain deployer-wallet lookup entirely (useful
if you just want the launch-date + Birdeye pieces fast); --skip-birdeye
skips Birdeye even if the key is set.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

from backtest_categorized import SOLANA_LABELED, BSC_LABELED
from layers.layer0_scoring import fetch_dexscreener_vol_liq
from layers.layer0d_point_in_time import (
    fetch_deployer_wallet_onchain, fetch_deployer_asof,
    fetch_birdeye_ohlcv, summarize_launch_window,
)

LAYER1_ALERT_TIERS = {"elite", "good"}


def run_one(name: str, chain: str, address: str, category: str, skip_onchain: bool, skip_birdeye: bool) -> dict:
    row = {"name": name, "chain": chain, "address": address, "category": category}

    dex = fetch_dexscreener_vol_liq(chain, address)
    launch_ts_ms = dex.get("launch_ts_ms") if dex.get("ok") else None
    if not launch_ts_ms:
        row["launch_date"] = None
        print(f"[{category:10s}] {name} / {chain}: no real launch date from DexScreener -- "
              f"skipping point-in-time checks for this token")
        return row
    launch_dt = datetime.fromtimestamp(launch_ts_ms / 1000, tz=timezone.utc)
    launch_date_str = launch_dt.strftime("%Y-%m-%d")
    row["launch_date"] = launch_date_str
    print(f"[{category:10s}] {name} / {chain}: real launch date = {launch_date_str}")

    if chain == "solana" and not skip_onchain:
        wallet_result = fetch_deployer_wallet_onchain(address)
        if wallet_result.get("ok"):
            wallet = wallet_result["deployer_wallet"]
            row["deployer_wallet"] = wallet
            print(f"    deployer wallet found on-chain in {wallet_result['pages_used']} page(s): {wallet}")
            asof = fetch_deployer_asof(wallet, chain, launch_date_str)
            if asof.get("ok") and asof.get("as_of") and asof.get("snapshot"):
                tier = asof["snapshot"].get("tier")
                row["deployer_tier_at_launch"] = tier
                would_pass_layer1 = tier in LAYER1_ALERT_TIERS
                row["would_pass_layer1"] = would_pass_layer1
                print(f"    deployer tier AS OF {launch_date_str}: {tier} "
                      f"-> Layer 1 would{'' if would_pass_layer1 else ' NOT'} have alerted on this")
            elif asof.get("ok"):
                print(f"    MadeOnSol has no reputation snapshot for this wallet at/before "
                      f"{launch_date_str} -- deployer wasn't tracked yet")
                row["deployer_tier_at_launch"] = None
            else:
                print(f"    deployer as-of lookup FAILED: {asof.get('reason')}")
        else:
            reason = wallet_result.get("reason")
            print(f"    on-chain deployer wallet lookup: {'TRUNCATED' if wallet_result.get('truncated') else 'FAILED'} "
                  f"-- {reason}")
    elif chain != "solana" and not skip_onchain:
        print(f"    on-chain deployer lookup skipped -- only implemented for Solana "
              f"(chain={chain!r} not supported)")

    if not skip_birdeye:
        window_start = int(launch_ts_ms / 1000)
        window_end = window_start + 48 * 3600
        ohlcv = fetch_birdeye_ohlcv(chain, address, window_start, window_end, interval="1H")
        if ohlcv.get("ok"):
            summary = summarize_launch_window(ohlcv.get("candles") or [])
            if summary.get("ok"):
                row["launch_window_summary"] = summary
                print(f"    first-48h window: open={summary['first_price']} "
                      f"peak={summary['peak_price']} "
                      f"drawdown_from_peak={summary['drawdown_from_peak_pct']}% "
                      f"({summary['num_candles']} candles)")
            else:
                print(f"    Birdeye returned candles but couldn't summarize: {summary.get('reason')}")
        else:
            print(f"    Birdeye historical OHLCV FAILED: {ohlcv.get('reason')} "
                  f"(status_code={ohlcv.get('status_code')})")

    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-onchain", action="store_true")
    parser.add_argument("--skip-birdeye", action="store_true")
    args = parser.parse_args()

    print("=" * 70)
    print("POINT-IN-TIME CREDIBILITY BACKTEST")
    print("See module docstring for exact scope -- this is NOT a full")
    print("historical risk/holders/bundle replay (no provider exposes that),")
    print("it's real launch-date + point-in-time deployer-tier + launch-")
    print("window price data, the pieces that genuinely are available.")
    print("=" * 70)

    rows = []
    print(f"\n--- Solana / Robinhood Chain ({len(SOLANA_LABELED)} labeled tokens) ---")
    for name, chain, address, category, _ in SOLANA_LABELED:
        rows.append(run_one(name, chain, address, category, args.skip_onchain, args.skip_birdeye))

    print(f"\n--- BSC / Base ({len(BSC_LABELED)} labeled tokens) ---")
    for name, chain, address, category, _ in BSC_LABELED:
        rows.append(run_one(name, chain, address, category, args.skip_onchain, args.skip_birdeye))

    scored = [r for r in rows if r.get("would_pass_layer1") is not None and r["category"] != "flat"]
    if scored:
        hits = 0
        for r in scored:
            expected_pass = r["category"] == "moonshot"
            hit = r["would_pass_layer1"] == expected_pass
            r["layer1_hit"] = hit
            hits += hit
        print("\n" + "=" * 70)
        print(f"LAYER 1 POINT-IN-TIME CREDIBILITY: {hits}/{len(scored)} "
              f"({round(100 * hits / len(scored), 1)}%) -- based only on tokens where a real "
              f"deployer wallet + as-of snapshot were both found")
        print("=" * 70)
    else:
        print("\nNo tokens had both a resolved deployer wallet AND a MadeOnSol as-of snapshot -- "
              "no credibility % to report yet. See per-token lines above for why each one failed "
              "(truncated on-chain lookup vs. no snapshot at that date vs. RPC failure).")

    return rows


if __name__ == "__main__":
    main()

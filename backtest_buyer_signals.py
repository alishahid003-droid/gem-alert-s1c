"""
backtest_buyer_signals.py -- GO_LIVE_CHECKLIST 9.9.

Tests whether the Layer 15 buyer-quality verdict predicts profit, on coins whose
buyers were archived by the pump.fun poller (state key "buyer_archive").

  python backtest_buyer_signals.py            # replay (needs internet: GeckoTerminal)
  python backtest_buyer_signals.py --no-net   # only show how much data has accrued

Method: coins are sorted by time and split in half. Only the SECOND half (never
used to pick anything) is judged. Each coin is entered ENTRY_DELAY_MIN after the
archive's first-seen buy and run through the same exit simulator as the other
backtests (backtest_trades.sim_stage, real round-trip cost). Result per verdict
group: n, win rate, average P&L %, and a t-stat.

VERDICT RULES (strict on purpose):
  * fewer than MIN_COINS coins with outcomes  -> "INSUFFICIENT DATA" (no claim made)
  * the 'strong'/'moderate' group must have n >= 15, mean > 0 and t >= 2 on the
    second half AND beat the 'weak/bot_farm' group -> "SIGNAL SHOWS EDGE"
  * otherwise -> "NO EDGE PROVEN"
Funding lookups use the free Solana RPC and are capped, so a run may take a while.
"""
import argparse
import math
import time

import state
from layers import layer15_buyer_quality as q

MIN_COINS = 40
ENTRY_DELAY_MIN = 5


def _tstat(v):
    n = len(v)
    if n < 3:
        return 0.0
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else 0.0


def judge(rows, min_coins=MIN_COINS):
    """rows: list of {'verdict','pnl_pct'} from the UNSEEN half. Pure -> unit-tested."""
    if len(rows) < min_coins:
        return "INSUFFICIENT DATA", f"{len(rows)} coins with outcomes on the unseen half; need {min_coins}+."
    good = [r["pnl_pct"] for r in rows if r["verdict"] in ("strong", "moderate")]
    bad = [r["pnl_pct"] for r in rows if r["verdict"] not in ("strong", "moderate")]
    if len(good) >= 15 and sum(good) / len(good) > 0 and _tstat(good) >= 2 \
            and (not bad or sum(good) / len(good) > sum(bad) / len(bad)):
        return "SIGNAL SHOWS EDGE", f"strong/moderate: n={len(good)} mean {sum(good) / len(good):+.1f}% t={_tstat(good):.1f}"
    return "NO EDGE PROVEN", f"strong/moderate n={len(good)}" + (f" mean {sum(good) / len(good):+.1f}%" if good else "")


def archive_summary():
    arch = state.get_value(q.ARCHIVE_KEY) or {}
    full = {m: r for m, r in arch.items() if len({b['w'] for b in r['buys']}) >= q.MIN_BUYERS_TO_JUDGE}
    return arch, full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-net", action="store_true")
    ap.add_argument("--max-coins", type=int, default=150)
    args = ap.parse_args()

    arch, full = archive_summary()
    print(f"buyer_archive: {len(arch)} coins recorded, {len(full)} with >= {q.MIN_BUYERS_TO_JUDGE} distinct buyers")
    if args.no_net or len(full) < MIN_COINS:
        v, why = judge([], MIN_COINS) if len(full) < MIN_COINS else ("READY", "enough coins; rerun without --no-net")
        print(f"\nVERDICT: {v} -- {why}")
        print("The poller fills this archive while the system runs; come back after a few days.")
        return

    import backtest_alert_replay as bar
    from backtest_trades import sim_stage, POSITION_USD
    from executor.compound_scalper import estimate_round_trip_cost_pct
    roster = q.state.get_value("pumpfun_smart_money_roster") or []
    coins = sorted(full.items(), key=lambda kv: kv[1]["first_ts"])[-args.max_coins:]
    half = len(coins) // 2
    rows = []
    for i, (mint, rec) in enumerate(coins):
        buys = q.archived_buys(mint)
        a = q.assess_demand(buys, roster, q.lookup_funders(sorted({b["wallet"] for b in buys})))
        pool, liq = bar.top_pool("solana", mint)
        if not pool:
            continue
        entry_ts = int(rec["first_ts"]) + ENTRY_DELAY_MIN * 60
        cs = bar.candles_since("solana", pool, entry_ts)
        idx = next((k for k, c in enumerate(cs) if c["unixTime"] >= entry_ts), None)
        if idx is None or len(cs) - idx < 3:
            continue
        cost = estimate_round_trip_cost_pct("solana", POSITION_USD, liq)
        pnl, how = sim_stage(cs, 300, idx, cost)
        rows.append({"verdict": a["verdict"], "pnl_pct": pnl / POSITION_USD * 100, "second": i >= half})
    unseen = [r for r in rows if r["second"]]
    print(f"\nreplayed {len(rows)} coins; judging the unseen half ({len(unseen)} coins)")
    for g in ("strong", "moderate", "weak", "concentrated", "bot_farm"):
        v = [r["pnl_pct"] for r in unseen if r["verdict"] == g]
        if v:
            print(f"  {g:13s} n={len(v):3d} win {sum(1 for x in v if x > 0) / len(v) * 100:3.0f}% avg {sum(v) / len(v):+6.1f}%")
    verdict, why = judge(unseen, MIN_COINS // 2)
    print(f"\nVERDICT: {verdict} -- {why}")


if __name__ == "__main__":
    main()

"""
Fast TRADE backtest -- Ali, Sept 30 2026: "one backtest can't give us our win
rate over the 27-28 coins we have fed in that backtest script, because the
paper script is going to take a lot of time."

backtest_categorized / backtest_point_in_time measure whether the SCORE told
rugs from moonshots. This one answers the money question instead: if the
system had BOUGHT each labeled coin, how would the real exit logic have done?

For every coin in backtest_categorized's labeled lists:
  1. launch time from DexScreener (pairCreatedAt);
  2. real Birdeye 5-minute candles (15-minute fallback) from launch;
  3. BUY at the open of the candle ENTRY_DELAY minutes after launch (the
     system can't see a coin at second zero), with the same round-trip cost
     estimate the paper ledger uses;
  4. walk candle by candle through TWO strategies:
       - "stage": executor.exit_rules (stop-loss, breakeven lock, trailing,
         time stop) + the moonbag ladder -- exactly what real money runs;
       - "scalper": the compound scalper's own thresholds
         (executor.compound_scalper.SCALPER_CONFIG);
     Inside each candle the LOW is applied before the HIGH (pessimistic:
     a stop is assumed to hit before a target in the same candle).
  5. report per-coin P&L and win rate overall, per category and per delay.

HONEST LIMITS, printed with the results:
  - The labeled set is hand-picked (9 moonshots / 9 rugs / 9 pump-dumps /
    1 flat), not a random sample of what the scanner sees.
  - No score gate here: every coin is "bought". The live system filters
    first, so this measures the EXIT logic's quality on a hard mix.
  - Robinhood Chain coins are skipped (Birdeye has no RHC data).
Needs BIRDEYE_API_KEY; spends no MadeOnSol calls.
Usage: python backtest_trades.py [--delays 15,30,60]
"""
import argparse
import time
from collections import defaultdict

from backtest_categorized import SOLANA_LABELED, BSC_LABELED
from executor import exit_rules
from executor.moonbag import DEFAULT_TRIM_LADDER
from executor.compound_scalper import SCALPER_CONFIG, estimate_round_trip_cost_pct
from layers.layer0_scoring import fetch_dexscreener_vol_liq
from layers.layer0d_point_in_time import fetch_birdeye_ohlcv

WINDOW_HOURS = 48
POSITION_USD = 30.0


def candles_for(chain, address, launch_ts):
    end = min(int(time.time()), launch_ts + WINDOW_HOURS * 3600)
    for interval, secs in (("5m", 300), ("15m", 900)):
        r = fetch_birdeye_ohlcv(chain, address, launch_ts, end, interval=interval)
        rows = [c for c in (r.get("candles") or []) if c.get("o") and c.get("h") and c.get("l") and c.get("c")]
        if r.get("ok") and len(rows) >= 4:
            return rows, secs, None
        err = r.get("reason")
    return [], 0, err or "no candles"


def sim_stage(candles, secs, entry_idx, cost):
    """exit_rules + moonbag ladder, same as executor/paper_ledger.manage."""
    entry = candles[entry_idx]["o"]
    t0 = candles[entry_idx].get("unixTime", 0)
    remaining, proceeds, locked, scale, peak = 1.0, 0.0, False, 1.0, entry
    fired = set()
    exit_type = "still_open_at_window_end"

    def sell(pct, price):
        nonlocal remaining, proceeds
        pct = min(pct, remaining)
        proceeds += POSITION_USD * pct * (price / entry) * (1 - cost)
        remaining -= pct

    for c in candles[entry_idx:]:
        now = c.get("unixTime", t0)
        for price in (c["l"], c["h"], c["c"]):          # pessimistic intra-candle order
            peak = max(peak, price)
            d = exit_rules.evaluate_exit(entry, price, peak, t0, now, remaining, locked, cost)
            if d.action == "exit_all":
                if d.exit_type == "stop_loss":
                    price = min(c["o"], entry * (1 - exit_rules.stop_loss_pct()))
                elif d.exit_type == "trailing_stop":
                    price = min(c["o"], peak * (1 - exit_rules.trail_giveback_pct()))
                sell(remaining, price)
                return proceeds - POSITION_USD, d.exit_type
            if d.action == "sell_partial":
                trigger = entry * exit_rules.breakeven_trigger_mult()
                lock_price = c["o"] if c["o"] >= trigger else trigger  # gap-up fills at the open
                pct = exit_rules.breakeven_sell_fraction(lock_price / entry, cost)
                sell(pct, lock_price)
                locked, scale = True, max(0.0, 1.0 - pct)
            for tier, tpct in DEFAULT_TRIM_LADDER:
                if tier not in fired and price >= entry * tier:
                    sell(tpct * scale, entry * tier)
                    fired.add(tier)
                    break
            if remaining <= 1e-9:
                return proceeds - POSITION_USD, "fully_sold"
    sell(remaining, candles[-1]["c"])
    return proceeds - POSITION_USD, exit_type


def sim_scalper(candles, secs, entry_idx, cost):
    """Compound scalper thresholds (take-profit partial, trail, hard stop,
    time stop) from SCALPER_CONFIG."""
    cfg = SCALPER_CONFIG
    entry = candles[entry_idx]["o"]
    t0 = candles[entry_idx].get("unixTime", 0)
    remaining, proceeds, tp_done, peak = 1.0, 0.0, False, entry

    def sell(pct, price):
        nonlocal remaining, proceeds
        pct = min(pct, remaining)
        proceeds += POSITION_USD * pct * (price / entry) * (1 - cost)
        remaining -= pct

    for c in candles[entry_idx:]:
        now = c.get("unixTime", t0)
        for price in (c["l"], c["h"], c["c"]):
            peak = max(peak, price)
            mult = price / entry
            if not tp_done and mult <= 1 - cfg.hard_stop_pct:
                sell(remaining, min(c["o"], entry * (1 - cfg.hard_stop_pct)))
                return proceeds - POSITION_USD, "hard_stop"
            if not tp_done and mult >= cfg.take_profit_multiple:
                sell(cfg.partial_tp_pct, entry * cfg.take_profit_multiple)
                tp_done = True
                continue
            if tp_done and (peak - price) / peak >= cfg.trail_stop_pct:
                sell(remaining, min(c["o"], peak * (1 - cfg.trail_stop_pct)))
                return proceeds - POSITION_USD, "trail_stop"
        if not tp_done and (now - t0) / 60 >= cfg.time_stop_minutes:
            sell(remaining, c["c"])
            return proceeds - POSITION_USD, "time_stop"
    sell(remaining, candles[-1]["c"])
    return proceeds - POSITION_USD, "still_open_at_window_end"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delays", default="15,30,60", help="minutes after launch to enter")
    args = ap.parse_args()
    delays = [int(x) for x in args.delays.split(",")]

    coins = [c for c in SOLANA_LABELED + BSC_LABELED if c[1] != "robinhood_chain"]
    skipped = [c[0] for c in SOLANA_LABELED + BSC_LABELED if c[1] == "robinhood_chain"]
    results = []  # (strategy, delay, name, category, pnl_usd, exit_type)
    cached = []   # (name, category, candles, secs, cost) -- reused by the grid below
    print(f"Trade backtest: {len(coins)} labeled coins (skipped Robinhood Chain: {skipped})\n")
    for name, chain, address, category, _pregrad in coins:
        dex = fetch_dexscreener_vol_liq(chain, address)
        launch_ms = dex.get("launch_ts_ms") if dex.get("ok") else None
        if not launch_ms:
            print(f"  {name:22s} SKIP: no launch time from DexScreener")
            continue
        candles, secs, err = candles_for(chain, address, int(launch_ms / 1000))
        if not candles:
            print(f"  {name:22s} SKIP: {err}")
            continue
        cost = estimate_round_trip_cost_pct(chain, POSITION_USD, (dex.get("liquidity_usd") if isinstance(dex, dict) else None))
        cached.append((name, category, candles, secs, cost))
        line = []
        for delay in delays:
            idx = min(len(candles) - 2, max(0, int(delay * 60 / secs)))
            for strat, fn in (("stage", sim_stage), ("scalper", sim_scalper)):
                pnl, how = fn(candles, secs, idx, cost)
                results.append((strat, delay, name, category, pnl, how))
                line.append(f"{strat[:5]}@{delay}m {pnl / POSITION_USD * 100:+6.0f}% {how}")
        print(f"  {name:22s} [{category:9s}] " + " | ".join(line))

    print("\n" + "=" * 78)
    for strat in ("stage", "scalper"):
        for delay in delays:
            rows = [r for r in results if r[0] == strat and r[1] == delay]
            if not rows:
                continue
            wins = sum(1 for r in rows if r[4] > 0)
            total = sum(r[4] for r in rows)
            by_cat = defaultdict(list)
            for r in rows:
                by_cat[r[3]].append(r)
            cats = ", ".join(f"{k} {sum(1 for r in v if r[4] > 0)}/{len(v)}" for k, v in sorted(by_cat.items()))
            print(f"{strat:7s} entry +{delay:>2}m: WIN RATE {wins}/{len(rows)} = {wins / len(rows) * 100:.0f}%  "
                  f"total P&L ${total:+.2f} on ${POSITION_USD:.0f}/trade  ({cats})")
    print("=" * 78)
    print("Limits: hand-picked labeled set (not a random sample); every coin bought (no score gate);\n"
          "pessimistic intra-candle order (stop before target); RHC coins skipped.")
    run_grid(cached, delays)


def entry_filter_ok(candles, idx) -> bool:
    """Point-in-time filter -- only uses candles BEFORE the entry candle:
    skip a coin already down >= 60% from its launch-window peak (the live
    scorer's launch-window collapse override), or trading below its first
    price (no momentum)."""
    seen = candles[:idx + 1]
    peak = max(c["h"] for c in seen)
    entry = candles[idx]["o"]
    return entry >= peak * 0.4 and entry >= candles[0]["o"]


GRID = [(stop, be, trail) for stop in (0.25, 0.40, 0.55) for be in (1.5, 2.0) for trail in (0.35, 0.5)]


def run_grid(cached, delays):
    """Exit settings x entry filter. 24 coins is small: the best row is a
    direction to paper-test, NOT a proven setting (it will overfit)."""
    import os
    print("\nPARAMETER GRID (stage exits), win rate / total P&L over coins actually entered")
    print(f"{'stop':>5} {'lock':>5} {'trail':>5} {'filter':>7} " + " ".join(f"{'+' + str(d) + 'm':>18}" for d in delays))
    rows_out = []
    for stop, be, trail in GRID:
        os.environ["STOP_LOSS_PCT"], os.environ["BREAKEVEN_TRIGGER_MULT"], os.environ["TRAIL_GIVEBACK_PCT"] = \
            str(stop), str(be), str(trail)
        for use_filter in (False, True):
            cells, agg_w, agg_n = [], 0, 0
            for d in delays:
                w = n = 0
                pnl = 0.0
                for _name, _cat, candles, secs, cost in cached:
                    idx = min(len(candles) - 2, max(0, int(d * 60 / secs)))
                    if use_filter and not entry_filter_ok(candles, idx):
                        continue
                    p, _how = sim_stage(candles, secs, idx, cost)
                    n += 1
                    w += p > 0
                    pnl += p
                agg_w, agg_n = agg_w + w, agg_n + n
                cells.append(f"{w:>2}/{n:<2} {(w / n * 100 if n else 0):>3.0f}% ${pnl:+7.0f}")
            rows_out.append((agg_w / agg_n if agg_n else 0, stop, be, trail, use_filter))
            print(f"{stop:>5.2f} {be:>5.1f} {trail:>5.2f} {('yes' if use_filter else 'no'):>7} " + " ".join(cells))
    for k in ("STOP_LOSS_PCT", "BREAKEVEN_TRIGGER_MULT", "TRAIL_GIVEBACK_PCT"):
        os.environ.pop(k, None)
    best = max(rows_out)
    print(f"\nBest combined win rate: {best[0] * 100:.0f}% (stop {best[1]}, lock {best[2]}x, trail {best[3]}, "
          f"filter {'on' if best[4] else 'off'}) -- small sample, confirm on paper before trusting.")


if __name__ == "__main__":
    main()

"""
31 Oct 2026 (Ali): "15% win rate is pathetic... maybe you're judging it the wrong way."
Correct and real criticism of the earlier peak-multiple-only backtest -- "did the coin
ever touch 2x" is NOT what a real trade captures. A real trade runs the system's actual
exit rules (stop-loss -55%, breakeven lock at 1.5x, trailing stop, time stop) which
routinely turns a coin that dipped and then ran into a real winner, and caps losers at
-55% instead of counting them as 100% losses. This script replays each qualifying
mint's FULL snap sequence (not just peak) through the real executor/exit_rules.py logic
to get an honest win-rate-with-real-exits and expected value per trade, comparable to
what actual trading would have captured.

Uses the same reconstructable-confirmation limitation as the prior backtest (velocity,
famous_name, pressure, repeat_grad only -- no wallet-identity signals, no hot_meta).
"""
import json
import sys

sys.path.insert(0, ".")
from executor.exit_rules import evaluate_exit

SOL_USD = 150.0

with open("/tmp/first_qual_cache.json") as f:
    cache = json.load(f)
first_qual = cache["first_qual"]  # mint -> (mc_usd_at_signal, conf)

# Need full snap sequences per mint now, not just first-qualifying snap. Re-scan logs
# for the mints we care about.
import glob
FILES = sorted(glob.glob("early_log/*.jsonl"))

TARGETS = {
    ("velocity",): "velocity_only",
    ("famous_name", "velocity"): "famous_velocity",
}
target_mints = {m: lbl for m, (mc, c) in first_qual.items()
                 for combo, lbl in TARGETS.items() if tuple(c) == combo}

print(f"Tracking {len(target_mints)} target mints across both groups")

# mint -> list of (ts, mc_usd) snaps, in order
paths = {m: [] for m in target_mints}
entry_mc_usd = {m: first_qual[m][0] for m in target_mints}

for fp in FILES:
    with open(fp) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("t") != "snap":
                continue
            m = d.get("mint")
            if m not in target_mints:
                continue
            mc = d.get("mc")
            ts = d.get("ts")
            if mc is None or ts is None:
                continue
            paths[m].append((ts, mc))

print("snap sequences collected")

def simulate(mint, label):
    entry_usd = entry_mc_usd[mint]
    if not entry_usd or entry_usd <= 0:
        return None
    seq = sorted(paths[mint])
    if not seq:
        return None
    opened_ts = seq[0][0]
    peak_usd = entry_usd
    remaining_pct = 1.0
    breakeven_locked = False
    realized_mult_weighted = 0.0  # sum of (fraction_sold * multiple_at_sale)
    exit_reason = None
    for ts, mc in seq:
        peak_usd = max(peak_usd, mc)
        dec = evaluate_exit(entry_mcap=entry_usd, current_mcap=mc, peak_mcap=peak_usd,
                             opened_ts=opened_ts, now_ts=ts, remaining_pct=remaining_pct,
                             breakeven_locked=breakeven_locked)
        mult = mc / entry_usd
        if dec.action == "sell_partial":
            frac = min(dec.pct_of_original, remaining_pct)
            realized_mult_weighted += frac * mult
            remaining_pct -= frac
            if dec.exit_type == "breakeven_lock":
                breakeven_locked = True
            exit_reason = dec.exit_type
        elif dec.action == "exit_all":
            realized_mult_weighted += remaining_pct * mult
            remaining_pct = 0.0
            exit_reason = dec.exit_type
            break
    # whatever's left at the end of the observed window, mark at last price (position_open proxy)
    if remaining_pct > 1e-6:
        last_mult = seq[-1][1] / entry_usd
        realized_mult_weighted += remaining_pct * last_mult
        if exit_reason is None:
            exit_reason = "still_open_at_window_end"
    return realized_mult_weighted, exit_reason

results = {"velocity_only": [], "famous_velocity": []}
for mint, label in target_mints.items():
    r = simulate(mint, label)
    if r is None:
        continue
    results[label].append(r)

for label, rows in results.items():
    n = len(rows)
    if n == 0:
        print(label, "n=0")
        continue
    mults = [r[0] for r in rows]
    wins = sum(1 for m in mults if m >= 1.0)  # "win" now = ended up net positive overall, not a 2x bar
    avg_mult = sum(mults) / n
    ev_per_dollar = avg_mult - 1.0  # expected P&L per $1 staked
    from collections import Counter
    reasons = Counter(r[1] for r in rows)
    print(f"\n{label}: n={n}")
    print(f"  win% (ended >=1.0x, i.e. not a net loss): {100*wins/n:.1f}%")
    print(f"  avg realized multiple: {avg_mult:.3f}x  (EV per $1 staked: {ev_per_dollar:+.3f})")
    print(f"  exit reasons: {dict(reasons)}")
    mults_sorted = sorted(mults)
    print(f"  median: {mults_sorted[n//2]:.3f}  p25: {mults_sorted[n//4]:.3f}  p75: {mults_sorted[3*n//4]:.3f}")

"""
31 Oct 2026 (Ali): "test the other single combinations." Generalized version of the real
exit-rule EV backtest (solana_ev_backtest3.py) across EVERY reconstructable confirmation
combo found in the 18-day replay (solana_replay_backtest.py's first_qual_cache.json),
not just velocity/famous_velocity. Same methodology: real 5/15/60min checkpoint prices,
real evaluate_exit() logic (stop-loss -55%, breakeven 1.5x, trailing stop, time stop),
entry mcap sanity-filtered to $500-$200k, median + 2%-trimmed mean reported (raw mean is
provably unreliable here, as found in v2 -- a handful of corrupted $1/$700k+ "entry"
rows destroyed it).

Also includes repeat_grad alone (the live system's actual bypass path) for direct
comparison against the earlier 9-coin live-trade audit finding (1/9 win, hot_meta-split
11%/33%) -- now at a (likely still small, but less anecdotal) reconstructed sample size.
"""
import json, glob, sys
sys.path.insert(0, ".")
from executor.exit_rules import evaluate_exit
from collections import Counter, defaultdict

with open("/tmp/first_qual_cache.json") as f:
    cache = json.load(f)
first_qual = cache["first_qual"]  # mint -> (mc_usd_at_signal, conf_list)

# group mints by exact combo
by_combo = defaultdict(dict)  # combo_tuple -> {mint: mc_usd}
for mint, (mc_usd, conf) in first_qual.items():
    by_combo[tuple(conf)][mint] = mc_usd

# only test combos with a workable sample size
TEST_COMBOS = [c for c, mints in by_combo.items() if len(mints) >= 10]
print("Testing combos:", TEST_COMBOS)

all_target_mints = set()
for c in TEST_COMBOS:
    all_target_mints |= set(by_combo[c].keys())
print(f"Total mints across all tested combos: {len(all_target_mints)}")

FILES = sorted(glob.glob("early_log/*.jsonl"))
create_ts, outcomes = {}, {m: [] for m in all_target_mints}
for fp in FILES:
    with open(fp) as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get("t")
            if t == "create" and d.get("mint") in all_target_mints:
                create_ts[d["mint"]] = d["ts"]
            elif t == "outcome":
                m = d.get("mint")
                if m not in all_target_mints: continue
                mcap = d.get("mcap_usd")
                if mcap is None: continue
                outcomes[m].append((d.get("cp_min"), d.get("ts"), mcap, d.get("pair_ts")))

print("outcome data collected")

def is_stale(mint, pair_ts_ms):
    cts = create_ts.get(mint)
    if cts is None or pair_ts_ms is None: return False
    return abs(pair_ts_ms/1000.0 - cts) > 3600

def simulate(mint, entry_usd):
    if not entry_usd or not (500 <= entry_usd <= 200_000):
        return None
    rows = sorted(outcomes[mint], key=lambda r: r[0] or 0)
    clean = [(cp, ts, mc) for cp, ts, mc, pts in rows if not is_stale(mint, pts)]
    if not clean: return None
    opened_ts = create_ts.get(mint, clean[0][1] - (clean[0][0] or 0)*60)
    peak_usd = entry_usd
    remaining_pct = 1.0
    breakeven_locked = False
    realized = 0.0
    exit_reason = None
    for cp, ts, mc in clean:
        peak_usd = max(peak_usd, mc)
        dec = evaluate_exit(entry_mcap=entry_usd, current_mcap=mc, peak_mcap=peak_usd,
                             opened_ts=opened_ts, now_ts=ts, remaining_pct=remaining_pct,
                             breakeven_locked=breakeven_locked)
        mult = mc / entry_usd
        if dec.action == "sell_partial":
            frac = min(dec.pct_of_original, remaining_pct)
            realized += frac * mult
            remaining_pct -= frac
            if dec.exit_type == "breakeven_lock": breakeven_locked = True
            exit_reason = dec.exit_type
        elif dec.action == "exit_all":
            realized += remaining_pct * mult
            remaining_pct = 0.0
            exit_reason = dec.exit_type
            break
    if remaining_pct > 1e-6:
        last_mult = clean[-1][2] / entry_usd
        realized += remaining_pct * last_mult
        if exit_reason is None: exit_reason = f"still_open_at_{clean[-1][0]}min"
    return realized, exit_reason

print()
print("=" * 110)
print(f"{'combo':50s} {'n':>6s} {'win%':>7s} {'median':>8s} {'trim_mean':>10s} {'EV/$1':>8s}")
print("=" * 110)

for combo in sorted(TEST_COMBOS, key=lambda c: -len(by_combo[c])):
    mints = by_combo[combo]
    rows = []
    for mint, entry_usd in mints.items():
        r = simulate(mint, entry_usd)
        if r: rows.append(r)
    n = len(rows)
    if n < 10:
        continue
    mults = sorted(r[0] for r in rows)
    wins = sum(1 for m in mults if m >= 1.0)
    lo, hi = int(n*0.02), int(n*0.98)
    trimmed = mults[lo:hi] if hi > lo else mults
    mean_trimmed = sum(trimmed)/len(trimmed)
    label = "+".join(combo) if combo else "(none)"
    print(f"{label:50s} {n:6d} {100*wins/n:6.1f}% {mults[n//2]:8.3f} {mean_trimmed:10.3f} {mean_trimmed-1:+8.3f}")

print()
print("Exit reason breakdown for top combos:")
for combo in sorted(TEST_COMBOS, key=lambda c: -len(by_combo[c]))[:6]:
    mints = by_combo[combo]
    reasons = Counter()
    for mint, entry_usd in mints.items():
        r = simulate(mint, entry_usd)
        if r: reasons[r[1]] += 1
    label = "+".join(combo) if combo else "(none)"
    print(f"  {label}: {dict(reasons)}")

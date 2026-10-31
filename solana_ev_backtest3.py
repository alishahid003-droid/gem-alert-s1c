"""
31 Oct 2026 (Ali): v3. v2's mean was destroyed by a handful of corrupted entry-mcap rows
($1 and $700k+ "entry" prints that are plainly data glitches, not real trades) --
caught by inspecting the top-15 by multiple and finding those exact rows. Filtering
entry_mcap_usd to a sane $500-$200,000 band (anything real pump.fun early coin should
land here) before computing stats, and reporting MEDIAN as the headline number since a
fat-tailed distribution like this is not well summarized by a mean even after filtering.
"""
import json, glob, sys
sys.path.insert(0, ".")
from executor.exit_rules import evaluate_exit
from collections import Counter

with open("/tmp/first_qual_cache.json") as f:
    cache = json.load(f)
first_qual = cache["first_qual"]

TARGETS = {("velocity",): "velocity_only", ("famous_name","velocity"): "famous_velocity"}
target_mints = {m: lbl for m, (mc, c) in first_qual.items()
                 for combo, lbl in TARGETS.items() if tuple(c) == combo}
entry_mc_usd = {m: first_qual[m][0] for m in target_mints}

FILES = sorted(glob.glob("early_log/*.jsonl"))
create_ts, outcomes = {}, {m: [] for m in target_mints}
for fp in FILES:
    with open(fp) as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get("t")
            if t == "create" and d.get("mint") in target_mints:
                create_ts[d["mint"]] = d["ts"]
            elif t == "outcome":
                m = d.get("mint")
                if m not in target_mints: continue
                mcap = d.get("mcap_usd")
                if mcap is None: continue
                outcomes[m].append((d.get("cp_min"), d.get("ts"), mcap, d.get("pair_ts")))

def is_stale(mint, pair_ts_ms, outcome_ts):
    cts = create_ts.get(mint)
    if cts is None or pair_ts_ms is None: return False
    return abs(pair_ts_ms/1000.0 - cts) > 3600

def simulate(mint):
    entry_usd = entry_mc_usd[mint]
    if not entry_usd or not (500 <= entry_usd <= 200_000):
        return None  # sanity band: real pump.fun early mcap range
    rows = sorted(outcomes[mint], key=lambda r: r[0] or 0)
    clean = [(cp, ts, mc) for cp, ts, mc, pts in rows if not is_stale(mint, pts, ts)]
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

results = {"velocity_only": [], "famous_velocity": []}
n_filtered_bad_entry = 0
for mint, label in target_mints.items():
    entry = entry_mc_usd[mint]
    if not entry or not (500 <= entry <= 200_000):
        n_filtered_bad_entry += 1
        continue
    r = simulate(mint)
    if r is None: continue
    results[label].append(r)

print(f"mints filtered out for bad entry mcap (<$500 or >$200k): {n_filtered_bad_entry}")

for label, rows in results.items():
    n = len(rows)
    if n == 0:
        print(label, "n=0"); continue
    mults = sorted(r[0] for r in rows)
    reasons = Counter(r[1] for r in rows)
    wins = sum(1 for m in mults if m >= 1.0)
    trimmed = mults[int(n*0.02):int(n*0.98)]  # drop top/bottom 2% for a robust mean
    mean_trimmed = sum(trimmed)/len(trimmed)
    print(f"\n{label}: n={n}")
    print(f"  win% (ended >=1.0x): {100*wins/n:.1f}%")
    print(f"  median multiple: {mults[n//2]:.3f}x")
    print(f"  mean multiple (2%-trimmed): {mean_trimmed:.3f}x  (EV per $1: {mean_trimmed-1:+.3f})")
    print(f"  raw (untrimmed) mean: {sum(mults)/n:.3f}x  <- for comparison, shows outlier skew")
    print(f"  p10: {mults[n//10]:.3f}  p25: {mults[n//4]:.3f}  p75: {mults[3*n//4]:.3f}  p90: {mults[9*n//10]:.3f}")
    print(f"  exit reasons: {dict(reasons)}")

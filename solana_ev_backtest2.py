"""
31 Oct 2026 (Ali): v2 of the EV backtest. v1 used "snap" events (trade-feed observations,
median tracked window only 28 SECONDS -- too short to mean anything) instead of the
"outcome" events, which are real 5/15/60-minute post-creation price checkpoints
(early_recorder.CHECKPOINTS_MIN) -- the actual horizon a real hold-and-exit would run
over. This version uses those.

KNOWN DATA QUALITY RISK, checked and handled: some outcome rows carry a "pair_ts" far
older than the coin's own creation time -- almost certainly a stale/wrong DexScreener
pair match (mint reused or ticker collision), not a real price for this coin. Any
outcome row where pair_ts predates creation by more than 1 hour is DROPPED, not used,
to avoid polluting results with garbage prices from an unrelated token.
"""
import json
import glob
import sys

sys.path.insert(0, ".")
from executor.exit_rules import evaluate_exit

with open("/tmp/first_qual_cache.json") as f:
    cache = json.load(f)
first_qual = cache["first_qual"]  # mint -> (mc_usd_at_signal, conf)

TARGETS = {
    ("velocity",): "velocity_only",
    ("famous_name", "velocity"): "famous_velocity",
}
target_mints = {m: lbl for m, (mc, c) in first_qual.items()
                 for combo, lbl in TARGETS.items() if tuple(c) == combo}
entry_mc_usd = {m: first_qual[m][0] for m in target_mints}
print(f"Tracking {len(target_mints)} target mints")

FILES = sorted(glob.glob("early_log/*.jsonl"))

# need create ts for staleness check
create_ts = {}
outcomes = {m: [] for m in target_mints}  # mint -> list of (cp_min, ts, mcap_usd)

dropped_stale = 0
for fp in FILES:
    with open(fp) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("t")
            if t == "create" and d.get("mint") in target_mints:
                create_ts[d["mint"]] = d["ts"]
            elif t == "outcome":
                m = d.get("mint")
                if m not in target_mints:
                    continue
                pair_ts_ms = d.get("pair_ts")
                mcap = d.get("mcap_usd")
                if mcap is None:
                    continue
                outcomes[m].append((d.get("cp_min"), d.get("ts"), mcap, pair_ts_ms))

print("outcome rows collected, filtering stale pair matches...")

def is_stale(mint, pair_ts_ms, outcome_ts):
    cts = create_ts.get(mint)
    if cts is None or pair_ts_ms is None:
        return False  # can't judge, keep it
    pair_ts_s = pair_ts_ms / 1000.0
    # a legit pair should have launched at/near our coin's own creation time
    return abs(pair_ts_s - cts) > 3600  # more than 1h off -> treat as a mismatched pair

def simulate(mint):
    entry_usd = entry_mc_usd[mint]
    if not entry_usd or entry_usd <= 0:
        return None
    rows = sorted(outcomes[mint], key=lambda r: r[0] or 0)  # by cp_min: 5, 15, 60
    clean = [(cp, ts, mc) for cp, ts, mc, pts in rows if not is_stale(mint, pts, ts)]
    if not clean:
        return None
    opened_ts = create_ts.get(mint, clean[0][1] - (clean[0][0] or 0) * 60)
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
            if dec.exit_type == "breakeven_lock":
                breakeven_locked = True
            exit_reason = dec.exit_type
        elif dec.action == "exit_all":
            realized += remaining_pct * mult
            remaining_pct = 0.0
            exit_reason = dec.exit_type
            break
    if remaining_pct > 1e-6:
        last_mult = clean[-1][2] / entry_usd
        realized += remaining_pct * last_mult
        if exit_reason is None:
            exit_reason = f"still_open_at_{clean[-1][0]}min"
    return realized, exit_reason, len(clean)

results = {"velocity_only": [], "famous_velocity": []}
n_dropped_mints_no_clean_data = 0
for mint, label in target_mints.items():
    r = simulate(mint)
    if r is None:
        n_dropped_mints_no_clean_data += 1
        continue
    results[label].append(r)

print(f"mints with no usable (non-stale) outcome data: {n_dropped_mints_no_clean_data}")

from collections import Counter
for label, rows in results.items():
    n = len(rows)
    if n == 0:
        print(label, "n=0")
        continue
    mults = [r[0] for r in rows]
    wins = sum(1 for m in mults if m >= 1.0)
    avg_mult = sum(mults) / n
    reasons = Counter(r[1] for r in rows)
    cp_counts = Counter(r[2] for r in rows)
    mults_sorted = sorted(mults)
    print(f"\n{label}: n={n}")
    print(f"  win% (ended >=1.0x): {100*wins/n:.1f}%")
    print(f"  avg realized multiple: {avg_mult:.3f}x  (EV per $1 staked: {avg_mult-1:+.3f})")
    print(f"  exit reasons: {dict(reasons)}")
    print(f"  # checkpoints reached per coin: {dict(cp_counts)}")
    print(f"  median: {mults_sorted[n//2]:.3f}  p10: {mults_sorted[n//10]:.3f}  p90: {mults_sorted[9*n//10]:.3f}")

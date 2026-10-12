"""Reads early_log/*.jsonl and prints what actually happened to launches.
For every launch: outcome mcap at +5/+15/+60 min vs the launch mcap (SOL price from SOL_USD,
default 150). Splits by dev-list creator, dev initial-buy size, and sniper decisions.
Usage: python analyze_early.py [days]"""
import glob
import json
import os
import sys
from collections import defaultdict

SOL = float(os.environ.get("SOL_USD", "150"))
files = sorted(glob.glob("early_log/*.jsonl"))[-int(sys.argv[1] if len(sys.argv) > 1 else 3):]
create, outc, dec, final, grads = {}, defaultdict(dict), {}, {}, set()
for fp in files:
    for line in open(fp, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t, m = r.get("t"), r.get("mint")
        if t == "create":
            create[m] = r
        elif t == "outcome":
            outc[m][r["cp_min"]] = r
        elif t == "decision":
            dec[m] = r
        elif t == "final":
            final[m] = r
        elif t == "grad":
            grads.add(m)
print(f"files: {len(files)} | launches: {len(create):,} | with outcomes: {len(outc):,} | decisions: {len(dec)}")


def mult(m, cp):
    c, o = create.get(m), outc.get(m, {}).get(cp)
    if not c or not o or not c.get("mcap_sol"):
        return None
    if not o.get("listed") or not o.get("mcap_usd"):
        return 0.0                                  # not listed / no price = dead
    return o["mcap_usd"] / (float(c["mcap_sol"]) * SOL)


def table(name, mints):
    mints = list(mints)
    if not mints:
        return
    print(f"\n== {name}  (n={len(mints):,})")
    print("cp    priced   >=1.5x   >=2x    >=5x    >=10x   median")
    for cp in (5, 15, 60):
        v = [x for x in (mult(m, cp) for m in mints) if x is not None]
        if not v:
            continue
        v.sort()
        pct = lambda k: 100.0 * sum(1 for x in v if x >= k) / len(v)
        print(f"+{cp:>2}m  {len(v):>6}  {pct(1.5):6.1f}%  {pct(2):5.1f}%  {pct(5):5.1f}%  {pct(10):5.1f}%  {v[len(v)//2]:.2f}x")


table("ALL launches", create)
table("dev-list creators", [m for m, c in create.items() if c.get("dev_list")])
table("tracked (passed creator filters)", [m for m, c in create.items() if c.get("tracked")])
table("sniper decisions", dec.keys())
for lane in sorted({d.get("lane") for d in dec.values() if d.get("lane")}):
    table(f"lane {lane}", [m for m, d in dec.items() if d.get("lane") == lane])
by = defaultdict(list)
for m, c in create.items():
    s = float(c.get("init_sol") or 0)
    by["dev bought 0 SOL" if s == 0 else "dev bought <1 SOL" if s < 1 else "dev bought 1-3 SOL" if s < 3 else "dev bought 3+ SOL"].append(m)
for k, v in by.items():
    table(k, v)
if final:
    fast = [m for m, f in final.items() if f.get("fast", 0) >= 5]
    table("tracked coins with >=5 first-seconds buyers (bundled)", fast)


# ---- GRADUATION LIFT: ground truth straight from the stream (graduated = reached ~$69K and migrated) ----
import math


def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


# only launches old enough to have had time to graduate (median ~4 min; allow 60)
import time as _t
mature = [m for m, c in create.items() if _t.time() - float(c.get("ts") or 0) > 3600]
base_k = sum(1 for m in mature if m in grads)
print(f"\n=== GRADUATION LIFT (launches older than 1h: {len(mature):,}; graduated: {base_k:,}; base rate {100.0*base_k/max(1,len(mature)):.2f}%) ===")
print("feature                          n      grads   rate    lift   95% CI")
feats = {
    "dev-list creator": lambda c: bool(c.get("dev_list")),
    "repeat-graduate creator": lambda c: bool(c.get("repeat_grad")),
    "creator graduated >=1 before": lambda c: (c.get("creator_grads") or 0) >= 1,
    "creator launched before (n>=2)": lambda c: (c.get("creator_launches") or 0) >= 2,
    "dev bought 0 SOL": lambda c: float(c.get("init_sol") or 0) == 0,
    "dev bought 0.01-1 SOL": lambda c: 0 < float(c.get("init_sol") or 0) < 1,
    "dev bought 1-3 SOL": lambda c: 1 <= float(c.get("init_sol") or 0) < 3,
    "dev bought 3+ SOL": lambda c: float(c.get("init_sol") or 0) >= 3,
}
base_rate = base_k / max(1, len(mature))
for name, fn in feats.items():
    sel = [m for m in mature if fn(create[m])]
    k = sum(1 for m in sel if m in grads)
    if not sel:
        continue
    lo, hi = wilson(k, len(sel))
    print(f"{name:<30} {len(sel):>7} {k:>7} {100.0*k/len(sel):6.2f}% {('%.1fx' % (k/len(sel)/base_rate)) if base_rate else '-':>6}  [{100*lo:.1f}%-{100*hi:.1f}%]")
fin = [m for m in mature if m in final]
if fin:
    print("\n(tracked coins with a trade feed)")
    for name, fn in {"buyers>=10 seen": lambda f: f.get("buyers", 0) >= 10, "buyers>=4 seen": lambda f: f.get("buyers", 0) >= 4,
                     "bundled (>=5 first-seconds buyers)": lambda f: f.get("fast", 0) >= 5}.items():
        sel = [m for m in fin if fn(final[m])]
        k = sum(1 for m in sel if m in grads)
        if sel:
            lo, hi = wilson(k, len(sel))
            print(f"{name:<34} {len(sel):>6} {k:>6} {100.0*k/len(sel):6.2f}%  [{100*lo:.1f}%-{100*hi:.1f}%]")
print("\nRead this honestly: a feature is only real if its CI does not overlap the base rate AND it holds on a second day.")

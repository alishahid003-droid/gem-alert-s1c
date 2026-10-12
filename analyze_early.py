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
create, outc, dec, final = {}, defaultdict(dict), {}, {}
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

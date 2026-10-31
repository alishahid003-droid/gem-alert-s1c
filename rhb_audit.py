"""
31 Oct 2026 (Ali): "extreme audit... checking every single combination... find a lever."
READ-ONLY -- does not touch the live system. Full combinatorial sweep of robinhood_chain
+ base real trade data (both chains combined per Ali's framing, and split separately too)
across confirmations, tier, mcap band, and raw signal fields (buys5/sells5/vol5/chg5/chg1h).

HONEST SAMPLE SIZE WARNING stated up front: only 36 unique closed trades total across
both chains (combining the current state file and the pre-reset Oct 26 backup). Any
"finding" here with n<15 is closer to an anecdote than a pattern -- flagged per row.
"""
import json
from collections import Counter, defaultdict

all_closed = []
seen_ids = set()
for fname in [".gem_alert_state.json.bak_pre_reset_26oct", ".gem_alert_state.json"]:
    with open(fname) as f:
        state = json.load(f)
    for t in state.get("paper_closed_positions", []):
        tid = t.get("id")
        if t.get("chain") not in ("robinhood_chain", "base"):
            continue
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        all_closed.append(t)

print(f"N = {len(all_closed)} unique robinhood_chain+base closed trades (combined snapshot)\n")

def stats(rows, label):
    n = len(rows)
    if n == 0:
        return
    wins = sum(1 for t in rows if t.get("win"))
    pnl = sum(t.get("pnl_usd", 0) or 0 for t in rows)
    staked = sum(t.get("usd", 0) or 0 for t in rows)
    ev_per_dollar = pnl / staked if staked else 0
    flag = "  [THIN n<15]" if n < 15 else ("  [VERY THIN n<8]" if n < 8 else "")
    print(f"{label:55s} n={n:3d} win={100*wins/n:5.1f}%  net=${pnl:+8.2f}  EV/$1={ev_per_dollar:+.3f}{flag}")

print("=== BY CHAIN ===")
for chain in ("robinhood_chain", "base"):
    stats([t for t in all_closed if t.get("chain") == chain], chain)

print("\n=== BY MODULE ===")
for mod in ("sprint", "compound", "core1", "core2"):
    stats([t for t in all_closed if t.get("module") == mod], mod)

print("\n=== BY TIER ===")
for tier in ("probe", "standard", "conviction"):
    stats([t for t in all_closed if t.get("tags",{}).get("tier") == tier], tier)

print("\n=== BY EXACT CONFIRMATION SET ===")
combo_rows = defaultdict(list)
for t in all_closed:
    conf = tuple(sorted(t.get("tags",{}).get("confirmations") or []))
    combo_rows[conf].append(t)
for combo, rows in sorted(combo_rows.items(), key=lambda kv: -len(kv[1])):
    label = "+".join(combo) if combo else "(none)"
    stats(rows, label)

print("\n=== BY INDIVIDUAL CONFIRMATION PRESENCE (not exact combo) ===")
all_confs = set()
for t in all_closed:
    all_confs |= set(t.get("tags",{}).get("confirmations") or [])
for conf in sorted(all_confs):
    rows = [t for t in all_closed if conf in (t.get("tags",{}).get("confirmations") or [])]
    stats(rows, f"HAS '{conf}'")
    rows_not = [t for t in all_closed if conf not in (t.get("tags",{}).get("confirmations") or [])]
    stats(rows_not, f"WITHOUT '{conf}'")

print("\n=== BY MCAP BAND (entry_mcap) ===")
bands = [(0, 5000), (5000, 20000), (20000, 50000), (50000, 200000), (200000, float("inf"))]
for lo, hi in bands:
    rows = [t for t in all_closed if t.get("entry_mcap") and lo <= t["entry_mcap"] < hi]
    stats(rows, f"mcap ${lo:,}-{hi if hi!=float('inf') else 'inf':}")

print("\n=== RAW SIGNAL FIELDS (where present) ===")
raw_rows = [t for t in all_closed if t.get("tags",{}).get("raw")]
print(f"({len(raw_rows)} of {len(all_closed)} trades have raw data)")

def raw_band_stats(field, bands):
    print(f"\n-- {field} --")
    for lo, hi in bands:
        rows = [t for t in raw_rows if t["tags"]["raw"].get(field) is not None and lo <= t["tags"]["raw"][field] < hi]
        stats(rows, f"{field} {lo}-{hi if hi!=float('inf') else 'inf'}")

raw_band_stats("buys5", [(0,20),(20,50),(50,100),(100,float("inf"))])
raw_band_stats("sells5", [(0,5),(5,20),(20,50),(50,float("inf"))])
raw_band_stats("vol5", [(0,2000),(2000,10000),(10000,50000),(50000,float("inf"))])
raw_band_stats("chg5", [(-100,0),(0,100),(100,300),(300,float("inf"))])
raw_band_stats("chg1h", [(-100,0),(0,100),(100,300),(300,float("inf"))])

print("\n=== buy/sell RATIO ===")
rows_with_ratio = []
for t in raw_rows:
    raw = t["tags"]["raw"]
    b, s = raw.get("buys5"), raw.get("sells5")
    if b is not None and s is not None and s > 0:
        rows_with_ratio.append((t, b/s))
for lo, hi in [(0,2),(2,4),(4,8),(8,float("inf"))]:
    rows = [t for t,r in rows_with_ratio if lo <= r < hi]
    stats(rows, f"buy/sell ratio {lo}-{hi if hi!=float('inf') else 'inf'}")

print("\n=== safety field ===")
safety_vals = Counter(t.get("tags",{}).get("safety") for t in all_closed)
print("values seen:", dict(safety_vals))
for val in safety_vals:
    rows = [t for t in all_closed if t.get("tags",{}).get("safety") == val]
    stats(rows, f"safety={val}")

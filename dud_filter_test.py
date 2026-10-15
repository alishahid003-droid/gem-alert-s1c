"""Read-only: how much of the dud crowd can simple launch-time / first-snapshot rules remove, and what share of winners (>=10x) goes with it?"""
import json, glob, time, collections
SOL = 107.55
cr, oc, snap = {}, collections.defaultdict(dict), {}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f, errors='ignore'):
        h = l[:24]
        try:
            if '"create"' in h:
                r = json.loads(l); cr[r['mint']] = r
            elif '"outcome"' in h:
                r = json.loads(l)
                if r.get('mcap_usd'): oc[r['mint']][r['cp_min']] = r['mcap_usd']
            elif '"snap"' in h:
                r = json.loads(l)
                if 30 <= r['age'] <= 130 and r['mint'] not in snap: snap[r['mint']] = r
        except Exception:
            pass
rows = []
for m, c in cr.items():
    if not m.endswith('pump') or not oc.get(m) or time.time() - c['ts'] < 3900: continue
    pk = max(oc[m].values()) / (c['mcap_sol'] * SOL)
    if pk > 500: continue
    s = snap.get(m)
    rows.append(dict(pk=pk, launches=c.get('creator_launches', 0) or 0, init=c.get('init_sol', 0) or 0, b=(s or {}).get('b'), s_=(s or {}).get('s'), v=(s or {}).get('v'), has=bool(s)))
n = len(rows); W = [r for r in rows if r['pk'] >= 10]
print(f'coins {n}, winners(>=10x) {len(W)} ({100*len(W)/n:.2f}%), with first snapshot {sum(r["has"] for r in rows)}')
def rule(name, drop):
    kept = [r for r in rows if not drop(r)]
    kw = [r for r in kept if r['pk'] >= 10]
    print(f'{name:42s} removes {100*(1-len(kept)/n):4.1f}% of coins, {100*(1-len(kw)/len(W)):4.1f}% of winners | winner rate of kept {100*len(kw)/max(len(kept),1):.2f}%')
rule('drop serial launchers (>=10 launches)', lambda r: r['launches'] >= 10)
rule('drop serial launchers (>=3 launches)', lambda r: r['launches'] >= 3)
rule('drop no first snapshot (never traded)', lambda r: not r['has'])
rule('drop <5 buys at first snapshot', lambda r: r['has'] and r['b'] < 5)
rule('drop <5 buys or no snapshot', lambda r: (not r['has']) or r['b'] < 5)
rule('drop <10 buys or no snapshot', lambda r: (not r['has']) or r['b'] < 10)
rule('drop vol<500 or no snapshot', lambda r: (not r['has']) or r['v'] < 500)
rule('drop sell/buy>0.6 (snapshot)', lambda r: r['has'] and r['b'] and r['s_'] / max(r['b'], 1) > .6)
rule('combo: launches>=3 or <5 buys/no snap', lambda r: r['launches'] >= 3 or (not r['has']) or r['b'] < 5)
rule('combo: launches>=10 or <10 buys/no snap', lambda r: r['launches'] >= 10 or (not r['has']) or r['b'] < 10)

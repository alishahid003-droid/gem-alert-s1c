"""Collective report: every coin we watched (thousands), scored at an entry-like moment, versus what it did later.
Read-only. Run:  python collect_report.py   -> prints and writes COLLECT_REPORT.txt
Entry-like moment = first price snapshot with age 30-130 s and a real market cap. Confirmations are approximated from snapshot
numbers (pressure: buys>=15 and buys>1.5x sells; velocity: volume>=$2500); famous name from the coin name."""
import json, glob, collections, sys, time
sys.path.insert(0, '.')
from layers.fast_lane import score
from backtest_fast import sim, RULES
from layers.layer17_early_entry import _famous_rx

SOL = 107.55
rows = []
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f, errors='ignore'):
        try: rows.append(json.loads(l))
        except Exception: pass
cr = {r['mint']: r for r in rows if r['t'] == 'create'}
oc = collections.defaultdict(dict)
for r in rows:
    if r['t'] == 'outcome': oc[r['mint']][r['cp_min']] = r.get('mcap_usd')
first = {}
for r in rows:
    if r['t'] == 'snap' and 30 <= r['age'] <= 130 and r['mc'] and r['mint'] not in first:
        first[r['mint']] = r
rx = _famous_rx()
data = []
for m, sn in first.items():
    e = sn['mc']
    path = [(oc.get(m, {}).get(k) / e if oc.get(m, {}).get(k) else None) for k in (5, 15, 60)]
    conf = []
    if sn['b'] >= 15 and sn['b'] > 1.5 * sn['s']: conf.append('pressure_free')
    if sn['v'] >= 2500: conf.append('velocity_free')
    c = cr.get(m, {})
    if rx.search((c.get('name') or '') + ' ' + (c.get('sym') or '')): conf.append('famous_name')
    d = {'confirmations': conf, 'fb': sn['b'], 'fs': sn['s'], 'mcap_sol': e / SOL}
    data.append((score(d), path, d, m))
out = []
P = lambda *a: out.append(' '.join(str(x) for x in a))
P(f'COLLECTIVE REPORT  {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}')
P(f'coins watched with an entry-like snapshot: {len(data)}   (with any later price: {sum(any(p is not None for p in x[1]) for x in data)})')
def stat(rows_):
    n = len(rows_)
    pk = [max([v for v in p if v] or [0]) for _, p, _, _ in rows_]
    return n, 100 * sum(x >= 1.4 for x in pk) / n, 100 * sum(x >= 2 for x in pk) / n, 100 * sum(x >= 5 for x in pk) / n, 100 * sum(x == 0 for x in pk) / n
P('\nscore   n    reach1.4x  reach2x  reach5x   dead/unpriced')
by = collections.defaultdict(list)
for x in data: by[x[0]].append(x)
for s in sorted(by):
    if len(by[s]) >= 5:
        n, a, b, c, d = stat(by[s]); P(f'{s:4d} {n:5d}   {a:6.0f}%  {b:6.0f}%  {c:6.0f}%   {d:6.0f}%')
P('\ncohort            n   reach2x  reach5x | simulated result of the NEW fast_sprint exit ladder (x of stake, after costs)')
lad, stop, trail, ta = RULES['NEW3 1.4x/2.5x/6x/15x stop.55 trail.55@1.4x']
for name, f in (('all coins', lambda s: True), ('score>=3', lambda s: s >= 3), ('score>=4', lambda s: s >= 4), ('score>=5', lambda s: s >= 5), ('score>=6', lambda s: s >= 6)):
    c = [x for x in data if f(x[0])]
    if len(c) < 3: P(f'{name:14s} n={len(c)} (too few)'); continue
    n, a, b, cc, d = stat(c)
    r = sorted(sim([v for v in x[1]], lad, stop, trail, ta) if any(x[1]) else 0.0 for x in c)
    P(f'{name:14s} {n:5d}   {b:5.0f}%  {cc:5.0f}%  | mean x{sum(r)/n:4.2f}   ex-best-2 x{sum(r[:-2])/max(n-2,1):4.2f}   win%{100*sum(v>1 for v in r)/n:3.0f}')
P('\nHow to read: "mean x1.00" = break-even. Needs mean above 1.0 AND ex-best-2 above ~1.0 before live. Dead/unpriced coins count as total loss.')
P('Check at least 300 coins in the score>=5 cohort before trusting it.')
txt = '\n'.join(out)
print(txt)
open('COLLECT_REPORT.txt', 'w').write(txt + '\n')

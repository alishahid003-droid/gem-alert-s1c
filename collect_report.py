"""Collective report: every coin we watched (thousands), scored at an entry-like moment, versus what it did later.
Read-only. Run:  python collect_report.py   -> prints and writes COLLECT_REPORT.txt
Coins younger than 65 min are skipped (outcome not in yet). Entry-like moment = first price snapshot with age 30-130 s and a real market cap. Confirmations are approximated from snapshot
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
NOW = time.time()
for m, sn in first.items():
    if NOW - sn['ts'] < 3900:      # younger than ~65 min: its 60-min price check has not happened yet, so do NOT count it as dead
        continue
    e = sn['mc']
    path = [(oc.get(m, {}).get(k) / e if oc.get(m, {}).get(k) else None) for k in (5, 15, 60)]
    conf = []
    if sn['b'] >= 15 and sn['b'] > 1.5 * sn['s']: conf.append('pressure_free')
    if sn['v'] >= 2500: conf.append('velocity_free')
    c = cr.get(m, {})
    if rx.search((c.get('name') or '') + ' ' + (c.get('sym') or '')): conf.append('famous_name')
    if max([v for v in path if v] or [0]) > 500:
        continue
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
# ---- version epochs: one per worker start (parsed from the log) so mixed-version data can be compared -------------
import re as _re, os as _os
epochs = []
try:
    for line in open('logs/local_early-watch.log', errors='ignore'):
        m_ = _re.match(r'=== (\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) early-watch start ===', line)
        if m_:
            y, mo, d_, h_, mi, se = map(int, m_.groups())
            epochs.append(time.mktime((y, mo, d_, h_, mi, se, 0, 0, 0)) - time.timezone - 5 * 3600 if False else
                          __import__('calendar').timegm((y, mo, d_, h_, mi, se)) - 5 * 3600)   # PC clock is PKT (UTC+5)
except OSError:
    pass
for r_ in rows:
    if r_.get('t') == 'run' and r_.get('ts'): epochs.append(r_['ts'])
epochs = sorted(set(round(e) for e in epochs))
def epoch_of(ts):
    k = -1
    for i, e in enumerate(epochs):
        if ts >= e: k = i
    return k
P(f'worker restarts found: {len(epochs)}  (each = a possibly different code/settings version)')
ep = collections.defaultdict(list)
for s_, p_, d_, m_ in data:
    ep[epoch_of(first[m_]['ts'])].append((s_, p_, d_, m_))
P('\nBY VERSION (restart time PKT)   coins  reach2x  mean x(new ladder)  | score>=5: coins  reach2x')
lad0, stop0, trail0, ta0 = RULES['NEW3 1.4x/2.5x/6x/15x stop.55 trail.55@1.4x']
for k in sorted(ep):
    c = ep[k]
    if len(c) < 20: continue
    st = time.strftime('%d %H:%M', time.gmtime(epochs[k] + 5 * 3600)) if k >= 0 else 'before first'
    n_, a_, b_, cc_, d2_ = stat(c)
    r_ = [sim(x[1], lad0, stop0, trail0, ta0) if any(x[1]) else 0.0 for x in c]
    hi = [x for x in c if x[0] >= 5]
    if len(hi) >= 3:
        rh = sorted(sim(x[1], lad0, stop0, trail0, ta0) if any(x[1]) else 0.0 for x in hi)
        hs = f'{len(hi):4d}  {stat(hi)[2]:5.0f}%  mean x{sum(rh)/len(rh):4.2f} ex-best x{sum(rh[:-1])/max(len(rh)-1,1):4.2f}'
    else:
        hs = '   -     -'
    P(f'  {st:20s}            {n_:5d}   {b_:5.0f}%      x{sum(r_)/n_:4.2f}              | {hs}')
P('If "reach2x" and "mean x" swing a lot between versions, the pooled table below is a blend and should not be trusted alone.')
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
top = sorted(data, key=lambda x: -max([v for v in x[1] if v] or [0]))[:3]
P('\nLargest single movers in the sample (they can dominate an average):')
for s_, p_, d_, m_ in top:
    P(f'   {m_[:8]} peak x{max([v for v in p_ if v] or [0]):.0f}  score {s_}  name {(cr.get(m_, {}).get("name") or "")[:16]}')
# trend-matched coins: decisions that carried narrative / narrative_first
tm = collections.defaultdict(list)
for r_ in rows:
    if r_.get('t') == 'decision' and set(r_.get('confirmations') or []) & {'narrative', 'narrative_first'}:
        tm[r_['mint']].append(r_)
P(f'\nTREND-MATCHED coins (decisions with narrative/narrative_first): {len(tm)} coins')
hits = []
for m_, ds_ in tm.items():
    e_ = ds_[0]['mcap_sol'] * SOL
    v_ = [oc.get(m_, {}).get(k) for k in (5, 15, 60)]
    hits.append(max([x / e_ for x in v_ if x] or [0]))
if hits:
    paths_ = []
    for m_, ds_ in tm.items():
        e_ = ds_[0]['mcap_sol'] * SOL
        pp_ = [(oc.get(m_, {}).get(k) / e_ if oc.get(m_, {}).get(k) else None) for k in (5, 15, 60)]
        if max([v for v in pp_ if v] or [0]) <= 500: paths_.append(pp_)
    rr_ = sorted(sim(pp_, lad0, stop0, trail0, ta0) if any(pp_) else 0.0 for pp_ in paths_)
    P(f'   exit-ladder result on these {len(rr_)}: mean x{sum(rr_)/len(rr_):.2f}   without best 2: x{sum(rr_[:-2])/max(len(rr_)-2,1):.2f}   win% {100*sum(v>1 for v in rr_)/len(rr_):.0f}')
    P(f'   reached 1.4x: {sum(h >= 1.4 for h in hits)}   reached 2x: {sum(h >= 2 for h in hits)}   reached 5x: {sum(h >= 5 for h in hits)}   no price/dead: {sum(h == 0 for h in hits)}   of {len(hits)}')
txt = '\n'.join(out)
print(txt)
open('COLLECT_REPORT.txt', 'w').write(txt + '\n')

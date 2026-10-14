"""Exit/entry search with out-of-sample check (read-only). python tune_exits.py
Path per coin = 12-second-ish snapshots after entry (first 5 min) + 5/15/60-min checkpoints. Train = earliest 60% of entries,
test = latest 40% (coins the search never saw). Cost 10% round trip (paper model). Dead/unpriced coins end at 0 value."""
import json, glob, collections, itertools, sys, time
SOL = 107.55
rows = []
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f, errors='ignore'):
        try: rows.append(json.loads(l))
        except Exception: pass
snaps = collections.defaultdict(list); oc = collections.defaultdict(dict); cr = {}
for r in rows:
    t = r.get('t')
    if t == 'snap' and r.get('mc'): snaps[r['mint']].append(r)
    elif t == 'outcome': oc[r['mint']][r['cp_min']] = r.get('mcap_usd')
    elif t == 'create': cr[r['mint']] = r
NOW = time.time()
coins = []
for m, sn in snaps.items():
    sn.sort(key=lambda x: x['ts'])
    e = next((x for x in sn if 30 <= x['age'] <= 130), None)
    if not e or NOW - e['ts'] < 3900: continue
    entry = e['mc']; t0 = e['age']
    path = [(x['age'] - t0, x['mc'] / entry) for x in sn if x['age'] > t0 and x['age'] <= 320]
    for cp in (5, 15, 60):
        v = oc.get(m, {}).get(cp)
        path.append((cp * 60 - t0, (v / entry) if v else 0.0))   # missing checkpoint = dead
    path.sort()
    if max(p[1] for p in path) > 500: continue                  # price glitch
    c = cr.get(m, {})
    coins.append({'m': m, 'ts': e['ts'], 'age': t0, 'b': e['b'], 's': e['s'], 'v': e['v'], 'mc': entry / SOL, 'path': path,
                  'name': (c.get('name') or '') + ' ' + (c.get('sym') or '')})
coins.sort(key=lambda c: c['ts'])
cut = int(len(coins) * 0.6)
train, test = coins[:cut], coins[cut:]

def run(path, lad, stop, trail, ta, hold, cost=0.10):
    rem, got, peak, fired = 1.0, 0.0, 1.0, set()
    last = 1.0
    for t, m in path:
        if t > hold: break
        last = m; peak = max(peak, m)
        for k, fr in lad:
            if k not in fired and m >= k and rem > 1e-9:
                f = min(fr, rem); got += f * m; rem -= f; fired.add(k)
        if rem > 1e-9 and ((peak < ta and m <= stop) or (peak >= ta and m <= peak * trail)):
            got += rem * m; rem = 0; break
    if rem > 1e-9: got += rem * last
    return got * (1 - cost)

LADS = {'A': ((1.4, .3), (2.5, .2), (6, .15), (15, .1)), 'B': ((1.3, .5),), 'C': ((1.6, .5), (3, .25)), 'D': ((2, .5), (5, .3)), 'E': ((1.2, .6),)}
GRID = list(itertools.product(LADS, (.5, .65, .8), (.6, .75, .88), (1.2, 1.5), (300, 900, 3600)))

def evaluate(cs, cfg):
    l, stop, trail, ta, hold = cfg
    r = sorted(run(c['path'], LADS[l], stop, trail, ta, hold) for c in cs)
    return sum(r) / len(r), sum(r[:-2]) / max(len(r) - 2, 1), 100 * sum(v > 1 for v in r) / len(r)

COHORTS = {
    'all coins': lambda c: True,
    'buys>=30 & s/b<.45': lambda c: c['b'] >= 30 and c['s'] / max(c['b'], 1) < .45,
    'mcap 30-45 SOL': lambda c: 30 <= c['mc'] < 45,
    'mcap 30-45 & s/b<.45': lambda c: 30 <= c['mc'] < 45 and c['s'] / max(c['b'], 1) < .45,
}
print(f'coins {len(coins)}  train {len(train)}  test {len(test)}  configs {len(GRID)}')
best_overall = []
for cname, f in COHORTS.items():
    tr = [c for c in train if f(c)]; te = [c for c in test if f(c)]
    if len(tr) < 100 or len(te) < 60:
        print(f'{cname}: too few ({len(tr)}/{len(te)})'); continue
    res = []
    for cfg in GRID:
        a = evaluate(tr, cfg); res.append((a[0], cfg, a))
    res.sort(key=lambda x: -x[0])
    print(f'\n== {cname}  train n={len(tr)} test n={len(te)}')
    for mean_tr, cfg, a in res[:3]:
        b = evaluate(te, cfg)
        print(f'  best-on-train {cfg}: train x{mean_tr:.2f} (ex-best2 x{a[1]:.2f}) -> TEST x{b[0]:.2f} (ex-best2 x{b[1]:.2f}, win {b[2]:.0f}%)')
        best_overall.append((b[1], cname, cfg, b, mean_tr))
best_overall.sort(key=lambda x: -x[0])
print('\nBest TEST ex-best-2 among the train-selected configs:')
for ex2, cname, cfg, b, mt in best_overall[:3]:
    print(f'  {cname} {cfg}: TEST mean x{b[0]:.2f} ex-best2 x{ex2:.2f}  (train x{mt:.2f})')

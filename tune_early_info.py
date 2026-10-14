"""Buy AT CREATION on name-only information (famous/viral name), no waiting for the crowd. Read-only.
Entry = launch price. Path = 5/15/60-min price checks (coarse). Dead/unpriced = 0. Train earliest 60% / test latest 40%."""
import json, glob, collections, sys
sys.path.insert(0, '.')
from layers.layer17_early_entry import _famous_rx
SOL = 107.55
rows = []
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f, errors='ignore'):
        try: rows.append(json.loads(l))
        except Exception: pass
oc = collections.defaultdict(dict); cr = []
for r in rows:
    if r['t'] == 'outcome': oc[r['mint']][r['cp_min']] = r.get('mcap_usd')
    elif r['t'] == 'create' and r['mint'].endswith('pump'): cr.append(r)
cr.sort(key=lambda r: r['ts'])
rx = _famous_rx()
import time
rows_ = []
for c in cr:
    if time.time() - c['ts'] < 3900: continue
    e = c['mcap_sol'] * SOL
    path = [((oc[c['mint']].get(k) / e) if oc[c['mint']].get(k) else 0.0) for k in (5, 15, 60)]
    if max(path) > 500: continue
    rows_.append((c, path, bool(rx.search((c.get('name') or '') + ' ' + (c.get('sym') or '')))))
print('coins', len(rows_), 'famous', sum(1 for r in rows_ if r[2]))

def run(path, lad, stop, trail, ta, cost=0.10):
    rem, got, peak, fired = 1.0, 0.0, 1.0, set(); last = 1.0
    for m in path:
        last = m; peak = max(peak, m)
        for k, fr in lad:
            if k not in fired and m >= k and rem > 1e-9:
                f = min(fr, rem); got += f * m; rem -= f; fired.add(k)
        if rem > 1e-9 and ((peak < ta and m <= stop) or (peak >= ta and m <= peak * trail)):
            got += rem * m; rem = 0; break
    if rem > 1e-9: got += rem * last
    return got * (1 - cost)

LAD = {'ladder2-5-12': ((2, .3), (5, .3), (12, .2)), 'half@2x': ((2, .5),), 'half@3x+25%@8x': ((3, .5), (8, .25)), 'hold-all': ()}
def stats(sel, lad, stop, trail, ta):
    r = sorted(run(p, LAD[lad], stop, trail, ta) for _, p, _ in sel)
    n = len(r)
    pk = [max(p) for _, p, _ in sel]
    return n, sum(r) / n, sum(r[:-2]) / max(n - 2, 1), 100 * sum(x > 1 for x in r) / n, [100 * sum(x >= t for x in pk) / n for t in (2, 5, 10)]
for cname, f in (('FAMOUS/VIRAL name', lambda r: r[2]), ('all other coins', lambda r: not r[2])):
    sel = [r for r in rows_ if f(r)]
    cut = int(len(sel) * .6); tr, te = sel[:cut], sel[cut:]
    n, m, ex, w, pk = stats(sel, 'hold-all', 0, 0, 99)
    print(f'\n== {cname}: n={n}  reach 2x/5x/10x: {pk[0]:.1f}% / {pk[1]:.1f}% / {pk[2]:.1f}%')
    best = None
    for lad in LAD:
        for stop in (.4, .55, .7):
            for trail in (.5, .65, .8):
                for ta in (1.5, 3):
                    a = stats(tr, lad, stop, trail, ta)
                    if best is None or a[1] > best[0]: best = (a[1], lad, stop, trail, ta, a)
    _, lad, stop, trail, ta, a = best
    b = stats(te, lad, stop, trail, ta)
    print(f'  best on train: ladder {lad} stop {stop} trail {trail} after {ta}x  -> train x{a[1]:.2f}  TEST n={b[0]} x{b[1]:.2f} (ex-best2 x{b[2]:.2f}, win {b[3]:.0f}%)')
    a2 = stats(sel, 'half@2x', .55, .65, 1.5)
    print(f'  fixed simple rule (sell half @2x, stop .55, trail .65 after 1.5x): all n={a2[0]} x{a2[1]:.2f} ex-best2 x{a2[2]:.2f} win {a2[3]:.0f}%')

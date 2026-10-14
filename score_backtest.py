"""Backtest of the entry score on logged decisions + outcomes (read-only). python score_backtest.py"""
import json, glob, collections, sys
sys.path.insert(0, '.')
SOL = 107.55

from layers.fast_lane import score

def load():
    rows = []
    for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
        for l in open(f, errors='ignore'):
            try: rows.append(json.loads(l))
            except Exception: pass
    return rows

if __name__ == '__main__':
    rows = load()
    oc = collections.defaultdict(dict)
    for r in rows:
        if r['t'] == 'outcome': oc[r['mint']][r['cp_min']] = r.get('mcap_usd')
    dec = {}
    for r in rows:
        if r['t'] == 'decision' and r['ts'] > 1760417000 and r.get('lane') != 'dev_list' and r.get('fb') is not None:
            dec.setdefault(r['mint'], r)
    data = []
    for m, d in dec.items():
        e = d['mcap_sol'] * SOL
        v = oc.get(m, {})
        pk = max([x / e for x in v.values() if x] or [0])
        data.append((score(d), d, pk, m))
    print('signals', len(data))
    by = collections.defaultdict(list)
    for s, d, pk, m in data: by[s].append(pk)
    print('score  n   hit1.4x  hit2x  hit5x  none/dead')
    for s in sorted(by):
        a = by[s]; n = len(a)
        print(f'{s:4d} {n:4d}  {100*sum(x>=1.4 for x in a)/n:5.0f}%  {100*sum(x>=2 for x in a)/n:5.0f}%  {100*sum(x>=5 for x in a)/n:5.0f}%  {100*sum(x==0 for x in a)/n:4.0f}%')
    for thr in (3, 4, 5, 6, 7):
        a = [pk for s, d, pk, m in data if s >= thr]
        if a: print(f'score>={thr}: n={len(a):3d} of {len(data)}  hit1.4x={100*sum(x>=1.4 for x in a)/len(a):3.0f}%  hit2x={100*sum(x>=2 for x in a)/len(a):3.0f}%  hit5x={100*sum(x>=5 for x in a)/len(a):3.0f}%')
    print('--- the 4 real losers:')
    for s, d, pk, m in data:
        if m[:8] in ('63pqJ5bq', '3juS8vJv', 'GbFyNXRd', 'H9YzUURp'):
            print(m[:8], 'score', s, 'peak x', round(pk, 2))
    top = sorted(data, key=lambda x: -x[2])[:8]
    print('--- 8 biggest movers among signals: score / peak x')
    for s, d, pk, m in top: print(m[:8], d.get('name', '')[:14], 'score', s, 'peak', round(pk, 1), d['confirmations'])

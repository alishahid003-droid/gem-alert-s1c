"""Read-only test: do coins whose NAME shares a word with a recent runner (>=5x, known >=60 min after it launched) win more often?
(the 'veil' / 'wojak' pattern seen among the biggest winners). Uses only information available at the new coin's creation time."""
import json, glob, re, time, collections
SOL = 107.55
cr, oc = {}, collections.defaultdict(dict)
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f, errors='ignore'):
        h = l[:24]
        try:
            if '"create"' in h:
                r = json.loads(l); cr[r['mint']] = r
            elif '"outcome"' in h:
                r = json.loads(l)
                if r.get('mcap_usd'): oc[r['mint']][r['cp_min']] = r['mcap_usd']
        except Exception:
            pass
coins = []
for m, c in cr.items():
    if not m.endswith('pump') or not oc.get(m) or time.time() - c['ts'] < 3900: continue
    e = c['mcap_sol'] * SOL
    pk = max(oc[m].values()) / e
    if pk > 500: continue
    name = (c.get('name', '') + ' ' + c.get('sym', '')).lower()
    words = {w for w in re.findall(r'[a-z0-9]+', name) if len(w) >= 4}
    coins.append((c['ts'], m, name, words, pk))
coins.sort()
print('coins', len(coins))
HOT, WIN, MAXFREQ = 5.0, 6 * 3600, 30
res = {True: [], False: []}
freq = collections.Counter(); hotc = collections.Counter()
lo = hi = 0                                   # window = coins with ts-6h <= t <= ts-1h
for ts, m, name, words, pk in coins:
    while hi < len(coins) and coins[hi][0] <= ts - 3600:
        for w in coins[hi][3]:
            freq[w] += 1
            if coins[hi][4] >= HOT: hotc[w] += 1
        hi += 1
    while lo < hi and coins[lo][0] < ts - WIN:
        for w in coins[lo][3]:
            freq[w] -= 1
            if coins[lo][4] >= HOT: hotc[w] -= 1
        lo += 1
    shared = any(c > 0 and freq[w] <= MAXFREQ and w in name for w, c in hotc.items())
    res[shared].append(pk)
for k, v in res.items():
    n = len(v)
    if not n: continue
    s2 = sorted(v)
    print(f'shares hot word={k!s:5s} n={n:5d}  >=2x {100*sum(x>=2 for x in v)/n:4.1f}%  >=3x {100*sum(x>=3 for x in v)/n:4.1f}%  >=5x {100*sum(x>=5 for x in v)/n:4.1f}%  >=10x {100*sum(x>=10 for x in v)/n:4.2f}%  median peak x{s2[n//2]:.2f}')

"""Backtest of exit ladders / book assignment on the logged early_log (checkpoints 5/15/60 min only).
Usage: python backtest_fast.py   (run in project root). Read-only."""
import json, glob, collections, statistics as st, datetime, sys

SOL = 107.55
COST = 0.10          # paper round-trip cost used by the ledger
STAKE = 5.0


def load():
    rows = []
    for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
        for l in open(f, errors='ignore'):
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    return rows


def sim(path, ladder, stop, trail, trail_after):
    """path = [mult at 5, 15, 60 min] (None skipped). Coarse: price visits only those points.
    Returns multiple of stake returned (net of COST)."""
    rem, got, peak, fired = 1.0, 0.0, 1.0, set()
    for m in path:
        if m is None:
            continue
        peak = max(peak, m)
        for t, fr in sorted(ladder.items()):
            if t not in fired and m >= t and rem > 1e-9:
                f = min(fr, rem); got += f * m; rem -= f; fired.add(t)
        if peak < trail_after and m <= stop:
            got += rem * m; rem = 0; break
        if peak >= trail_after and m <= peak * trail:
            got += rem * m; rem = 0; break
    if rem > 1e-9:
        last = [m for m in path if m is not None]
        got += rem * (last[-1] if last else 1.0)
    return got * (1 - COST)


RULES = {
    'OLD  2x/4x/10x  stop.35 trail.40@2x': ({2: .20, 4: .15, 10: .10}, .35, .40, 2.0),
    'NEW  1.5x/3x/10x stop.50 trail.50@1.5x': ({1.5: .30, 3: .20, 10: .10}, .50, .50, 1.5),
    'NEW2 1.5x/3x/10x stop.60 trail.55@1.5x': ({1.5: .30, 3: .20, 10: .10}, .60, .55, 1.5),
    'NEW3 1.4x/2.5x/6x/15x stop.55 trail.55@1.4x': ({1.4: .30, 2.5: .20, 6: .15, 15: .10}, .55, .55, 1.4),
}


def summarize(name, cohort):
    if not cohort:
        print(name, 'n=0'); return
    print(f'\n== {name}  n={len(cohort)}')
    for rn, (lad, stop, trail, ta) in RULES.items():
        r = sorted(sim(p, lad, stop, trail, ta) for p in cohort)
        mean = sum(r) / len(r)
        ex2 = sum(r[:-2]) / max(len(r) - 2, 1)
        print(f'  {rn:46s} mean x{mean:5.2f}  ex-best2 x{ex2:5.2f}  win%{100*sum(x>1 for x in r)/len(r):4.0f}  pnl@$5 ${ (mean-1)*STAKE*len(r):7.1f}')


def main():
    rows = load()
    t0 = datetime.datetime(2026, 10, 14, 4, 43, tzinfo=datetime.timezone.utc).timestamp()
    cr = {r['mint']: r for r in rows if r['t'] == 'create'}
    oc = collections.defaultdict(dict)
    for r in rows:
        if r['t'] == 'outcome':
            oc[r['mint']][r['cp_min']] = r.get('mcap_usd')
    dec = {}
    for r in rows:
        if r['t'] == 'decision' and r['ts'] > t0 and r['lane'] != 'dev_list':
            dec.setdefault(r['mint'], r)

    def path_for(m, entry_usd):
        v = oc.get(m) or {}
        p = [(v.get(k) / entry_usd if v.get(k) else None) for k in (5, 15, 60)]
        return p if any(x is not None for x in p) else None

    # A. what the new free-data signals did (entries as logged)
    A_all, A_fast, A_late = [], [], []
    for m, d in dec.items():
        e = d['mcap_sol'] * SOL
        p = path_for(m, e)
        if not p:
            continue
        A_all.append(p)
        (A_fast if d['age_s'] <= 120 and d['mcap_sol'] <= 55 else A_late).append(p)
    summarize('A. free-confirmed signals (all)', A_all)
    summarize('A1. ... age<=120s & mcap<=55 SOL  (Sprint rule)', A_fast)
    summarize('A2. ... older/bigger (Core 2 rule)', A_late)

    # B. unfiltered baseline: every pump coin, entered at its create price (mcap<=55 SOL), 2 days
    B, B0 = [], []
    for m, c in cr.items():
        if not m.endswith('pump') or c['mcap_sol'] > 55:
            continue
        p = path_for(m, max(c['mcap_sol'] * SOL, 1))
        if p is None:
            B0.append([0.0, None, None])          # no price at all = dead coin = total loss
        elif max(x for x in p if x) <= 500:       # drop absurd prints (>500x)
            B.append(p)
    summarize('B. NO FILTER, priced coins only (biased: dead coins dropped)', B)
    summarize('B2. NO FILTER, dead coins counted as total loss', B + B0)
    print(f'   (dead/unpriced share: {100*len(B0)/max(len(B)+len(B0),1):.0f}%)')

    # C. reality: how many signals the old books really opened
    print('\n== C. old books reality (today since 09:43): 263 signals -> 4 opened (1.5%).')


if __name__ == '__main__':
    main()

import sys; sys.path.insert(0,'.')
exec(open('tune_early_info.py').read().split("def run(")[0])
def run(path, lad, stop, trail, ta, cost):
    rem, got, peak, fired = 1.0, 0.0, 1.0, set(); last = 1.0
    for m in path:
        last = m; peak = max(peak, m)
        for k, fr in lad:
            if k not in fired and m >= k and rem > 1e-9:
                f = min(fr, rem); got += f * k; rem -= f; fired.add(k)   # fill at TARGET k, not checkpoint
        if rem > 1e-9 and ((peak < ta and m <= stop) or (peak >= ta and m <= peak * trail)):
            got += rem * m; rem = 0; break
    if rem > 1e-9: got += rem * last
    return got * (1 - cost)
LAD=((2,.3),(5,.3),(12,.2))
fam=[r for r in rows_ if r[2]]
n=len(fam)
def rep(sel,label,cost):
    r=sorted(run(p,LAD,.7,.8,1.5,cost) for _,p,_ in sel)
    k=len(r); print(f'{label} cost{int(cost*100)}% n={k} mean x{sum(r)/k:.2f} ex-best2 x{sum(r[:-2])/(k-2):.2f} ex-best5 x{sum(r[:-5])/(k-5):.2f} win {100*sum(x>1 for x in r)/k:.0f}%')
for c in (.10,.25,.35): rep(fam,'famous all',c)
q=n//4
for i in range(4): rep(fam[i*q:(i+1)*q] if i<3 else fam[3*q:],f'famous quarter{i+1}',.25)

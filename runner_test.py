"""Entry when early buys/volume show up (snapshot 30-130 s), then RUNNER exits (small early sell, wide hold) vs the old fixed ladder.
Path = later snapshots (to ~300 s) + 5/15/60-min price checks. Fill at target price, cost 15%, dead=0. Train earliest 60% / test latest 40%."""
import json,glob,collections,time
cr={};paths=collections.defaultdict(list);entry={}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        h=l[:24]
        try:
            if '"create"' in h:
                r=json.loads(l); cr[r['mint']]=r
            elif '"snap"' in h:
                r=json.loads(l); paths[r['mint']].append((r['ts'],r['mc'])); 
                if 30<=r['age']<=130 and r['mint'] not in entry: entry[r['mint']]=r
            elif '"outcome"' in h:
                r=json.loads(l)
                if r.get('mcap_usd'): paths[r['mint']].append((r['ts'],r['mcap_usd']))
        except Exception: pass
def build(trig):
    out=[]
    for m,s in entry.items():
        c=cr.get(m)
        if not c or time.time()-c['ts']<3900 or s['mc']<=0 or not trig(s): continue
        e=s['mc']; ps=sorted(p for p in paths[m] if p[0]>s['ts'])
        series=[x/e for _,x in ps]
        # missing 5/15/60 outcomes after a dead coin => priced as 0 at the end
        if not any(c2['cp_min']==60 for c2 in []): pass
        out.append((c['ts'],series or [0.0]))
    out.sort(key=lambda r:r[0]); return out
def sim(series,lad,stop,trail,ta,cost=.15):
    rem,got,peak,fired=1.0,0.0,1.0,set();last=series[-1] if series else 0
    for m in series:
        if m>500: continue
        peak=max(peak,m)
        for k,fr in lad:
            if k not in fired and m>=k and rem>1e-9:
                f=min(fr,rem);got+=f*k;rem-=f;fired.add(k)
        if rem>1e-9 and ((peak<ta and m<=stop) or (peak>=ta and m<=peak*trail)):
            got+=rem*m;rem=0;break
    if rem>1e-9: got+=rem*min(last,500)
    return got*(1-cost)
CFG={'OLD ladder (1.4x/2.5x/6x/15x)':(((1.4,.3),(2.5,.2),(6,.15),(15,.1)),.55,.55,1.4),
     'runner A: 25%@3x, trail .5 after 2x, stop .6':(((3,.25),),.6,.5,2.0),
     'runner B: 20%@4x, trail .6 after 3x, no stop (.01)':(((4,.2),),.01,.6,3.0),
     'runner C: 30%@2x, trail .55 after 2x, stop .5':(((2,.3),),.5,.55,2.0),
     'runner D: 15%@5x, trail .5 after 3x, stop .6':(((5,.15),),.6,.5,3.0)}
TR={'buys>=40':lambda s:s['b']>=40,'vol>=4000':lambda s:s['v']>=4000,'buys>=40 & vol>=3000':lambda s:s['b']>=40 and s['v']>=3000}
for tn,t in TR.items():
    rows=build(t); n=len(rows); cut=int(n*.6)
    print(f'\n### entry: {tn}   coins {n}')
    for cn,(lad,stop,trail,ta) in CFG.items():
        def stat(R):
            r=sorted(sim(s,lad,stop,trail,ta) for _,s in R); k=len(r)
            return k,sum(r)/k,sum(r[:-2])/max(k-2,1),100*sum(x>1 for x in r)/k
        a,b,c=stat(rows),stat(rows[:cut]),stat(rows[cut:])
        print(f'  {cn:52s} ALL x{a[1]:.2f} ex2 x{a[2]:.2f} | train x{b[1]:.2f} | TEST n={c[0]} x{c[1]:.2f} ex2 x{c[2]:.2f} win {c[3]:.0f}%')

print('\n== top contributors, entry buys>=40 & vol>=3000, runner A ==')
rows=[]
for m,s in entry.items():
    c=cr.get(m)
    if not c or time.time()-c['ts']<3900 or s['mc']<=0 or not(s['b']>=40 and s['v']>=3000): continue
    ps=sorted(p for p in paths[m] if p[0]>s['ts']); ser=[x/s['mc'] for _,x in ps] or [0.0]
    rows.append((sim(ser,CFG['runner A: 25%@3x, trail .5 after 2x, stop .6'][0],.6,.5,2.0),max(ser),c.get('name','')[:24],round(s['mc'])))
rows.sort(reverse=True)
for r in rows[:5]: print(f'  sim x{r[0]:.1f}  peak x{r[1]:.0f}  {r[2]}  entry mc ${r[3]}')
print('  coins',len(rows),' total sim x%.2f'%(sum(r[0] for r in rows)/len(rows)),' share of profit from top 2: %.0f%%'%(100*(rows[0][0]+rows[1][0]-2*.0)/sum(r[0] for r in rows)))

"""Do creators who launched a winner launch more winners? Uses our own logs (no screenshots). Read-only."""
import json,glob,collections,time,sys
rows=[]
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        try: rows.append(json.loads(l))
        except Exception: pass
SOL=107.55
oc=collections.defaultdict(dict); grad=set(); cr=[]
for r in rows:
    if r['t']=='outcome': oc[r['mint']][r['cp_min']]=r.get('mcap_usd')
    elif r['t']=='grad': grad.add(r['mint'])
    elif r['t']=='create' and r['mint'].endswith('pump') and time.time()-r['ts']>3900: cr.append(r)
cr.sort(key=lambda r:r['ts'])
def peak(c):
    e=c['mcap_sol']*SOL; v=[x for x in oc[c['mint']].values() if x]
    m=(max(v)/e) if v else 0
    return 0 if m>500 else m
for c in cr: c['pk']=peak(c); c['win']=c['pk']>=3 or c['mint'] in grad
print('coins',len(cr),'win rate %.1f%%'%(100*sum(c['win'] for c in cr)/len(cr)))
# 1) screenshot devs
shot={w['address'] for w in json.load(open('pumpfun_top_devs_oct2026.json'))['wallets']}
s=[c for c in cr if c['creator'] in shot]
print('screenshot devs: launches seen',len(s),'wins',sum(c['win'] for c in s),'distinct devs seen',len({c['creator'] for c in s}))
# 2) data-driven: creator's earlier wins predict later coin?
by=collections.defaultdict(list)
for c in cr: by[c['creator']].append(c)
hit=[];miss=[]
for k,v in by.items():
    prior=0
    for i,c in enumerate(v):
        if i>0: (hit if prior>=1 else miss).append(c) if True else None
        prior+= c['win']
# split: creators with >=1 earlier win
def st(L):
    return len(L),(100*sum(c['win'] for c in L)/len(L) if L else 0),(100*sum(c['pk']>=5 for c in L)/len(L) if L else 0)
a=[];b=[]
for k,v in by.items():
    pw=0;pn=0
    for c in v:
        if pn>0: (a if pw>=1 else b).append(c)
        pw+=c['win'];pn+=1
print('repeat creators: coin AFTER creator had >=1 earlier win: n=%d win%%=%.1f 5x%%=%.1f'%st(a))
print('repeat creators: coin after only non-wins:            n=%d win%%=%.1f 5x%%=%.1f'%st(b))
print('first-time creators: n=%d win%%=%.1f 5x%%=%.1f'%st([v[0] for v in by.values()]))
top=sorted(by.items(),key=lambda kv:-sum(c['win'] for c in kv[1]))[:8]
for k,v in top: print(k[:10],'launches',len(v),'wins',sum(c['win'] for c in v),'best %.0fx'%max(c['pk'] for c in v))

print('\n== data-driven good-dev list: pick on first 60% of time, test on last 40% ==')
def run(path,cost=.25,lad=((2,.3),(5,.3),(12,.2)),stop=.7,trail=.8,ta=1.5):
    rem,got,peak,fired=1.0,0.0,1.0,set();last=1.0
    for m in path:
        last=m;peak=max(peak,m)
        for k,fr in lad:
            if k not in fired and m>=k and rem>1e-9:
                f=min(fr,rem);got+=f*k;rem-=f;fired.add(k)
        if rem>1e-9 and ((peak<ta and m<=stop) or (peak>=ta and m<=peak*trail)):
            got+=rem*m;rem=0;break
    if rem>1e-9:got+=rem*last
    return got*(1-cost)
def pth(c):
    e=c['mcap_sol']*SOL
    return [((oc[c['mint']].get(k)/e) if oc[c['mint']].get(k) else 0.0) for k in (5,15,60)]
cut=cr[int(len(cr)*.6)]['ts']
tr=[c for c in cr if c['ts']<cut]; te=[c for c in cr if c['ts']>=cut]
g=collections.defaultdict(list)
for c in tr: g[c['creator']].append(c)
for minl,minw in ((3,.3),(5,.3),(5,.5),(8,.2)):
    good={k for k,v in g.items() if len(v)>=minl and sum(c['win'] for c in v)/len(v)>=minw}
    t=[c for c in te if c['creator'] in good]
    if len(t)<5: print(minl,minw,'devs',len(good),'test coins',len(t)); continue
    r=sorted(run(pth(c)) for c in t)
    print(f'launches>={minl} win>={int(minw*100)}%: devs {len(good)} TEST coins {len(t)} win {100*sum(c["win"] for c in t)/len(t):.1f}% ladder x{sum(r)/len(r):.2f} ex-best2 x{sum(r[:-2])/max(len(r)-2,1):.2f}')

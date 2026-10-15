"""What did >=10x coins look like EARLY (first snapshot 30-130 s) compared with everything else? Read-only."""
import json,glob,collections,time,statistics as st
SOL=107.55
cr={};oc=collections.defaultdict(dict);snap={};dec={}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        h=l[:24]
        try:
            if '"create"' in h:
                r=json.loads(l); cr[r['mint']]=r
            elif '"outcome"' in h:
                r=json.loads(l)
                if r.get('mcap_usd'): oc[r['mint']][r['cp_min']]=r['mcap_usd']
            elif '"snap"' in h:
                r=json.loads(l)
                if 30<=r['age']<=130 and r['mint'] not in snap: snap[r['mint']]=r
            elif '"decision"' in h:
                r=json.loads(l); dec.setdefault(r['mint'],r)
        except Exception: pass
rows=[]
for m,c in cr.items():
    if time.time()-c['ts']<3900 or not m.endswith('pump') or m not in snap or not oc.get(m): continue
    e=c['mcap_sol']*SOL; pk=max(oc[m].values())/e
    if pk>500: continue
    s=snap[m]
    rows.append(dict(m=m,name=c.get('name',''),pk=pk,b=s['b'],s=s['s'],v=s['v'],mc=s['mc'],age=s['age'],init=c.get('init_sol',0),launches=c.get('creator_launches',0),mcs=c['mcap_sol'],conf=(dec.get(m) or {}).get('confirmations') or []))
W=[r for r in rows if r['pk']>=10]; B=rows
print('coins',len(rows),' >=10x:',len(W),'(%.2f%%)'%(100*len(W)/len(rows)),' >=20x:',sum(r['pk']>=20 for r in rows))
def q(L,k):
    v=sorted(r[k] for r in L); n=len(v)
    return '%s / %s / %s'%(round(v[n//4],1),round(v[n//2],1),round(v[3*n//4],1))
for k in ('b','s','v','mc','mcs','init','launches'):
    print(f'{k:9s} winners p25/med/p75: {q(W,k):28s} all coins: {q(B,k)}')
print('\nlift by early buys (first snapshot):')
for lo,hi in ((0,1),(1,5),(5,15),(15,40),(40,999)):
    g=[r for r in rows if lo<=r['b']<hi]
    if g: print(f'  buys {lo:3d}-{hi:3d}: n={len(g):5d}  >=10x {100*sum(r["pk"]>=10 for r in g)/len(g):.2f}%  >=3x {100*sum(r["pk"]>=3 for r in g)/len(g):.1f}%')
print('\nlift by early volume $:')
for lo,hi in ((0,1),(1,500),(500,2000),(2000,6000),(6000,1e9)):
    g=[r for r in rows if lo<=r['v']<hi]
    if g: print(f'  vol {lo:5.0f}-{hi:9.0f}: n={len(g):5d}  >=10x {100*sum(r["pk"]>=10 for r in g)/len(g):.2f}%  >=3x {100*sum(r["pk"]>=3 for r in g)/len(g):.1f}%')
print('\nthe winners:')
for r in sorted(W,key=lambda r:-r['pk'])[:14]: print(f'  {r["pk"]:5.0f}x {r["name"][:22]:22s} buys {r["b"]:3d} sells {r["s"]:3d} vol ${r["v"]:6d} mc ${r["mc"]:6d} launches {r["launches"]} signals {",".join(r["conf"]) or "-"}')

print('\n== BUYING AT THE SNAPSHOT (after the early volume is visible): forward peak vs snapshot price ==')
def fwd(r): 
    p=max(oc[r['m']].values()) if oc.get(r['m']) else 0
    return p/r['mc'] if r['mc']>0 else 0
for name,f in (('all coins',lambda r:True),('buys>=40',lambda r:r['b']>=40),('vol>=4000',lambda r:r['v']>=4000),('buys>=40 & s/b<.4',lambda r:r['b']>=40 and r['s']/max(r['b'],1)<.4),('buys>=40 & mc<=15000',lambda r:r['b']>=40 and r['mc']<=15000),('vol>=4000 & mc<=10000',lambda r:r['v']>=4000 and r['mc']<=10000)):
    g=[r for r in rows if f(r) and r['mc']>0]
    if len(g)<10: print(name,'n',len(g)); continue
    F=sorted(fwd(r) for r in g)
    print(f'  {name:24s} n={len(g):5d} reach 1.5x {100*sum(x>=1.5 for x in F)/len(F):4.1f}%  2x {100*sum(x>=2 for x in F)/len(F):4.1f}%  5x {100*sum(x>=5 for x in F)/len(F):4.1f}%  median fwd x{F[len(F)//2]:.2f}')

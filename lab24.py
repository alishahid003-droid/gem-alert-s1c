import json,glob,collections,statistics as st,datetime,re
S=collections.defaultdict(list);C={};F={}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        try:r=json.loads(l)
        except: continue
        t=r['t']
        if t=='snap': S[r['mint']].append(r)
        elif t=='create': C[r['mint']]=r
        elif t=='final' and r.get('buys'): F[r['mint']]=r
namecount=collections.Counter((c.get('sym') or '').lower() for c in C.values())
D=[]
for m,L in S.items():
    L.sort(key=lambda r:r['age']); c=C.get(m)
    if not c: continue
    ok=[r for r in L if r['mc'] and 0<r['mc']<30e6]
    ent=[r for r in ok if 45<=r['age']<=150]
    if not ent: continue
    e=ent[0]; path=[(r['age'],r['mc']/e['mc']) for r in ok if r['age']>e['age']]
    if not path or max(p for _,p in path)>200: continue
    D.append(dict(m=m,c=c,e=e,path=path,ts=c['ts']))
D.sort(key=lambda d:d['ts']);N=len(D);print('coins',N)
COST=.15
def sim(path,tp,sl):
    """buy at 1.0; sell at tp x or stop at sl x (checked in snap order); else last value. returns multiple after cost"""
    for _,x in path:
        if x>=tp: return tp*(1-COST)
        if x<=sl: return sl*(1-COST)
    return path[-1][1]*(1-COST)
def ev(items,tp,sl):
    r=[sim(d['path'],tp,sl) for d in items]; s=sorted(r,reverse=True)
    return sum(r)/len(r),sum(s[2:])/max(1,len(s)-2)
feat={
 'all':lambda d:True,
 'creator_grad>=1':lambda d:d['c'].get('creator_grads',0)>=1,
 'launch3-15':lambda d:3<=d['c'].get('creator_launches',1)<=15,
 'launch3-15 & b>=6':lambda d:3<=d['c'].get('creator_launches',1)<=15 and d['e']['b']>=6,
 'b>=6&ratio>=2':lambda d:d['e']['b']>=6 and d['e']['b']>=2*max(1,d['e']['s']),
 'clone_name(>=3 same sym)':lambda d:namecount[(d['c'].get('sym') or '').lower()]>=3,
 'unique_name':lambda d:namecount[(d['c'].get('sym') or '').lower()]==1,
 'pump_mint':lambda d:d['m'].endswith('pump'),
 'init_sol<1':lambda d:d['c'].get('init_sol',0)<1,
 'mc<1500 at 1min':lambda d:d['e']['mc']<1500,
 'mc>5000 at 1min':lambda d:d['e']['mc']>5000,
}
print('rule                        n    hit3x  | EV(TP/SL)  all  ex-best2 | first half  second half')
h=N//2
for nm,f in feat.items():
    s=[d for d in D if f(d)]
    if len(s)<30: print(nm,'n',len(s)); continue
    best=None
    for tp in (1.5,2,3,5):
        for sl in (0.3,0.5,0.7):
            a,b=ev(s,tp,sl)
            if best is None or b>best[0]: best=(b,a,tp,sl)
    b,a,tp,sl=best
    tr=[d for d in s if d['ts']<=D[h]['ts']];te=[d for d in s if d['ts']>D[h]['ts']]
    t1=ev(tr,tp,sl)[1] if len(tr)>10 else float('nan');t2=ev(te,tp,sl)[1] if len(te)>10 else float('nan')
    hit=100*sum(max(p for _,p in d['path'])>=3 for d in s)/len(s)
    print(f'{nm:26s}{len(s):6d} {hit:5.1f}% | TP{tp} SL{sl}  {a:.2f} {b:.2f}      | {t1:.2f}   {t2:.2f}')
# hour of day (UTC) of creation
hr=collections.defaultdict(list)
for d in D: hr[datetime.datetime.utcfromtimestamp(d['ts']).hour].append(max(p for _,p in d['path'])>=3)
print('3x rate by UTC hour:',{h:f'{100*sum(v)/len(v):.1f}' for h,v in sorted(hr.items()) if len(v)>200})
# wallets: early buyers of coins that hit 3x
ws=collections.defaultdict(lambda:[0,0])
for m,f in F.items():
    d=next((x for x in D if x['m']==m),None)
    if not d: continue
    win=max(p for _,p in d['path'])>=3
    for w in f.get('early_buyers',[]): ws[w][0]+=1;ws[w][1]+=win
rep=[(w,a,b) for w,(a,b) in ws.items() if a>=3]
print('wallets seen early in >=3 tracked coins:',len(rep),'; with >=1 3x:',[(w[:6],a,b) for w,a,b in rep if b>=1][:8])

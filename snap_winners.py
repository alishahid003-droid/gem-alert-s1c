import json,glob,collections,statistics as st
S=collections.defaultdict(list);C={}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        try:r=json.loads(l)
        except: continue
        if r['t']=='snap': S[r['mint']].append(r)
        elif r['t']=='create': C[r['mint']]=r
print('coins with snaps',len(S),'creates',len(C))
D=[]
for m,L in S.items():
    L.sort(key=lambda r:r['age'])
    ok=[r for r in L if r['mc'] and r['mc']<30e6]
    early=[r for r in ok if 45<=r['age']<=150]
    if not early or not C.get(m): continue
    e=early[0]
    if e['mc']<=0: continue
    later=[r['mc'] for r in ok if r['age']>e['age']]
    pk=max(later)/e['mc'] if later else 1
    if pk>200: continue
    D.append(dict(m=m,pk=pk,e=e,c=C[m],n=len(L)))
print('usable coins',len(D))
for t in (2,3,5,10): print(f'  reached {t}x after minute-1 snap: {sum(d["pk"]>=t for d in D)} ({100*sum(d["pk"]>=t for d in D)/len(D):.1f}%)')
W=[d for d in D if d['pk']>=3];Lo=[d for d in D if d['pk']<1.5]
print('winners(>=3x)',len(W),'losers(<1.5x)',len(Lo))
for k in ('b','s','v','mc','age'):
    print(f'{k:4s} W med {st.median([d["e"][k] for d in W]):10.1f}  L med {st.median([d["e"][k] for d in Lo]):10.1f}')
print('creator_launches W',st.median([d['c'].get('creator_launches',1) for d in W]),'L',st.median([d['c'].get('creator_launches',1) for d in Lo]))
print('mint ends pump: W %.0f%% L %.0f%%'%(100*sum(d['m'].endswith('pump') for d in W)/len(W),100*sum(d['m'].endswith('pump') for d in Lo)/len(Lo)))
# simple rules: P(>=3x) given minute-1 buyers/sells/volume
def rate(f,label):
    s=[d for d in D if f(d)]
    if len(s)>=10: print(f'{label:34s} n={len(s):4d} hit2x={100*sum(d["pk"]>=2 for d in s)/len(s):4.1f}% hit3x={100*sum(d["pk"]>=3 for d in s)/len(s):4.1f}% hit5x={100*sum(d["pk"]>=5 for d in s)/len(s):4.1f}%')
rate(lambda d:True,'ALL')
for b in (3,6,10,20): rate(lambda d,b=b:d['e']['b']>=b,f'buys>={b} at ~1min')
for v in (500,1500,4000): rate(lambda d,v=v:d['e']['v']>=v,f'vol>=${v} at ~1min')
rate(lambda d:d['e']['b']>=6 and d['e']['b']>=2*max(1,d['e']['s']),'buys>=6 & buy/sell>=2')
rate(lambda d:d['e']['b']>=10 and d['e']['b']>=2*max(1,d['e']['s']) and d['e']['v']>=1500,'buys>=10,ratio>=2,vol>=1500')
rate(lambda d:d['e']['mc']<8000,'mcap<8k at 1min')
print('top10:',[(round(d['pk']),d['e']['b'],d['e']['s'],d['e']['v'],d['e']['mc'],d['c'].get('sym')) for d in sorted(D,key=lambda d:-d['pk'])[:10]])
print('--- creator history (launches/grads known at creation)')
for lo,hi in ((1,1),(2,2),(3,5),(6,15),(16,999)): rate(lambda d,lo=lo,hi=hi:lo<=d['c'].get('creator_launches',1)<=hi,f'creator launches {lo}-{hi}')
rate(lambda d:d['c'].get('creator_grads',0)>=1,'creator has >=1 graduate')
rate(lambda d:d['c'].get('repeat_grad'),'repeat_grad')
rate(lambda d:d['c'].get('creator_launches',1)>=3 and d['e']['b']>=6,'launches>=3 & buys>=6')
rate(lambda d:d['c'].get('creator_launches',1)>=3 and d['e']['b']>=6 and d['e']['b']>=2*max(1,d['e']['s']),'launches>=3,buys>=6,ratio>=2')
rate(lambda d:d['m'].endswith('pump') and d['c'].get('init_sol',0)>=1,'pump mint, dev buy>=1 SOL')
rate(lambda d:d['c'].get('init_sol',0)>=3,'dev initial buy>=3 SOL')
import collections as cc
cr=cc.defaultdict(list)
for d in D: cr[d['c']['creator']].append(d['pk'])
big=[(c,len(v),sum(x>=3 for x in v)) for c,v in cr.items() if sum(x>=3 for x in v)>=2]
print('creators with >=2 coins that hit 3x:',len(big),sorted(big,key=lambda x:-x[2])[:6])

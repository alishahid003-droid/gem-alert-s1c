import json,glob,collections
SOL=107.55;S=collections.defaultdict(list);C={}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        try:r=json.loads(l)
        except: continue
        if r['t']=='snap': S[r['mint']].append(r)
        elif r['t']=='create' and r['mint'].endswith('pump'): C[r['mint']]=r
rows=[]
for m,c in C.items():
    L=sorted([r for r in S.get(m,[]) if r['mc'] and 0<r['mc']<30e6],key=lambda r:r['age'])
    if len(L)<3: continue
    e0=c['mcap_sol']*SOL
    if not 2000<e0<8000: continue
    e1=next((r['mc'] for r in L if r['age']>=45),None)
    if not e1: continue
    rows.append((e0,e1,[r['mc'] for r in L],c))
print('pump coins',len(rows))
def run(label,sel,entry,tp,sl,cost):
    R=[]
    for e0,e1,path,c in rows:
        if not sel(c):continue
        e=e0 if entry=='create' else e1
        out=None
        for x in path:
            m=x/e
            if m>=tp: out=tp;break
            if m<=sl: out=sl;break
        if out is None: out=path[-1]/e
        R.append(out*(1-cost))
    if len(R)<30: print(label,'n',len(R));return
    s=sorted(R,reverse=True);print(f'{label:34s} n={len(R):5d} EV={sum(R)/len(R):.2f} exbest2={sum(s[2:])/(len(s)-2):.2f}')
for cost in (.15,.05):
    print('--- round-trip cost',cost)
    for tp in (3,5):
        run(f'buy at CREATE, TP{tp} SL.5',lambda c:True,'create',tp,.5,cost)
        run(f'buy at 1 MIN,  TP{tp} SL.5',lambda c:True,'min1',tp,.5,cost)
        run(f'CREATE + creator_grad>=1, TP{tp}',lambda c:c.get('creator_grads',0)>=1,'create',tp,.5,cost)

import json,glob,collections
P=collections.defaultdict(list);G={};C={}
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        try:r=json.loads(l)
        except: continue
        t=r['t']
        if t=='grad': G.setdefault(r['mint'],r['ts'])
        elif t=='snap' and r['mc']: P[r['mint']].append((r['ts'],r['mc'],'s'))
        elif t=='outcome' and r.get('mcap_usd'): P[r['mint']].append((r['ts'],r['mcap_usd'],'o'))
        elif t=='create': C[r['mint']]=r
print('graduated coins logged',len(G))
rows=[]
for m,gt in G.items():
    pts=sorted(p for p in P.get(m,[]) if p[1]<30e6)
    after=[p for p in pts if p[0]>=gt]
    if len(after)<2: continue
    e=after[0]; 
    if not 30000<e[1]<200000: continue
    rows.append((m,e,[p[1]/e[1] for p in after[1:]],gt))
print('with price path after graduation and entry mcap 30k-200k:',len(rows))
if rows:
  import statistics as st
  print('entry mcap median $%d'%st.median(r[1][1] for r in rows))
  for t in (1.5,2,3,5): print(f'  reached {t}x after graduation: {sum(max(r[2])>=t for r in rows)} ({100*sum(max(r[2])>=t for r in rows)/len(rows):.0f}%)')
  print('  fell below 0.5x at some point:',sum(min(r[2])<=0.5 for r in rows))
  for tp,sl in ((1.5,.7),(2,.7),(2,.5),(3,.5),(5,.5)):
    for cost in (.15,.08):
      R=[]
      for m,e,path,gt in rows:
        out=None
        for x in path:
            if x>=tp: out=tp;break
            if x<=sl: out=sl;break
        if out is None: out=path[-1]
        R.append(out*(1-cost))
      s=sorted(R,reverse=True);print(f'  TP{tp} SL{sl} cost{cost}: EV={sum(R)/len(R):.2f} exbest2={sum(s[2:])/(len(s)-2):.2f}')
  c=collections.Counter()
  for m,e,path,gt in rows:
    cr=C.get(m,{});c['creator_grad>=1' if cr.get('creator_grads',0)>=1 else 'other']+=1
  print(c)
print('--- momentum entry: wait until coin is >=X above graduation price, then buy')
for X in (1.15,1.3,1.5):
  R=[];H=[]
  for m,e,path,gt in rows:
    i=next((k for k,x in enumerate(path) if x>=X),None)
    if i is None or i>=len(path)-1: continue
    base=path[i];rest=[x/base for x in path[i+1:]]
    out=None
    for x in rest:
        if x>=2: out=2;break
        if x<=.7: out=.7;break
    if out is None: out=rest[-1]
    R.append(out*.9);H.append(max(rest)>=2)
  if len(R)>=15:
    s=sorted(R,reverse=True);print(f'  buy after +{int((X-1)*100)}%: n={len(R)} hit2x={100*sum(H)/len(H):.0f}% EV={sum(R)/len(R):.2f} exbest2={sum(s[2:])/(len(s)-2):.2f}')
  else: print('  X',X,'n',len(R))

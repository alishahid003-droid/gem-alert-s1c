import json,glob,collections,statistics as st
SOL=107.55
rows=[]
for f in sorted(glob.glob('early_log/2026-10-1*.jsonl')):
    for l in open(f,errors='ignore'):
        try: rows.append(json.loads(l))
        except: pass
oc=collections.defaultdict(dict)
for r in rows:
    if r['t']=='outcome':
        mc,lq=r.get('mcap_usd'),r.get('liq_usd')
        if mc and lq is not None and mc/max(lq,1)>150: mc=None
        if mc and r.get('pair_ts') and r['ts']-r['pair_ts']/1000>7200: mc=None
        if mc and mc>30e6: mc=None
        oc[r['mint']][r['cp_min']]=mc
fin={r['mint']:r for r in rows if r['t']=='final'}
dec={}
for r in rows:
    if r['t']=='decision' and r.get('lane')!='dev_list' and r.get('fb') is not None: dec.setdefault(r['mint'],r)
D=[]
for m,d in dec.items():
    f=fin.get(m,{})
    if not f.get('buys'): continue            # wrong token / no activity
    e=d['mcap_sol']*SOL
    pk=max([x/e for x in oc.get(m,{}).values() if x] or [0])
    if pk>500: continue
    D.append((pk,d,f,m))
print('clean coins with real buys:',len(D))
W=[x for x in D if x[0]>=5];L=[x for x in D if x[0]<2]
print('winners>=5x:',len(W),' >=10x:',sum(x[0]>=10 for x in D),' losers<2x:',len(L))
keys=[k for k,v in D[0][1].items() if isinstance(v,(int,float)) and not isinstance(v,bool) and k not in('ts',)]
print('feature          winners_median  losers_median   (decision-time)')
for k in keys:
    a=[x[1].get(k) for x in W if isinstance(x[1].get(k),(int,float))];b=[x[1].get(k) for x in L if isinstance(x[1].get(k),(int,float))]
    if len(a)>3 and len(b)>3: print(f'{k:16s} {st.median(a):12.3f} {st.median(b):12.3f}')
cw=collections.Counter(c for x in W for c in x[1].get('confirmations',[]));cl=collections.Counter(c for x in L for c in x[1].get('confirmations',[]))
print('confirmation share winners vs losers:')
for c in sorted(set(cw)|set(cl),key=lambda c:-cw[c]): print(f'  {c:16s} W {100*cw[c]/max(1,len(W)):3.0f}%  L {100*cl[c]/max(1,len(L)):3.0f}%')
print('final-row features (what happened by end of watch window):')
for k in ('buyers','buys','sells','fast','max_buy','creator_sold'):
    a=[x[2].get(k,0) for x in W];b=[x[2].get(k,0) for x in L]
    print(f'  {k:12s} W {st.median(a):.2f}  L {st.median(b):.2f}')
print('top winners:',[(round(x[0]),x[1].get('name','')[:10],round(x[1]['mcap_sol'],1)) for x in sorted(W,key=lambda x:-x[0])[:10]])

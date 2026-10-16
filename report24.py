import json,glob,collections,sys,statistics as st
sys.path.insert(0,'.')
from layers.fast_lane import score
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
    if r['t']=='decision' and r['ts']>1760417000 and r.get('lane')!='dev_list' and r.get('fb') is not None:
        dec.setdefault(r['mint'],r)
data=[];drop=0;nooc=0
for m,d in dec.items():
    e=d['mcap_sol']*SOL
    v=oc.get(m,{})
    if not any(v.values()): nooc+=1
    pk=max([x/e for x in v.values() if x] or [0])
    f=fin.get(m)
    if f and f.get('last_sol') and pk*e>20*max(f['last_sol'],d['mcap_sol'])*SOL: pk=0
    if pk>500: drop+=1;continue
    ex=(v.get(60) or v.get(15) or v.get(5) or 0)/e   # value at last checkpoint
    data.append(dict(s=score(d),pk=pk,ex=ex,d=d,m=m,ts=d['ts']))
print('decisions',len(dec),'usable',len(data),'dropped',drop,'no outcome',nooc)
def ret(x,cost=.15,mode='ex'): # buy at 1, sell at last checkpoint value (proxy), minus cost
    return x*(1-cost)
def stats(items,label):
    if len(items)<5: print(f'{label:18s} n={len(items)} (too few)');return
    r=[ret(i['ex']) for i in items]
    srt=sorted(r,reverse=True)
    mean=sum(r)/len(r); m2=sum(srt[2:])/max(1,len(srt)-2)
    pk=[i['pk'] for i in items]
    print(f'{label:18s} n={len(items):4d} hit2x={100*sum(p>=2 for p in pk)/len(pk):3.0f}% hit5x={100*sum(p>=5 for p in pk)/len(pk):3.0f}% hit10x={100*sum(p>=10 for p in pk)/len(pk):3.0f}% meanx={mean:.2f} ex-best2={m2:.2f} best={srt[0]:.1f}')
print('--- by score (return = value at last checkpoint, 15% cost)')
for s in sorted(set(i['s'] for i in data)): stats([i for i in data if i['s']==s],f'score {s}')
for t in (3,4,5,6): stats([i for i in data if i['s']>=t],f'score>={t}')
print('--- by confirmation')
cf=collections.defaultdict(list)
for i in data:
    for c in i['d'].get('confirmations',[]): cf[c if isinstance(c,str) else str(c)].append(i)
for c,l in sorted(cf.items(),key=lambda x:-len(x[1])): stats(l,c[:18])
stats(data,'ALL')
print('--- train/test split by time (score>=3 and >=5)')
data.sort(key=lambda i:i['ts']);h=len(data)//2
for nm,part in (('first half',data[:h]),('second half',data[h:])):
    stats([i for i in part if i['s']>=3],nm+' s>=3');stats([i for i in part if i['s']>=5],nm+' s>=5')

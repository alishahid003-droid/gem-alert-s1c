import sys
sys.path.insert(0, '.')
from executor.paper_ledger import _closed
import collections

rows = _closed()

def stat(sub, label):
    if not sub: print(f"{label}: n=0"); return
    n=len(sub)
    w=sum(1 for r in sub if r.get('win'))/n*100
    pnl=sum(r.get('pnl_usd') or 0 for r in sub)
    by_coin=collections.defaultdict(list)
    for r in sub: by_coin[r.get('token')].append(r)
    cw=sum(1 for t in by_coin.values() if any(x.get('win') for x in t))/len(by_coin)*100
    print(f"{label:55} trades n={n:4} win={w:5.1f}% net=${pnl:9.2f}  coins={len(by_coin):3} coin_win={cw:5.1f}%")

base = [r for r in rows if r.get('chain')=='base']
base_pass = [r for r in base if {'growth','volume'} <= set((r.get('tags') or {}).get('confirmations') or [])]
stat(base_pass, "BASE pass (growth+volume)")
stat([r for r in base if r not in base_pass], "BASE fail")

rhc = [r for r in rows if r.get('chain') in ('robinhood_chain','robinhood')]
rhc_pass = [r for r in rhc if (r.get('entry_mcap') or 0) >= 30000 and 'biglaunch' not in ((r.get('tags') or {}).get('confirmations') or [])]
stat(rhc_pass, "ROBINHOOD pass (NEW: mcap>=30k, no biglaunch)")
stat([r for r in rhc if r not in rhc_pass], "ROBINHOOD fail")

def sol_pass(r):
    tags = r.get('tags') or {}
    if tags.get('origin') != 'early': return False
    conf = tags.get('confirmations') or []
    mcap_sol = tags.get('mcap_sol')
    if mcap_sol is None: return False
    mcap_usd = mcap_sol * 150.0
    return 5000 <= mcap_usd < 20000 and len(conf) == 3 and 'hot_meta' not in conf

sol = [r for r in rows if r.get('chain')=='solana']
sol_early = [r for r in sol if (r.get('tags') or {}).get('origin')=='early']
sol_p = [r for r in sol_early if sol_pass(r)]
stat(sol_p, "SOLANA layer17 pass (mcap$5-20k,conf==3,no hot_meta)")
stat([r for r in sol_early if r not in sol_p], "SOLANA layer17 fail")

# repeat_grad check
rg = [r for r in sol_early if 'repeat_grad' in ((r.get('tags') or {}).get('confirmations') or [])]
by_coin_rg = collections.defaultdict(list)
for r in rg: by_coin_rg[r.get('token')].append(r)
print(f"\nrepeat_grad unique coins so far: {len(by_coin_rg)} (was 37 yesterday)")
clean = [c for c in by_coin_rg.values() if set((c[0].get('tags') or {}).get('confirmations') or []) <= {'repeat_grad','velocity_free','pressure_free'}]
print(f"'clean mechanical' repeat_grad coins: {len(clean)} (was 17 yesterday)")
if clean:
    w = sum(1 for c in clean if any(x.get('win') for x in c))
    pnl = sum(sum(x.get('pnl_usd') or 0 for x in c) for c in clean)
    print(f"  win={w}/{len(clean)} ({w/len(clean)*100:.1f}%)  net=${pnl:.2f}")

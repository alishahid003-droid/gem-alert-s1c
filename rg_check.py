import sys
sys.path.insert(0, '.')
from executor.paper_ledger import _closed
import collections

rows = _closed()
sol = [r for r in rows if r.get('chain')=='solana']
sol_early = [r for r in sol if (r.get('tags') or {}).get('origin')=='early']
rg = [r for r in sol_early if 'repeat_grad' in ((r.get('tags') or {}).get('confirmations') or [])]
by_coin = collections.defaultdict(list)
for r in rg: by_coin[r.get('token')].append(r)

clean_coins = {addr: trades for addr, trades in by_coin.items()
               if set((trades[0].get('tags') or {}).get('confirmations') or []) <= {'repeat_grad','velocity_free','pressure_free'}}

print(f"n={len(clean_coins)} clean repeat_grad coins")
pnls = []
for addr, trades in clean_coins.items():
    won = any(t.get('win') for t in trades)
    pnl = sum(t.get('pnl_usd') or 0 for t in trades)
    pnls.append(pnl)
    print(f"  won={won}  pnl=${pnl:7.2f}")

pnls.sort(reverse=True)
print(f"\ntotal net: ${sum(pnls):.2f}")
print(f"biggest single win: ${pnls[0]:.2f}")
print(f"net WITHOUT biggest win: ${sum(pnls)-pnls[0]:.2f}")
print(f"net WITHOUT top 2 wins: ${sum(pnls)-pnls[0]-pnls[1]:.2f}")

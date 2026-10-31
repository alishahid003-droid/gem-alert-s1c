import sys, time
sys.path.insert(0, '.')
from executor.paper_ledger import _closed
import collections

rows = _closed()
now = time.time()
cutoff = now - 14*3600

def base_pass(r):
    return {'growth','volume'} <= set((r.get('tags') or {}).get('confirmations') or [])
def rhc_pass(r):
    tags = r.get('tags') or {}
    conf = tags.get('confirmations') or []
    mcap = r.get('entry_mcap') or 0
    return mcap >= 30000 and 'biglaunch' not in conf
def sol_pass(r):
    tags = r.get('tags') or {}
    if tags.get('origin') != 'early': return False
    conf = tags.get('confirmations') or []
    mcap_sol = tags.get('mcap_sol')
    if mcap_sol is None: return False
    mcap_usd = mcap_sol * 150.0
    return 5000 <= mcap_usd < 20000 and len(conf) == 3 and 'hot_meta' not in conf

qualifying = []
for r in rows:
    ts = r.get('opened_ts') or 0
    if ts < cutoff:
        continue
    chain = r.get('chain')
    if chain == 'base' and base_pass(r):
        qualifying.append(r)
    elif chain in ('robinhood_chain','robinhood') and rhc_pass(r):
        qualifying.append(r)
    elif chain == 'solana' and sol_pass(r):
        qualifying.append(r)

qualifying.sort(key=lambda r: r.get('opened_ts') or 0)
print(f"Qualifying trades opened in last 14h: {len(qualifying)}")
by_chain = collections.Counter(r.get('chain') for r in qualifying)
print("by chain:", dict(by_chain))
w = sum(1 for r in qualifying if r.get('win'))
print(f"win rate: {w}/{len(qualifying)} = {w/len(qualifying)*100:.1f}%")

STAKE = 5.0
bank = 100.0
min_bank = bank
peak_bank = bank
log = []
for r in qualifying:
    pnl_pct = (r.get('pnl_pct') or 0) / 100.0
    stake_pnl = STAKE * pnl_pct
    bank += stake_pnl
    min_bank = min(min_bank, bank)
    peak_bank = max(peak_bank, bank)
    log.append((r.get('chain'), r.get('win'), stake_pnl, bank))

print(f"\n$100 start, $5/trade stake, {len(qualifying)} qualifying trades, last 14h:")
print(f"ending bank: ${bank:.2f}")
print(f"min bank touched: ${min_bank:.2f}")
print(f"peak bank touched: ${peak_bank:.2f}")
print(f"net change: ${bank-100:.2f}")

print("\nper-trade log:")
for chain, win, pnl, running in log:
    print(f"  {chain:16} win={win!s:5} trade_pnl=${pnl:7.2f}  running_bank=${running:8.2f}")

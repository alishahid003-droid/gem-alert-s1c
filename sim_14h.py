import sys, datetime, time
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
print(f"Qualifying (filter-passing) trades opened in last 14h: {len(qualifying)}")

by_chain = collections.Counter(r.get('chain') for r in qualifying)
print("by chain:", dict(by_chain))

# peak concurrent open positions among these, to pick a stake size
opens = [(r.get('opened_ts'), 1) for r in qualifying]
closes = [(r.get('closed_ts'), -1) for r in qualifying if r.get('closed_ts')]
events = sorted(opens+closes, key=lambda x: x[0] or 0)
cur = 0; peak = 0
for ts,d in events:
    cur += d
    peak = max(peak, cur)
print("peak concurrent open among qualifying trades:", peak)

STAKE = 5.0
bank = 100.0
min_bank = bank
trades_done = 0
total_staked_pnl = 0.0
for r in qualifying:
    pnl_pct = r.get('pnl_pct')
    if pnl_pct is None:
        pnl_usd = r.get('pnl_usd') or 0
        entry = r.get('entry_mcap') or 1
        pnl_pct = 0  # fallback, shouldn't hit
    stake_pnl = STAKE * (pnl_pct if pnl_pct is not None else 0)
    bank += stake_pnl
    total_staked_pnl += stake_pnl
    min_bank = min(min_bank, bank)
    trades_done += 1

print(f"\n$100 start, $5/trade stake, {trades_done} qualifying trades over last 14h:")
print(f"ending bank: ${bank:.2f}")
print(f"min bank touched: ${min_bank:.2f}")
print(f"total pnl from these trades: ${total_staked_pnl:.2f}")

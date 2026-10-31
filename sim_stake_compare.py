import sys, time
sys.path.insert(0, '.')
from executor.paper_ledger import _closed

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
    if ts < cutoff: continue
    chain = r.get('chain')
    if chain == 'base' and base_pass(r): qualifying.append(r)
    elif chain in ('robinhood_chain','robinhood') and rhc_pass(r): qualifying.append(r)
    elif chain == 'solana' and sol_pass(r): qualifying.append(r)
qualifying.sort(key=lambda r: r.get('opened_ts') or 0)

for STAKE in [5.0, 10.0, 15.0]:
    bank = 100.0
    min_bank = bank
    for r in qualifying:
        pnl_pct = (r.get('pnl_pct') or 0) / 100.0
        bank += STAKE * pnl_pct
        min_bank = min(min_bank, bank)
    print(f"stake=${STAKE:>5.1f}  ending=${bank:8.2f}  min_touched=${min_bank:8.2f}  pct_of_bank_at_6_concurrent={6*STAKE:.0f}%")

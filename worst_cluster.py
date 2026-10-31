import sys, time
sys.path.insert(0, '.')
from executor.paper_ledger import _closed

rows = _closed()

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
    chain = r.get('chain')
    if chain == 'base' and base_pass(r): qualifying.append(r)
    elif chain in ('robinhood_chain','robinhood') and rhc_pass(r): qualifying.append(r)
    elif chain == 'solana' and sol_pass(r): qualifying.append(r)

qualifying.sort(key=lambda r: r.get('opened_ts') or 0)
print(f"total qualifying trades, all-time: {len(qualifying)}")

# peak concurrent open AMONG QUALIFYING trades, all time
opens = [(r.get('opened_ts'), 1) for r in qualifying]
closes = [(r.get('closed_ts'), -1) for r in qualifying if r.get('closed_ts')]
events = sorted(opens+closes, key=lambda x: x[0] or 0)
cur = 0; peak = 0
for ts,d in events:
    cur += d
    peak = max(peak, cur)
print("ALL-TIME peak concurrent open among qualifying trades:", peak)

# worst rolling-window loss: in any 10-trade stretch, what's the worst combined pnl_pct sum?
pnl_pcts = [(r.get('pnl_pct') or 0)/100.0 for r in qualifying]
worst_window = 0
worst_start = 0
W = 6
for i in range(len(pnl_pcts)-W+1):
    s = sum(pnl_pcts[i:i+W])
    if s < worst_window:
        worst_window = s
        worst_start = i
print(f"\nworst any-6-consecutive-trades combined pnl_pct sum: {worst_window*100:.1f}% (starting trade #{worst_start})")
print("at $5/trade stake, worst 6-in-a-row loss would cost: $%.2f" % (5*worst_window))
print("at $10/trade stake, worst 6-in-a-row loss would cost: $%.2f" % (10*worst_window))

# single worst individual trade pnl_pct
worst_single = min(pnl_pcts)
print(f"\nsingle worst trade pnl_pct ever: {worst_single*100:.1f}%")
print("at $5 stake: $%.2f loss  |  at $10 stake: $%.2f loss" % (5*worst_single, 10*worst_single))

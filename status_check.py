import sys, datetime
sys.path.insert(0, '.')
from executor.paper_ledger import _closed
import collections

rows = _closed()
print("total closed trades:", len(rows))
ts = [r.get('opened_ts') for r in rows if r.get('opened_ts')]
lo, hi = min(ts), max(ts)
print("earliest:", datetime.datetime.utcfromtimestamp(lo), "UTC")
print("latest:  ", datetime.datetime.utcfromtimestamp(hi), "UTC")
print("span hours: %.2f" % ((hi-lo)/3600))
print("now utc:", datetime.datetime.utcnow())

by_chain = collections.Counter(r.get('chain') for r in rows)
print("\nby chain (total closed, unfiltered):", dict(by_chain))
for chain in ['solana','robinhood_chain','base','bsc']:
    sub = [r for r in rows if r.get('chain')==chain]
    if not sub: continue
    addrs = set(r.get('token') for r in sub)
    w = sum(1 for r in sub if r.get('win'))
    pnl = sum(r.get('pnl_usd') or 0 for r in sub)
    print(f"{chain:16} trades={len(sub):5} unique_coins={len(addrs):4} win={w/len(sub)*100:5.1f}% net=${pnl:10.2f}")

# critically: has anything landed in the NEW 30-50k robinhood band since restart? (proves live code)
rhc = [r for r in rows if r.get('chain') in ('robinhood_chain','robinhood')]
new_band = [r for r in rhc if 30000 <= (r.get('entry_mcap') or 0) < 50000]
print(f"\nrobinhood_chain trades with entry_mcap in [30k,50k): {len(new_band)}")
for r in new_band[-10:]:
    opened = r.get('opened_ts')
    when = datetime.datetime.utcfromtimestamp(opened) if opened else None
    print(f"  mcap=${r.get('entry_mcap'):,.0f}  opened={when}  win={r.get('win')}  pnl=${r.get('pnl_usd'):.2f}")

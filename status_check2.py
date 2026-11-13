import sys
sys.path.insert(0, '.')
from executor.paper_ledger import _closed
from executor import books as bk
import json, collections

rows = _closed()
print("=== EQUITY PER BOOK ===")
for book in ['compound','sprint','core1','core2']:
    try:
        eq = bk.equity(book)
        bank = bk.banked(book) if hasattr(bk,'banked') else None
        print(book, ': equity=$%.2f'%eq, '  banked=$%.2f'%bank if bank is not None else '')
    except Exception as e:
        print(book, ': ERROR', e)

print()
print("=== CLOSED TRADES BY CHAIN ^(proxy for RHC modules^) ===")
by_chain = collections.defaultdict(list)
for r in rows:
    by_chain[r.get('chain')].append(r)
for chain, trades in by_chain.items():
    total = len(trades)
    wins = sum(1 for t in trades if t.get('win'))
    pnl = sum(t.get('pnl_usd') or 0 for t in trades)
    wr = 100.0*wins/total if total else 0
    print(chain, ': trades=', total, ' wins=', wins, ' wr=%.1f%%'%wr, ' net_pnl=$%.2f'%pnl)

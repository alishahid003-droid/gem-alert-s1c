"""streams.py -- where each of the four money streams stands (paper) and what it needs.  Run: python streams.py
Reads the same ledger/books the dashboard uses. Read-only."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:      # noqa: BLE001
    pass
from executor import books, paper_ledger

STREAMS = [
    ("sprint", "BILLS stream (Sprint 96h)", "fast_sprint"),
    ("core1", "Core 2 ($125k-150k, 30d)", "fast_core"),
    ("core2", "Core 3 ($1M-1.5M, 180d)", "fast_moon"),
    ("compound", "Compound (in/out small runners)", "compound"),
]
WALLET_SPLIT = {"sprint": 0.50, "compound": 0.28, "core1": 0.16, "core2": 0.06}     # share of the live wallet once a stream is proven


def main():
    wallet = float(os.environ.get("LIVE_WALLET_USD", "25") or 25)    # PumpPortal wallet, ~25 on 14 Oct
    print(f"Live wallet ${wallet:.2f}. Planned split once proven (only Sprint can be live today):")
    for b, name, prof in STREAMS:
        c = books.cfg(b)
        eq = books.total_value(b)
        p = books.pace(b, time.time(), eq)
        rows = [r for r in paper_ledger.module_rows(b, include_runners=True) if str(r.get("exit_profile") or "").startswith("fast_") or b == "compound"]
        wins = sum(1 for r in rows if (r.get("pnl_usd") or 0) > 0)
        pnl = sum(float(r.get("pnl_usd") or 0) for r in rows)
        opn = books.open_count(b)
        t = f"target ${c['target']:,.0f}" if c.get("target") else "daily-profit goal"
        need = f", needs {p['needed_daily_growth_pct']}%/day" if p.get("has_target") and p.get("needed_daily_growth_pct") is not None else ""
        print(f"\n{name}\n  paper balance ${eq:,.2f}  ({t}{need})  open {opn}  closed {len(rows)}  wins {wins}  fast P&L ${pnl:,.2f}"
              f"\n  planned live share {int(WALLET_SPLIT[b]*100)}% = ${wallet*WALLET_SPLIT[b]:.2f}")


if __name__ == "__main__":
    main()

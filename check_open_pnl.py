import sys
sys.path.insert(0, ".")
from layers.layer0_scoring import fetch_dexscreener_snapshot

positions = [
    ("robinhood_chain", "0x0a43eca18d7a29125298334667835e5043c594f0", 32113.84553, 20.08),
    ("robinhood_chain", "0x443da47242b5d3c749e459f82b5c0f444f17e19c", 44314.20013, 186.03),
    ("robinhood_chain", "0xeab88b5ae3c9e0be789ad9b6f7701fe9dede4e6f", 41870.19962, 72.94),
]

for chain, addr, entry_mcap, stake in positions:
    snap = None
    try:
        snap = fetch_dexscreener_snapshot(chain, addr)
    except Exception as e:
        print(f"{addr[:10]}... ERROR: {e}")
        continue
    cur_mcap = snap.get("mcap_usd") if snap else None
    if cur_mcap:
        mult = cur_mcap / entry_mcap
        print(f"{addr[:10]}... entry=${entry_mcap:,.0f} current=${cur_mcap:,.0f} mult={mult:.3f}x stake=${stake:.2f} unrealized=${stake*(mult-1):+.2f}")
    else:
        print(f"{addr[:10]}... NO PRICE DATA")

"""sell_orphans.py -- sell 100% of coins the bot bought live but lost track of (14 Oct 15:30 PKT bug).
Usage:  python sell_orphans.py            (sells the mints listed below, asks YES first)
        python sell_orphans.py MINT ...   (sell other mints)
Reads the key from .env, never prints it. Every reply goes to logs\\live_exec.log."""
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()
from executor import pumpportal_exec  # noqa: E402

MINTS = ["FiMxPVpgJj6Y9qC3H6ZSSi8zn5wtPiiQsVNHPAE8pump",     # iCoin
         "HMNubqMJkR51GXaiYHWKExpB7Ks8izq2sBhSm4bSpump"]      # dotfomo


def main():
    mints = sys.argv[1:] or MINTS
    print("About to SELL 100% (real money) of:")
    for m in mints:
        print("  ", m)
    if input("Type YES to sell: ").strip().upper() != "YES":
        print("Cancelled.")
        return
    for m in mints:
        r = pumpportal_exec.sell(m, 100)
        sig = (r.get("resp") or {}).get("signature") if isinstance(r.get("resp"), dict) else None
        print(m[:8], "->", "SOLD, tx " + sig if r.get("ok") and sig else f"FAILED: {str(r)[:160]}")
        time.sleep(2)
    print("Done. Check Solscan / Phantom for the wallet.")


if __name__ == "__main__":
    main()

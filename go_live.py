"""ONE-COMMAND SWITCH: run this the moment the wallet is funded, to go from paper testing to
real money on sprint + compound, both reseeded to a clean $50.

31 Oct 2026 (Ali): "the only part should be left on my end is to fund the wallet so that it gets
deployed to automatically compound and sprint ... we just enable the live version of this system.
... The paper testing should be run till I fund the wallet and let you know."

What this does, in order:
  1. Force-reseeds sprint and compound to $50 each (wipes today's paper P&L from the running
     total via executor.books.force_reseed -- see that function's comment for the mechanism).
     Nothing else (core1/core2/copy books) is touched.
  2. Prints the exact lines to add/confirm in .env for live execution to actually use $50 seeds
     and the staged compound structure -- this script does NOT edit .env itself, on purpose:
     .env holds live secrets and should be a deliberate, reviewed edit, not something a script
     silently rewrites right before going live with real money.
  3. Prints the current book status for both, post-reseed, so you can see $50.00 confirmed before
     restarting the system.

Usage (after funding the wallet):
    python3 go_live.py

This does NOT restart the system and does NOT flip EXECUTION_ENABLED on its own -- those are your
calls to make explicitly in .env, listed below, then restart local_runner.py as usual.
"""
import os
import sys
import time

sys.path.insert(0, ".")
from executor import books  # noqa: E402

TARGET_SEED = 50.0


def main():
    now = time.time()
    print("=" * 70)
    print("GO LIVE: reseeding sprint + compound to $50, wiping paper P&L")
    print("=" * 70)

    # Guard: force_reseed() zeroes realized P&L but reads the seed from cfg(), which only reports
    # $50 if SPRINT_SEED_USD/COMPOUND_SEED_USD are actually set in the environment (.env). Running
    # this before setting them would reseed to the BOOKS dict default ($100), silently -- caught in
    # testing (dry run showed "now reads $100.00" with no override set). Refuse to proceed rather
    # than reseed to the wrong number.
    missing = [v for v in ("SPRINT_SEED_USD", "COMPOUND_SEED_USD") if not os.environ.get(v, "").strip()]
    if missing:
        print(f"\nSTOP: {', '.join(missing)} not set in the environment (.env).")
        print("Add these two lines to .env FIRST, then re-run this script:")
        print("  SPRINT_SEED_USD=50")
        print("  COMPOUND_SEED_USD=50")
        print("\nNothing was reseeded.")
        return 1

    for book_id in ("sprint", "compound"):
        expected_seed = books.cfg(book_id)["seed"]
        if expected_seed != TARGET_SEED:
            print(f"\nSTOP: {book_id}'s configured seed is ${expected_seed:.2f}, not "
                  f"${TARGET_SEED:.2f}. Check {book_id.upper()}_SEED_USD in .env. Nothing was reseeded.")
            return 1
        before = books.total_value(book_id)
        snap = books.force_reseed(book_id, now, reason="go_live_50usd_31oct2026")
        after = books.total_value(book_id)
        print(f"\n{book_id}: paper total_value was ${before:,.2f} -> reseed snapshot banked "
              f"${snap['realized_at_bust']:,.2f} of history -> now reads ${after:,.2f}")

    print("\n" + "-" * 70)
    print("Before restarting, confirm these are set in .env for the LIVE run:")
    print("-" * 70)
    print("""
SPRINT_SEED_USD=50
COMPOUND_SEED_USD=50
EXECUTION_ENABLED=true          # already true -- confirm it's still what you want live
EARLY_LIVE_LANES=sprint         # confirm compound should also go live if you want both real
LIVE_MAX_STAKE_USD=5            # per-trade real-money ceiling -- revisit for $50 seed if needed
LIVE_MAX_OPEN=3
LIVE_DAILY_LOSS_USD=17
TOTAL_WALLET_USD=<your real funded amount>
""")
    print("-" * 70)
    print("Post-reseed status check:")
    print("-" * 70)
    for book_id in ("sprint", "compound"):
        s = books.status(book_id, now)
        print(f"\n{book_id}: {books.line(book_id)}")
        print(f"  seed=${s['seed']:.2f}  equity=${s['equity']:.2f}  banked=${s['banked']:.2f}  "
              f"total_value=${s['total_value']:.2f}  open={s['open']}")

    print("\nDone. Once .env is confirmed and the wallet is funded, restart local_runner.py as usual.")


if __name__ == "__main__":
    sys.exit(main() or 0)

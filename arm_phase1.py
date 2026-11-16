"""One-off script: arms executor/phase_target.py's real drawdown breaker + target-hit tracking
for Phase 1 of Ali's 4-phase plan, right now. Written 16 Nov 2026 after Ali asked for the
system's one real protective mechanism (the drawdown circuit breaker) to actually be turned on,
rather than sit inert as it has been since this module was first built.

MUST be run on the machine actually executing real trades (where RPC reads to
robinhood_chain/base genuinely succeed) -- NOT from a sandbox with no route to the chain, which
would set a false, artificially-low peak_equity baseline (reading the real wallet as $0 on an
RPC failure) and could cause the breaker to misfire against a wrong peak forever after. This
script checks that the real read succeeds before arming, and refuses (does nothing, prints why)
if it can't confirm a real number first.

Run once: `python3 arm_phase1.py`. Safe to re-run -- start_phase() is idempotent (re-arming
phase 1 again just resets the baseline to whatever combined equity is at that moment; it will
print the current status either way so you can see exactly what it did).
"""
import sys

from dotenv import load_dotenv
load_dotenv()

from executor import books, phase_target as pt, swap_executor as se


def main():
    real_check = se.get_total_real_wallet_usd_or_none()
    if real_check is None:
        print("REFUSING to arm: the real wallet balance could not be read from this machine "
              "right now (RPC unreachable or key unreadable). Re-run this once the connection "
              "is good -- arming now would set a false, artificially-low baseline.")
        sys.exit(1)

    print(f"Real wallet balance confirmed readable: ${real_check:,.2f}")
    print(f"Real compound equity (equity_real()):  ${books.equity_real():,.2f}")
    print(f"Combined equity about to be used as the phase-1 baseline: "
          f"${pt.combined_equity():,.2f}")

    already_active = pt.status().get("active")
    if already_active:
        print(f"\nphase_target is ALREADY active (phase {pt.status()['phase']}) -- re-arming "
              f"phase 1 will reset its peak_equity baseline to the current combined equity. "
              f"Confirm this is what you want before re-running if you weren't expecting this.")

    result = pt.start_phase("1")
    print("\nArmed. start_phase('1') result:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    print("\nFull status():")
    for k, v in pt.status().items():
        print(f"  {k}: {v}")

    print("\nDone. scheduler.py / worker_fast_watch.py already call phase_target.check() every "
          "poll cycle -- no restart needed, the drawdown breaker and phase-1 target tracking "
          "are live as of this call.")


if __name__ == "__main__":
    main()

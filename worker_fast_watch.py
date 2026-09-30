"""
Fast watcher -- GO_LIVE_CHECKLIST 5.1 (Sept 30 2026). Runs on Ali's PC.

Why: a scalper/sniper cannot run on a 10-minute clock. The compound
scalper's time-stop is 20 minutes, a momentum move can start and finish
inside one GitHub cycle, and a stop-loss that checks every 10 minutes can
fill far below its level. This loop does the management every
FAST_INTERVAL_SECONDS (default 20 s) instead:

  - every tick:  real positions (stop-loss / breakeven lock / trailing /
                 time stop / moonbag / rug exit) and the compound-scalper
                 pool -- scheduler._run_position_management_cycle and
                 _run_compound_scalper_cycle, unchanged;
                 paper positions (executor.paper_ledger.manage);
  - every 3rd tick (~1 min): the Layer 14 revival watch (one free
                 DexScreener batch call per chain) -- momentum revivals go
                 straight to the scalper's momentum entry.

It stamps a "fast-watch" heartbeat every tick. While that heartbeat is
fresh, GitHub's poll-fast skips the same work (scheduler.
fast_watch_owns_management), so exactly one runner writes position/paper
state at a time; if the PC sleeps, GitHub takes over again on its own.

No MadeOnSol calls. Discovery (finding new coins) stays on the existing
10-minute GitHub cycle; this loop only reacts fast to coins already known.

Usage:  python worker_fast_watch.py [--interval 20]
Normally started at logon by setup_pc.bat ("GemAlert FastWatch" task).
"""
import argparse
import time
import traceback

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import state  # noqa: E402
import scheduler  # noqa: E402
import executor.paper_ledger as paper_ledger  # noqa: E402
from layers.layer0_scoring import fetch_dexscreener_snapshot  # noqa: E402
from layers.layer11_social_buzz import fetch_boost_board  # noqa: E402

REVIVAL_EVERY_N_TICKS = 3


def tick(n: int) -> dict:
    out = {}
    state.record_runner_heartbeat("fast-watch", scheduler._runner_where(), f"tick {n}")
    out["positions"] = scheduler._safe(scheduler._run_position_management_cycle)
    out["scalper"] = scheduler._safe(scheduler._run_compound_scalper_cycle)
    out["paper"] = scheduler._safe(paper_ledger.manage, fetch_dexscreener_snapshot,
                                  batch_fn=scheduler.layer14.fetch_dexscreener_batch)
    if n % REVIVAL_EVERY_N_TICKS == 0:
        board = scheduler._safe(fetch_boost_board)
        board = board if isinstance(board, dict) and board.get("ok") else None
        out["revival"] = scheduler._safe(scheduler._run_revival_watch_cycle, board)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--once", action="store_true", help="one tick then exit (testing)")
    args = ap.parse_args()
    n = 0
    print(f"[fast-watch] started, every {args.interval:.0f}s (state backend: {state.backend()})", flush=True)
    while True:
        started = time.time()
        try:
            out = tick(n)
            paper = out.get("paper") if isinstance(out.get("paper"), dict) else {}
            rev = out.get("revival") if isinstance(out.get("revival"), dict) else {}
            print(f"[fast-watch] tick {n}: paper open {paper.get('open')}, closed {paper.get('closed_now')}"
                  + (f", revival checked {rev.get('checked')} revived {rev.get('revived')}" if rev else ""),
                  flush=True)
        except Exception:  # the loop must never die on one bad tick
            traceback.print_exc()
        n += 1
        if args.once:
            return
        time.sleep(max(1.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    main()

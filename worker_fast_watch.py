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

FAST_INTERVAL_SECONDS = 20.0   # while real money is in a position / the scalper holds a coin
IDLE_INTERVAL_SECONDS = 60.0   # otherwise (checklist 5.4: Upstash command budget)


def busy() -> bool:
    """True while real money is at stake: an executor position that holds
    tokens, or an open compound-scalper position."""
    try:
        import executor.position_state as position_state
        import executor.compound_scalper as compound_scalper
        if compound_scalper.sprint_mode():
            return True      # sprint: 20-s ticks for the momentum lane
        if any(p.get("amount_tokens") for p in position_state.list_open_positions()):
            return True
        return bool(compound_scalper.status().get("open_position"))
    except Exception:
        return True   # when unsure, stay fast


def tick(n: int, fast: bool = True) -> dict:
    """Fast mode: positions + scalper every tick (20 s), paper + revival every
    3rd tick (~1 min). Idle mode (60 s ticks): paper and revival alternate,
    each every ~2 min. Heartbeat every tick (ownership window is 120 s)."""
    out = {}
    note = f"tick {n} {'fast' if fast else 'idle'} {state.commands_per_minute():.0f} state cmds/min"
    state.record_runner_heartbeat("fast-watch", scheduler._runner_where(), note)
    out["positions"] = scheduler._safe(scheduler._run_position_management_cycle)
    out["scalper"] = scheduler._safe(scheduler._run_compound_scalper_cycle)
    out["lane"] = scheduler._safe(scheduler._run_momentum_lane)     # paper always; real buys in sprint
    do_paper = (n % 3 == 0) if fast else (n % 2 == 0)
    do_revival = (n % 3 == 0) if fast else (n % 2 == 1)
    if do_paper:
        out["paper"] = scheduler._safe(paper_ledger.manage, fetch_dexscreener_snapshot,
                                      batch_fn=scheduler.layer14.fetch_dexscreener_batch)
    if do_revival:
        board = scheduler._safe(fetch_boost_board)
        board = board if isinstance(board, dict) and board.get("ok") else None
        out["revival"] = scheduler._safe(scheduler._run_revival_watch_cycle, board)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=None,
                    help="fixed seconds between ticks (default: 20 s busy / 60 s idle)")
    ap.add_argument("--once", action="store_true", help="one tick then exit (testing)")
    args = ap.parse_args()
    n = 0
    print(f"[fast-watch] started ({'every %.0fs' % args.interval if args.interval else '20 s busy / 60 s idle'}, "
          f"state backend: {state.backend()})", flush=True)
    while True:
        started = time.time()
        fast = busy()
        try:
            out = tick(n, fast)
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
        interval = args.interval or (FAST_INTERVAL_SECONDS if fast else IDLE_INTERVAL_SECONDS)
        time.sleep(max(1.0, interval - (time.time() - started)))


if __name__ == "__main__":
    main()

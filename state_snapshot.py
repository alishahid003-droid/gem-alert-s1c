"""State snapshot tool (6 Nov 2026, Ali).

Periodically copies .gem_alert_state.json to backups/state_snapshots/ so a
reset, crash, or bad script run can never again permanently erase trade
history -- this is exactly what happened to the real $100->$4,100 (344-trade)
run earlier tonight: its only record was a chat screenshot, because no
snapshot existed to recover the underlying data from.

READ-ONLY with respect to the live system: this script only ever reads
.gem_alert_state.json (through the same file lock every other reader uses,
so it never sees a half-written file) and writes to a separate backups/
folder. It never writes back to the live state file, and it is run as its
own standalone process -- not imported into or called from worker_fast_watch.py,
local_runner.py, or any other live trading process, so a bug in this script
cannot affect trading.

Usage:
    python state_snapshot.py             # take one snapshot now
    python state_snapshot.py --watch 600 # loop forever, snapshot every 600s
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

STATE_FILE = os.environ.get("GEM_ALERT_STATE_FILE", ".gem_alert_state.json")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups", "state_snapshots")
KEEP_LAST_N = 200  # prune older snapshots beyond this count (disk safety)


def _take_one() -> str | None:
    import state  # local import: only needed when actually snapshotting

    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILE)
    if not os.path.exists(src):
        print(f"[snapshot] no state file at {src}, skipping")
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"state_{ts}.json")

    # Take the same lock every real reader/writer uses, so we never copy a
    # half-written file mid os.replace().
    with state._local_file_lock():
        shutil.copy2(src, dst)

    _prune()
    print(f"[snapshot] saved {dst}")
    return dst


def _prune() -> None:
    files = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.startswith("state_") and f.endswith(".json")),
    )
    excess = len(files) - KEEP_LAST_N
    for f in files[:max(0, excess)]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass


def watch_loop(interval_s: int) -> None:
    """Runs forever, snapshotting every interval_s seconds. Never raises -- a single bad
    snapshot is logged and skipped rather than killing the thread/process it runs in."""
    print(f"[snapshot] watching every {interval_s}s -> {BACKUP_DIR}")
    while True:
        try:
            _take_one()
        except Exception as e:  # never let a snapshot failure crash the loop
            print(f"[snapshot] error (continuing): {e}")
        time.sleep(interval_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0, help="loop forever, snapshot every N seconds")
    args = parser.parse_args()

    if args.watch <= 0:
        _take_one()
        return

    watch_loop(args.watch)


if __name__ == "__main__":
    main()

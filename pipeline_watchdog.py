"""Pipeline watchdog (24 Nov 2026, Ali: "add a watchdog" -- real incident:
poll-fast hung 6.2 hours on 24 Nov 2026 inside one requests.get() call
(fetch_goplus_security(), a stalled chunked read eventually raising
ChunkedEncodingError -- see utils/http.py's _as_connect_read_timeout for the
actual fix to that call). While it hung, multichain_sniper.py's candidate feed
went fully silent for 7.3 hours (logs/candidate_eval.jsonl had zero new lines)
with nothing anywhere surfacing that fact -- Ali only found out by asking why
trading looked slow.

This script is READ-ONLY with respect to every file it checks, same contract
as state_snapshot.py: it never writes to a live log or state file, only reads
mtimes/tail lines and (on a stale finding) calls state.log_full_alert(), which
is the existing dashboard Alerts-tab mechanism already used by phase_target.py
and dev_alerts.py. It runs as its own daemon thread inside local_runner.py
(added the same way state_snapshot.watch_loop() is already wired in), so a
bug here cannot affect trading, and it is independently disableable via
LOCAL_RUNNER_WATCHDOG=false.

What it checks, every WATCHDOG_INTERVAL_S (default 300s = 5 min):
  1. logs/candidate_eval.jsonl -- the real candidate-discovery feed written by
     multichain_sniper.py. If its last line is older than
     WATCHDOG_CANDIDATE_STALE_S (default 3600s = 1h), the scanning pipeline is
     silently stuck (this is exactly what happened during the 6.2h hang).
  2. logs/local_poll-fast.log -- if the most recent "=== <ts> poll-fast ==="
     run-start marker is older than WATCHDOG_POLLFAST_STALE_S (default
     2400s = 40 min, i.e. the 25-min job timeout plus a safety margin), a
     poll-fast cycle is overdue to have even STARTED again, meaning the
     previous one is still running/stuck past its own timeout window.
Each distinct stale condition fires at most once per WATCHDOG_ALERT_COOLDOWN_S
(default 3600s) so a prolonged outage doesn't spam the alert feed once per
5-minute check.

Usage (mirrors state_snapshot.py):
    python pipeline_watchdog.py           # run one check now, print result, exit
    python pipeline_watchdog.py --watch   # loop forever (what local_runner.py uses)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE_LOG = os.path.join(HERE, "logs", "candidate_eval.jsonl")
POLLFAST_LOG = os.path.join(HERE, "logs", "local_poll-fast.log")

WATCHDOG_INTERVAL_S = float(os.environ.get("WATCHDOG_INTERVAL_S", "300"))
WATCHDOG_CANDIDATE_STALE_S = float(os.environ.get("WATCHDOG_CANDIDATE_STALE_S", "3600"))
WATCHDOG_POLLFAST_STALE_S = float(os.environ.get("WATCHDOG_POLLFAST_STALE_S", "2400"))
WATCHDOG_ALERT_COOLDOWN_S = float(os.environ.get("WATCHDOG_ALERT_COOLDOWN_S", "3600"))

_last_alert_ts: dict = {}


def _tail_last_line(path: str, max_bytes: int = 8192):
    """Read only the last chunk of a file, not the whole thing -- both logs
    this watches can grow to many MB over a long run."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            chunk = fh.read()
    except OSError:
        return None
    lines = [ln for ln in chunk.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _candidate_feed_age_s() -> float | None:
    """Seconds since the last candidate_eval.jsonl entry's own ts field (not
    file mtime -- the file could be touched by something else)."""
    line = _tail_last_line(CANDIDATE_LOG)
    if not line:
        return None
    try:
        rec = json.loads(line)
        ts = float(rec.get("ts"))
    except (ValueError, TypeError, KeyError):
        return None
    return time.time() - ts


def _pollfast_last_start_age_s() -> float | None:
    """Seconds since the most recent '=== <timestamp> poll-fast ===' marker."""
    line = _tail_last_line(POLLFAST_LOG, max_bytes=65536)
    # the marker line itself may not be the literal last line if a run is
    # mid-output, so scan backward through the tail for the last marker
    try:
        with open(POLLFAST_LOG, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    marker_lines = [ln for ln in chunk.splitlines() if ln.startswith("=== ") and ln.endswith(" poll-fast ===")]
    if not marker_lines:
        return None
    raw_ts = marker_lines[-1][4:-len(" poll-fast ===")]
    try:
        started = time.mktime(time.strptime(raw_ts, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    return time.time() - started


def _maybe_alert(key: str, headline: str, tags: dict):
    now = time.time()
    if now - _last_alert_ts.get(key, 0.0) < WATCHDOG_ALERT_COOLDOWN_S:
        return False
    _last_alert_ts[key] = now
    try:
        import state
        state.log_full_alert("pipeline_watchdog", "multichain", "WATCHDOG", key, headline, tags, ts=now)
    except Exception as e:  # noqa: BLE001 -- never let an alert failure kill the watchdog
        print(f"[pipeline_watchdog] WARNING: could not post alert ({e}): {headline}", flush=True)
    print(f"[pipeline_watchdog] ALERT: {headline}", flush=True)
    return True


def check_once() -> dict:
    result = {"ts": time.time(), "alerts_fired": []}

    cand_age = _candidate_feed_age_s()
    result["candidate_feed_age_s"] = cand_age
    if cand_age is not None and cand_age > WATCHDOG_CANDIDATE_STALE_S:
        headline = (f"Candidate feed silent for {cand_age/60:.0f} min "
                    f"(threshold {WATCHDOG_CANDIDATE_STALE_S/60:.0f} min) -- "
                    "scanning pipeline may be stuck")
        if _maybe_alert("candidate_feed_stale", headline, {"age_s": round(cand_age)}):
            result["alerts_fired"].append("candidate_feed_stale")

    pf_age = _pollfast_last_start_age_s()
    result["pollfast_last_start_age_s"] = pf_age
    if pf_age is not None and pf_age > WATCHDOG_POLLFAST_STALE_S:
        headline = (f"poll-fast has not started a new run in {pf_age/60:.0f} min "
                    f"(threshold {WATCHDOG_POLLFAST_STALE_S/60:.0f} min) -- "
                    "previous run may still be stuck")
        if _maybe_alert("pollfast_stale", headline, {"age_s": round(pf_age)}):
            result["alerts_fired"].append("pollfast_stale")

    return result


def watch_loop(interval_s: float = WATCHDOG_INTERVAL_S) -> None:
    print(f"[pipeline_watchdog] watching every {interval_s:.0f}s "
          f"(candidate feed stale > {WATCHDOG_CANDIDATE_STALE_S/60:.0f}min, "
          f"poll-fast stale > {WATCHDOG_POLLFAST_STALE_S/60:.0f}min)", flush=True)
    while True:
        started = time.time()
        try:
            check_once()
        except Exception as e:  # noqa: BLE001 -- daemon thread must never die
            print(f"[pipeline_watchdog] ERROR during check: {e}", flush=True)
        time.sleep(max(5.0, interval_s - (time.time() - started)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="loop forever instead of checking once")
    args = ap.parse_args()
    if args.watch:
        watch_loop()
    else:
        print(json.dumps(check_once(), indent=2))


if __name__ == "__main__":
    main()

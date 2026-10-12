"""
Local-mode supervisor (Oct 6 2026) -- runs the cycles GitHub Actions normally
runs, on this PC, when Upstash is not available.

Why: with no Upstash, state lives in a local file (state.py, safe for several
processes on one machine). GitHub runners cannot see that file, so every cycle
has to run here. This loop launches, one at a time (they share API rate
limits), the same commands the workflows run:

    scheduler.py --poll-fast-once    every LOCAL_POLL_FAST_MINUTES  (default 10)
    scheduler.py --poll-slow         every LOCAL_POLL_SLOW_MINUTES  (default 20)
    scheduler.py --poll-madeonsol    every LOCAL_MADEONSOL_MINUTES  (default 15)

The fast watcher (worker_fast_watch.py) and the dashboard run as their own
processes (start_local_mode.bat starts all three). Set LOCAL_RUNNER_MADEONSOL=false
if the Windows Task Scheduler job for --poll-madeonsol is still enabled, so the
MadeOnSol budget is not spent twice.

Nothing here trades by itself: execution is still controlled by the existing
EXECUTION_ENABLED / SPRINT_MODE / MOONSHOT_ENABLED switches in .env.
"""
import os
import subprocess
import sys
import time
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def schedule() -> List[Tuple[str, str, float]]:
    """[(name, scheduler flag, interval seconds)] in run order."""
    jobs = [
        ("poll-fast", "--poll-fast-once", _f("LOCAL_POLL_FAST_MINUTES", 10) * 60),
        ("poll-slow", "--poll-slow", _f("LOCAL_POLL_SLOW_MINUTES", 20) * 60),
    ]
    if os.environ.get("LOCAL_RUNNER_MADEONSOL", "true").strip().lower() != "false":
        jobs.append(("poll-madeonsol", "--poll-madeonsol", _f("LOCAL_MADEONSOL_MINUTES", 15) * 60))
    return jobs


def due_jobs(last_run: Dict[str, float], now: float, jobs=None) -> List[Tuple[str, str, float]]:
    """Jobs whose interval has elapsed (never-run jobs are due), most overdue first."""
    jobs = jobs if jobs is not None else schedule()
    due = [(now - last_run.get(n, 0.0) - iv, n, flag, iv) for n, flag, iv in jobs
           if now - last_run.get(n, 0.0) >= iv]
    due.sort(reverse=True)
    return [(n, flag, iv) for _, n, flag, iv in due]


def run_job(name: str, flag: str, timeout_s: float) -> int:
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    log = os.path.join(HERE, "logs", f"local_{name}.log")
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {name} ===\n")
        fh.flush()
        try:
            r = subprocess.run([sys.executable, "-u", os.path.join(HERE, "scheduler.py"), flag],
                               cwd=HERE, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout_s)
            return r.returncode
        except subprocess.TimeoutExpired:
            fh.write(f"[local_runner] {name} timed out after {timeout_s:.0f}s -- killed\n")
            return -1


def radar_enabled() -> bool:
    return os.environ.get("LOCAL_RUNNER_RADAR", "true").strip().lower() != "false"


def radar_interval_s() -> float:
    return max(60.0, _f("LOCAL_RADAR_MINUTES", 3) * 60)


def _radar_loop() -> None:
    """Own thread so a long poll-slow/madeonsol job never delays the radar."""
    iv = radar_interval_s()
    while True:
        started = time.time()
        try:
            rc = run_job("poll-radar", "--poll-radar", timeout_s=min(iv * 3, 600))
        except Exception as e:  # noqa: BLE001
            rc = f"error {e}"
        print(f"[local_runner] {time.strftime('%H:%M:%S')} poll-radar exit={rc} ({time.time() - started:.0f}s)", flush=True)
        time.sleep(max(5.0, iv - (time.time() - started)))


def early_enabled() -> bool:
    return os.environ.get("LOCAL_RUNNER_EARLY", "true").strip().lower() != "false"


def _early_loop() -> None:
    """Supervises worker_early_watch.py (the real-time launch sniper): restarts it if it dies."""
    backoff = 5.0
    log = os.path.join(HERE, "logs", "local_early-watch.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    while True:
        started = time.time()
        try:
            with open(log, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} early-watch start ===\n")
                fh.flush()
                rc = subprocess.run([sys.executable, "-u", os.path.join(HERE, "worker_early_watch.py")],
                                    cwd=HERE, stdout=fh, stderr=subprocess.STDOUT).returncode
        except Exception as e:  # noqa: BLE001
            rc = f"error {e}"
        print(f"[local_runner] {time.strftime('%H:%M:%S')} early-watch exited rc={rc}; restarting", flush=True)
        backoff = 5.0 if time.time() - started > 120 else min(backoff * 2, 120.0)
        time.sleep(backoff)


def main() -> None:
    last: Dict[str, float] = {}
    if early_enabled():
        import threading
        threading.Thread(target=_early_loop, name="early", daemon=True).start()
        print("[local_runner] early-watch (launch sniper) supervisor started", flush=True)
    if os.environ.get("LOCAL_RUNNER_MULTICHAIN", "true").strip().lower() != "false":
        import threading
        import multichain_sniper
        threading.Thread(target=multichain_sniper.run_loop, name="multichain", daemon=True).start()
        print("[local_runner] multichain sniper (bsc/base/robinhood/solana+StonkFun) started", flush=True)
    if radar_enabled():
        import threading
        threading.Thread(target=_radar_loop, name="radar", daemon=True).start()
        print(f"[local_runner] radar thread started (every {int(radar_interval_s() // 60)} min)", flush=True)
    print("[local_runner] started. Jobs:", [(n, int(iv // 60)) for n, _, iv in schedule()], flush=True)
    while True:
        due = due_jobs(last, time.time())
        if not due:
            time.sleep(15)
            continue
        name, flag, _ = due[0]
        started = time.time()
        rc = run_job(name, flag, timeout_s=_f("LOCAL_JOB_TIMEOUT_MINUTES", 25) * 60)
        last[name] = started
        print(f"[local_runner] {time.strftime('%H:%M:%S')} {name} exit={rc} ({time.time() - started:.0f}s)", flush=True)


if __name__ == "__main__":
    main()

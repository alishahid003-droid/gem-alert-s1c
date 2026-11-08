"""API budget ledger (Ali, Oct 13 2026: free-tier calls must be paced to last the whole month).

Every HTTP call made through utils/http.py is appended to logs/api_calls_<UTC date>.log
(one line: epoch host status). This works across all the runner's processes and threads with no
shared-state locking. `python api_budget.py` aggregates it: calls today per API, busiest minute
(vs the per-minute limit), 429 count, projected month usage vs the monthly allowance.

LIMITS = (per_day, per_minute) for the free plan; None = unknown (tracked only). MadeOnSol BASIC is
200/day (confirmed by its own 429 message) = 6,000/month; the runner's guard keeps routine use <=190.
"""
import glob
import os
import sys
import time
from collections import Counter, defaultdict

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# host fragment -> (label, per_day, per_minute)
LIMITS = {
    "madeonsol": ("MadeOnSol", 200, None),
    "geckoterminal": ("GeckoTerminal", None, 8),   # published 30/min, but measured Oct 13: only ~6 calls/min succeed from Ali's IP
    "dexscreener": ("DexScreener", None, 300),
    "gopluslabs": ("GoPlus", None, 30),
    "birdeye": ("Birdeye", None, 60),
    "fomo": ("Fomo", None, None),
    "stonk": ("StonkFun", None, None),
    "pumpportal": ("PumpPortal", None, None),
    "jup.ag": ("Jupiter", None, 60),
    "helius": ("Helius RPC", None, None),
    "solana": ("Solana RPC", None, None),
}


def label_for(host: str) -> str:
    h = (host or "").lower()
    for frag, (name, _d, _m) in LIMITS.items():
        if frag in h:
            return name
    return h or "unknown"


def _limits(name: str):
    for frag, (n, d, m) in LIMITS.items():
        if n == name:
            return d, m
    return None, None


def record(url: str, status) -> None:
    """Append one call to today's log. Never raises."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc or "unknown"
        os.makedirs(LOG_DIR, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with open(os.path.join(LOG_DIR, f"api_calls_{day}.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.time():.0f} {host} {status}\n")
    except Exception:  # noqa: BLE001
        pass


def _read(day: str):
    rows = []
    try:
        with open(os.path.join(LOG_DIR, f"api_calls_{day}.log"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.split()
                if len(p) == 3 and p[0].isdigit():
                    rows.append((int(p[0]), p[1], p[2]))
    except OSError:
        pass
    return rows


def report(day: str = None) -> list:
    day = day or time.strftime("%Y-%m-%d", time.gmtime())
    rows = _read(day)
    calls, errs, per_min = Counter(), Counter(), defaultdict(Counter)
    for ts, host, status in rows:
        n = label_for(host)
        calls[n] += 1
        if status == "429":
            errs[n] += 1
        per_min[n][ts // 60] += 1
    days_logged = len(glob.glob(os.path.join(LOG_DIR, "api_calls_*.log"))) or 1
    out = []
    now = time.time()
    frac = max((now % 86400) / 86400.0, 0.02) if day == time.strftime("%Y-%m-%d", time.gmtime()) else 1.0
    for n, c in calls.most_common():
        d_lim, m_lim = _limits(n)
        peak = max(per_min[n].values()) if per_min[n] else 0
        proj_day = c / frac
        flag = "OK"
        if d_lim and proj_day > 0.95 * d_lim:
            flag = "OVER PACE"
        elif m_lim and peak > m_lim:
            flag = "BURST > per-minute limit"
        if errs[n]:
            flag += f" ({errs[n]} x HTTP 429)"
        out.append({"api": n, "calls_today": c, "projected_day": round(proj_day), "per_day_limit": d_lim,
                    "busiest_minute": peak, "per_minute_limit": m_lim, "month_projection": round(proj_day * 30),
                    "month_allowance": d_lim * 30 if d_lim else None, "http_429": errs[n], "flag": flag})
    return out


def main():
    rep = report(sys.argv[1] if len(sys.argv) > 1 else None)
    if not rep:
        print("No API calls logged yet (the log starts when the runner restarts on this version).")
        return
    print(f"{'API':14s} {'today':>7s} {'proj/day':>9s} {'day cap':>8s} {'peak/min':>9s} {'min cap':>8s} {'month proj':>11s} {'allow':>8s}  status")
    for r in rep:
        print(f"{r['api']:14s} {r['calls_today']:7d} {r['projected_day']:9d} {str(r['per_day_limit'] or '-'):>8s} "
              f"{r['busiest_minute']:9d} {str(r['per_minute_limit'] or '-'):>8s} {r['month_projection']:11d} "
              f"{str(r['month_allowance'] or '-'):>8s}  {r['flag']}")


if __name__ == "__main__":
    main()


# Shared per-minute pacing (Oct 13 2026: GeckoTerminal returned 222 HTTP 429 in one hour -- several processes
# share its 30 calls/min). The call log is shared by every process, so it doubles as the limiter's memory.
GATE_FRACTION = 0.8


def _recent_calls(frag: str, window: float = 60.0) -> int:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    path = os.path.join(LOG_DIR, f"api_calls_{day}.log")
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    cut = time.time() - window
    n = 0
    for ln in lines:
        p = ln.split()
        if len(p) == 3 and p[0].isdigit() and int(p[0]) >= cut and frag in p[1].lower():
            n += 1
    return n


# 8 Nov 2026 (Ali, full-system audit): gate() used to just READ _recent_calls() (the real call
# log) with no locking at all before deciding to proceed -- real evidence from logs/api_calls_
# 2026-11-08.log: up to 8 GeckoTerminal 429s landing in the EXACT SAME SECOND, repeatedly, and
# 2,893 429s out of 5,069 calls that day (57% wasted). Root cause: layer16_catalyst_radar.py's
# poll-radar cycle (launched by local_runner.py's run_job() as a SEPARATE "scheduler.py
# --poll-radar" subprocess, confirmed -- not just a thread) fires all 3 of its chains'
# GeckoTerminal calls via a ThreadPoolExecutor truly simultaneously, on top of
# multichain_sniper.py's own loop running continuously in local_runner.py's own process.
# Multiple callers (different threads, different processes) can all read "5 calls in the last
# 60s, cap is 6" at the exact same instant, all see themselves as under the cap, and all fire
# together -- a classic check-then-act race, invisible to any single caller, and _recent_calls()
# alone can never fix it no matter how it's tuned, because reading and deciding happen as two
# separate unprotected steps.
#
# FIRST FIX ATTEMPT (same day, reverted before commit): added a second "reservation" counter,
# summed with _recent_calls() under the lock. Caught in isolated testing before shipping: once a
# reservation's matching real call lands in api_calls_<date>.log a moment later, BOTH the
# reservation and the real entry count it -- same event counted twice, which doesn't fix the
# race, it just quietly halves the real admitted rate (cap=6 behaved like cap=3). Reverted
# rather than ship a system that throttles itself harder than intended for an unclear reason.
#
# ACTUAL FIX: a single rolling-window counter file (GATE_LOG), written only under the OS-level
# lock below, that the lock check reads directly -- no second counter, no reservation to
# reconcile against a later real entry, so nothing can ever be counted twice. Admission is
# GATE_LOG's own line count in the last 60s, full stop; every admitted gate() call appends
# itself to GATE_LOG right there under the lock before returning (not after the HTTP call
# completes, which is the whole point -- the next caller must see this one's slot immediately,
# not a second or more later once utils/http.py's own record() call finally lands). Deliberately
# NOT api_calls_<date>.log -- that file is report()'s only data source (calls_today,
# projected_day, busiest_minute, HTTP 429 counts for every API), so writing gate admissions into
# it would corrupt every budget number this module reports with a second, synthetic entry per
# real call. The lock itself is its own dedicated file (NOT state.py's trading-state lock --
# that one must never be held for the seconds an HTTP call can take, or every process's position
# management would stall behind a slow API response), held only for this brief check-and-append
# moment, never around the actual network call -- concurrent API traffic across processes still
# happens in parallel; only the admission decision is race-free now. Same proven technique
# state.py's atomic_multi_fn already uses for CLOSED_KEY (same race class: a real, measured
# cross-process double-close bug, fixed by computing under the lock instead of
# read-then-decide-then-write across separate unlocked steps).
#
# Verified in isolated testing (8 Nov 2026) before being wired in: sequential calls admit
# exactly cap-per-window, no double counting; 6 real concurrent OS subprocesses launched at once
# admit no more than cap, with the rest correctly waiting/fail-opening past max_wait -- same
# fail-open posture as every other gate in this codebase (a lock that can't be acquired, or a
# cap that never clears within max_wait, lets the call through rather than hang a poll cycle
# forever).
GATE_LOG = os.path.join(LOG_DIR, "api_gate_admits.log")


def _with_gate_lock(timeout: float = 5.0):
    """Returns an open, OS-locked file handle (caller must close it via _release_gate_lock) or
    None if the lock couldn't be acquired within timeout -- callers proceed unlocked rather than
    ever hang a poll cycle, same fail-open posture as state.py's own _local_file_lock."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = open(GATE_LOG + ".lock", "a+")
    except OSError:
        return None
    deadline = time.time() + timeout
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.time() > deadline:
                try:
                    fh.close()
                except Exception:  # noqa: BLE001
                    pass
                return None
            time.sleep(0.02)


def _release_gate_lock(fh) -> None:
    if fh is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except Exception:  # noqa: BLE001
        pass


def _gate_admit_count(frag: str, window: float = 60.0) -> int:
    cut = time.time() - window
    try:
        with open(GATE_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    n = 0
    for ln in lines:
        p = ln.split()
        if len(p) == 2 and p[1] == frag and p[0].replace(".", "", 1).isdigit() and float(p[0]) >= cut:
            n += 1
    return n


def _gate_admit(frag: str) -> None:
    try:
        with open(GATE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {frag}\n")
    except OSError:
        pass


def gate(url: str, max_wait: float = 25.0) -> None:
    """Block (up to max_wait s) while this API is at GATE_FRACTION of its per-minute limit. Never
    raises. See the long comment above this function for the race this closes and how."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).netloc or "").lower()
        for frag, (_name, _d, per_min) in LIMITS.items():
            if per_min and frag in host:
                cap = max(1, int(per_min * GATE_FRACTION))
                waited = 0.0
                while waited < max_wait:
                    fh = _with_gate_lock()
                    try:
                        # max(), not sum(): _recent_calls() is the real completed-call log (also
                        # catches saturation from calls made before this process started, or by
                        # any caller that reached record() without going through gate() first);
                        # _gate_admit_count() is this function's own just-granted admissions,
                        # which haven't necessarily landed in the real log yet. The SAME event
                        # can show up in both once a real call completes shortly after being
                        # admitted here -- summing them double-counts it (the bug caught and
                        # reverted earlier today); taking the max never does, since whichever
                        # count is higher already reflects every call either log could know about.
                        if max(_recent_calls(frag), _gate_admit_count(frag)) < cap:
                            _gate_admit(frag)
                            return
                    finally:
                        _release_gate_lock(fh)
                    time.sleep(2.0)
                    waited += 2.0
                return
    except Exception:  # noqa: BLE001
        pass

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
    "geckoterminal": ("GeckoTerminal", None, 30),
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

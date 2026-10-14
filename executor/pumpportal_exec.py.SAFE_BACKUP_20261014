"""Real execution through PumpPortal's Lightning API (S1c sniper, Oct 12 2026).

Trades from the wallet that belongs to PUMPPORTAL_API_KEY (fund THAT wallet with a
small amount; ~1% venue fee per trade). Everything is gated:
  EXECUTION_ENABLED=true  AND  PUMPPORTAL_API_KEY set  AND  lane listed in
  EARLY_LIVE_LANES (comma list, default empty = no live trades)  AND  kill switch off
  AND risk limits (LIVE_MAX_STAKE_USD, LIVE_MAX_OPEN, LIVE_DAILY_LOSS_USD).
Sells are never blocked by the kill switch (you must always be able to exit).
Untested against the live API from the build environment: do a $1 test first.
"""
import os
import time

import state

KILL_FILE = "KILL_SWITCH"
API = "https://pumpportal.fun/api/trade"


def _env_f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def kill_switch_on() -> bool:
    if os.path.exists(KILL_FILE):
        return True
    try:
        return bool(state.get_value("kill_switch"))
    except Exception:  # noqa: BLE001
        return False


def lane_live(lane: str) -> bool:
    lanes = {x.strip() for x in os.environ.get("EARLY_LIVE_LANES", "").split(",") if x.strip()}
    return (os.environ.get("EXECUTION_ENABLED", "").strip().lower() == "true"
            and bool(os.environ.get("PUMPPORTAL_API_KEY", "").strip()) and lane in lanes)


def _live_rows():
    from executor import paper_ledger
    open_ = [p for p in paper_ledger._open().values() if p.get("live")]
    day = time.strftime("%Y-%m-%d", time.gmtime())
    closed = [r for r in paper_ledger._closed() if r.get("live")
              and time.strftime("%Y-%m-%d", time.gmtime(r.get("closed_ts") or 0)) == day]
    return open_, closed


def allowed(lane: str, usd: float):
    """(ok, reason, stake_usd) -- stake is capped by LIVE_MAX_STAKE_USD."""
    if not lane_live(lane):
        return False, "lane not live", 0.0
    if kill_switch_on():
        return False, "KILL SWITCH on", 0.0
    open_, closed = _live_rows()
    if len(open_) >= int(_env_f("LIVE_MAX_OPEN", 3)):
        return False, "max live positions open", 0.0
    lost = -sum(min(0.0, float(r.get("pnl_usd") or 0)) for r in closed)
    if lost >= _env_f("LIVE_DAILY_LOSS_USD", 10):
        return False, f"daily loss cap reached (${lost:.2f})", 0.0
    return True, "ok", min(float(usd), _env_f("LIVE_MAX_STAKE_USD", 5))


def _post(data: dict) -> dict:
    import requests
    key = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
    r = requests.post(API, params={"api-key": key}, data=data, timeout=15)
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text[:200]}
    ok = r.status_code == 200 and not (isinstance(j, dict) and j.get("errors"))
    return {"ok": ok, "status": r.status_code, "resp": j}


def buy(mint: str, usd: float, sol_usd: float) -> dict:
    sol = round(usd / max(sol_usd, 1e-6), 4)
    return _post({"action": "buy", "mint": mint, "amount": sol, "denominatedInSol": "true",
                  "slippage": int(_env_f("LIVE_SLIPPAGE_PCT", 25)),
                  "priorityFee": _env_f("LIVE_PRIORITY_FEE_SOL", 0.0005), "pool": "pump"})


def sell(mint: str, pct: float) -> dict:
    if not os.environ.get("PUMPPORTAL_API_KEY", "").strip():
        return {"ok": False, "reason": "no key"}
    return _post({"action": "sell", "mint": mint, "amount": f"{max(1, min(100, round(pct)))}%",
                  "denominatedInSol": "false", "slippage": int(_env_f("LIVE_SLIPPAGE_PCT", 25)),
                  "priorityFee": _env_f("LIVE_PRIORITY_FEE_SOL", 0.0005), "pool": "pump"})

"""
Layer 1 -- Deployer-reputation alerts.

Fires on elite/good-tier wallet deploys via MadeOnSol's Deployer Hunter.
Covers both Solana (/deployer-hunter/alerts) and Robinhood Chain
(/rhc/deployer-hunter/alerts) -- confirmed as parallel endpoint families in
MadeOnSol's docs.

Fully blocked until MADEONSOL_API_KEY (Part A item 1) is set -- there is no
free/keyless way to reach this data, and no meaningful "replay" test either,
since it's a live alert stream (see tests/test_layer1_deployer.py for what a
fixture-based parse test *can* verify -- the JSON shape only, not that MadeOnSol
itself is reachable).
"""
from config import CONFIG
from utils.http import get_json, describe_fetch_failure
import state

ALERT_TIERS = {"elite", "good"}

# Must match poll-fast.yml's cron cadence (currently */10 minutes).
FAST_CYCLE_SECONDS = 600


def chain_for_cycle(now_ts: float, cycle_seconds: int = FAST_CYCLE_SECONDS) -> str:
    """Deterministically picks which chain Layer 1 checks on a given fast
    cycle, alternating Solana / Robinhood Chain by wall-clock time rather
    than a stored counter -- so it self-corrects across missed or late cron
    runs instead of drifting out of sync with the other chain.

    Why this exists (Ali's call-budget question, answered): MadeOnSol's
    Solana (`/deployer-hunter/alerts`) and RHC (`/rhc/deployer-hunter/alerts`)
    deployer-alert endpoints are confirmed separate/parallel endpoint
    families, not one combined multi-chain response -- so covering both
    chains costs 2 calls/cycle no matter what. And the deployer-reputation
    tier Layer 1 needs (`deployer_tier`, see parse_deployer_alerts) is
    already the ONLY field it reads off that same one-call response -- there
    is no separate reputation-tier lookup riding along that could be cut.
    So there's no dedup available here; checking each chain half as often is
    the only real lever, which is what this function implements."""
    bucket = int(now_ts // cycle_seconds)
    return "solana" if bucket % 2 == 0 else "robinhood_chain"


def fetch_deployer_alerts(chain: str = "solana", since: str = None) -> dict:
    if not CONFIG.madeonsol_api_key:
        return {"ok": False, "reason": "MADEONSOL_API_KEY not configured"}
    # Real daily-budget gate added Sept 25 2026 -- see state.py's
    # madeonsol_budget_remaining() docstring for why (a confirmed real
    # 200/day BASIC-tier cap, hit live today). Fails closed with a clear
    # reason rather than spending a call MadeOnSol would just reject anyway.
    if state.madeonsol_budget_remaining() < 1:
        return {"ok": False, "reason": "MadeOnSol daily call budget exhausted "
                                        f"({state.madeonsol_calls_today()}/{state.MADEONSOL_DAILY_BUDGET})"}
    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    # FIXED Sept 25, 2026 -- real bug found live-testing backtest.py's
    # sibling call: MadeOnSol's /deployer-hunter/alerts endpoint rejects a
    # comma-joined tier value ("tier=elite,good" -> 400 "Invalid query
    # parameters") -- it wants the tier param repeated once per value
    # ("tier=elite&tier=good"), confirmed against the real API. requests
    # encodes a list value that way automatically. This means Layer 1's
    # elite/good deployer alerts have almost certainly been silently
    # 400ing on every single poll-fast.yml cycle since this was built --
    # poll_layer1 just returns ok=False on any non-2xx, so this never
    # surfaced as a loud error, just permanently empty deployer alerts.
    params = {"tier": sorted(ALERT_TIERS)}
    if since:
        params["since"] = since
    result = get_json(f"{CONFIG.madeonsol_base_url}{prefix}/deployer-hunter/alerts",
                       headers=headers, params=params)
    state.record_madeonsol_calls(1)
    return {"ok": result["ok"], "raw": result}


def parse_deployer_alerts(payload: dict) -> list:
    """payload is the JSON body of a /deployer-hunter/alerts response.
    Returns a list of normalized alert dicts, filtered to elite/good tier."""
    alerts = payload.get("alerts", []) if payload else []
    out = []
    for a in alerts:
        tier = a.get("deployer_tier")
        if tier not in ALERT_TIERS:
            continue
        out.append({
            "token_address": a.get("token_mint") or a.get("token_address"),
            "deployer_tier": tier,
            "deployer_balance": a.get("deployer_sol_balance") or a.get("deployer_balance"),
            "alert_type": a.get("alert_type"),
            "created_at": a.get("created_at"),
        })
    return out


def poll_layer1(chain: str = "solana", since: str = None) -> dict:
    fetched = fetch_deployer_alerts(chain, since)
    if not fetched["ok"]:
        return {"ok": False, "reason": describe_fetch_failure(fetched), "alerts": []}
    body = fetched["raw"].get("json")
    if body is None:
        return {"ok": False, "reason": f"non-JSON response, status {fetched['raw']['status_code']}", "alerts": []}
    return {"ok": True, "alerts": parse_deployer_alerts(body)}

"""
Free, no-signup RPC pool with automatic failover, across ALL THREE chains
this system trades on (Solana, BSC, Robinhood Chain) -- the real answer to
"what happens when the free public RPC rate-limits us," without adding any
paid RPC provider account (hard constraint, Ali, unchanged since Sept 22,
2026).

Problem this solves: a single public RPC endpoint is free but has no SLA
and rate-limits under real load -- that pain is real and has hit other
streams before (see S1e-Futures' CL/6E still blocked on a Yahoo Finance
network restriction -- same class of problem, different provider). Rather
than trading that risk away with a paid account, this rotates across
MULTIPLE independent free, no-signup endpoints PER CHAIN and fails over
automatically -- if one is rate-limited or down, the call just tries the
next one in that chain's pool. Worst case (all endpoints for a chain down
at once) is returned as {"ok": False, ...} for the caller to skip this
attempt and retry next cycle -- this module never lets an RPC problem
become an uncaught exception that could halt the scheduler.

SOLANA -- FINAL, live-tested by Ali on his own machine, Sept 23 2026,
across THREE rounds (the only real test that matters, since this build
environment can't reach these hosts at all):
  1. https://api.mainnet-beta.solana.com      -- CONFIRMED, healthy all 3 rounds
  2. https://solana-rpc.publicnode.com        -- CONFIRMED, healthy all 3 rounds
  3. https://solana.leorpc.com/?api_key=FREE  -- CONFIRMED, round 3 (LeoRPC's
     published free-tier trick -- the literal string "FREE" as the key
     value opens their community rate limit, no real signup)
5 Solana candidates were tried and rejected across rounds 1-3 -- real
signal that free, no-signup Solana RPC access is actively shrinking as
providers lock things behind keys:
  - https://rpc.ankr.com/solana -- now requires an API key (-32052)
  - https://solana.public-rpc.com -- broken SSL cert (self-signed)
  - https://solana.blockpi.network/v1/rpc/public -- now requires an API key
  - https://endpoints.omniatech.io/v1/sol/mainnet/public -- HTTP 521, origin down
  - https://solana.api.onfinality.io/public -- HTTP 429 on the very first call
3 endpoints, each independently confirmed, is where Solana's pool stops.
Chasing a 4th past this point is diminishing returns.

BSC -- CONFIRMED, all 3 endpoints, live-tested by Ali, round 4, Sept 23
2026 (Ali, Sept 23 2026: "point 6 should cover all 3 chains" -- done):
  - https://bsc-dataseed.binance.org       -- CONFIRMED healthy, round 4 (BNB Chain's own official endpoint, was already the single default here before this pool existed)
  - https://bsc-rpc.publicnode.com         -- CONFIRMED healthy, round 4 (Allnodes -- same provider as Solana's confirmed #2)
  - https://bsc-dataseed1.defibit.io       -- CONFIRMED healthy, round 4 (BNB Chain's other official dataseed mirror)
All 3 healthy on the first live test -- BSC's pool is as solid as Solana's.

ROBINHOOD CHAIN -- CONFIRMED, its one candidate, live-tested by Ali, round
4, Sept 23 2026:
  - https://rpc.mainnet.chain.robinhood.com -- CONFIRMED healthy, round 4.
    Robinhood's own stated public endpoint for chain ID 4663. Their own
    docs say it "is rate-limited and not recommended for production use"
    and point to a commercial provider (Alchemy) for production instead --
    that caveat still stands even though the endpoint answered cleanly
    here: a single successful call doesn't test their rate limit under
    real repeated load. Still the only free candidate, so it's what this
    pool uses -- if it starts failing under real polling frequency, that's
    the moment to revisit paid vs. a second candidate, not before.

This trades a little latency (a failed/cooling-down endpoint costs one
skipped hop, or one timeout the first time it's seen failing) for real
redundancy. It does NOT trade away the no-account constraint -- every URL
above needs no signup, no key, nothing added to the deploy checklist
(Robinhood Chain's caveat above is a rate-limit note, not a signup
requirement).

Not yet wired into swap_executor.py -- that module's sign+send steps are
themselves still unbuilt/untested for all three chains (see its own
docstring). This is prep work for that build: when execute_buy_solana /
execute_buy_bsc / execute_buy_robinhood_chain's sign+send steps are
written, each should call rpc_call(<chain>, ...) here instead of hitting a
single hardcoded endpoint directly, so the RPC-reliability problem is
solved for every chain at the same time as the buy paths themselves, not
bolted on afterward chain by chain.
"""
import time
from typing import Optional

from utils.http import post_json
import state

RPC_ENDPOINT_POOLS = {
    "solana": [
        "https://api.mainnet-beta.solana.com",       # CONFIRMED healthy, Ali, Sept 23 2026 (3 rounds)
        "https://solana-rpc.publicnode.com",          # CONFIRMED healthy, Ali, Sept 23 2026 (3 rounds)
        "https://solana.leorpc.com/?api_key=FREE",    # CONFIRMED healthy, Ali, Sept 23 2026 (round 3)
    ],
    "bsc": [
        "https://bsc-dataseed.binance.org",   # CONFIRMED healthy, Ali, Sept 23 2026 (round 4)
        "https://bsc-rpc.publicnode.com",     # CONFIRMED healthy, Ali, Sept 23 2026 (round 4)
        "https://bsc-dataseed1.defibit.io",   # CONFIRMED healthy, Ali, Sept 23 2026 (round 4)
    ],
    "robinhood_chain": [
        "https://rpc.mainnet.chain.robinhood.com",  # CONFIRMED healthy, Ali, Sept 23 2026 (round 4); official but rate-limited per Robinhood's own docs -- only 1 candidate exists
    ],
}

# How long (seconds) to skip an endpoint after it fails, so a dead or
# rate-limited endpoint doesn't eat a fresh timeout on every single call
# until it recovers on its own. Short on purpose -- free public endpoints'
# rate limits are usually per-minute windows, not outages.
_COOLDOWN_SECONDS = 60


def _cooldown_key(chain: str, url: str) -> str:
    return f"rpc_pool_cooldown:{chain}:{url}"


def _is_cooling_down(chain: str, url: str) -> bool:
    until = state.get_value(_cooldown_key(chain, url))
    return bool(until and time.time() < until)


def _mark_failed(chain: str, url: str):
    state.set_value(_cooldown_key(chain, url), time.time() + _COOLDOWN_SECONDS)


def _looks_rate_limited(status_code: Optional[int], err: dict) -> bool:
    if status_code == 429:
        return True
    message = str(err.get("message", "")).lower()
    return "rate" in message or "limit" in message or "429" in message


def rpc_call(chain: str, method: str, params: list, timeout: int = 15) -> dict:
    """Tries every non-cooling-down endpoint in RPC_ENDPOINT_POOLS[chain],
    in order, until one returns a usable JSON-RPC response.

    Returns {"ok": True, "result": ..., "endpoint_used": <url>} on success,
    or {"ok": False, "reason": ..., "tried": [...]} if the pool for this
    chain is exhausted, empty, or every endpoint returned a real
    (non-rate-limit) RPC error. Callers MUST treat a False result as "skip
    this attempt, try again next cycle" -- this function deliberately never
    raises for a reachable-but-failing endpoint, only lets genuine network
    exceptions from utils.http bubble up the same way every other layer in
    this repo already handles them.
    """
    pool = RPC_ENDPOINT_POOLS.get(chain, [])
    if not pool:
        return {"ok": False, "reason": f"no RPC endpoints configured for chain '{chain}'", "tried": []}

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    tried = []
    endpoints = [u for u in pool if not _is_cooling_down(chain, u)]
    if not endpoints:
        # whole pool is cooling down at once -- try anyway rather than give
        # up outright, since a cooldown is a guess, not a guarantee
        endpoints = list(pool)
    for url in endpoints:
        resp = post_json(url, json=payload, timeout=timeout)
        tried.append({"url": url, "status_code": resp.get("status_code")})
        if not resp.get("ok"):
            _mark_failed(chain, url)
            continue
        body = resp.get("json")
        if body is None:
            _mark_failed(chain, url)
            continue
        if "error" in body:
            err = body["error"]
            if _looks_rate_limited(resp.get("status_code"), err):
                _mark_failed(chain, url)
                continue
            # a real RPC error (bad method, bad params) -- every endpoint in
            # the pool would return the same thing, so don't burn through
            # the rest of the pool for nothing
            return {"ok": False, "reason": f"rpc error from {url}: {err}", "tried": tried}
        return {"ok": True, "result": body.get("result"), "endpoint_used": url}
    return {"ok": False, "reason": f"all pool endpoints for '{chain}' failed or rate-limited", "tried": tried}

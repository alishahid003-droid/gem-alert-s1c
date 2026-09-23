"""
Execution config -- separate from config.py's CONFIG on purpose. Everything
here governs REAL CAPITAL moving, so it has its own explicit master switch
and defaults that fail safe (off) rather than inheriting the alert system's
"degrade gracefully" posture.

EXECUTION_ENABLED must be explicitly set to the string "true" in the
environment. Its absence, or any other value, keeps the executor fully
inert -- swap_executor.py checks this before doing anything, not just here.

No RPC provider account needed anywhere in this file, per Ali's explicit
direction (Sept 22, 2026): Solana tx submission goes through Jupiter's swap
API + a pool of free, no-signup public Solana RPC endpoints (see
executor/rpc_pool.py -- 3 endpoints, each individually live-tested by Ali
on his own machine across 3 rounds, Sept 23 2026: api.mainnet-beta.
solana.com, solana-rpc.publicnode.com, and solana.leorpc.com/?api_key=FREE.
5 other candidates were tried and rejected -- see rpc_pool.py for exactly
which and why). EVM chains (BSC, Robinhood Chain) go through web3.py
against each chain's own public RPC. Free, no dedicated provider, nothing
added to the deploy checklist.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name, default)
    if val is not None:
        val = val.strip()
        if val == "":
            val = None
    return val


def _env_float(name: str, default: float) -> float:
    val = _env(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    val = _env(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# Bankroll-scaling benchmarks (Ali, Sept 23, 2026: "set benchmarks for this
# system of how we should utilize the funds if... our account grows"). The
# real target discussed -- $50-100 seed to $125k in a month -- is only
# reachable by catching a genuine outlier (LEVERCAT peaked 2,744x same-day;
# APECAT peaked 220x same-day, both confirmed against real StonkFun data
# Sept 22-23, 2026) with real capital committed when it happens. No amount
# of disciplined 2x-10x trading closes that gap on its own -- a same-day
# simulation against real signals landed around $50 -> ~$173 on a GOOD day.
# These tiers exist to serve one goal: survive long enough, sized right
# enough, to be in a real outlier when one shows up -- not to grind there.
#
# (tier name, min wallet $ [inclusive], max wallet $ [exclusive, None = no
#  cap], position size as % of current wallet, max concurrent open
#  positions, minimum moonbag.compute_moonshot_score needed to fire --
#  0 = no extra gate, matches moonbag.CONVICTION_THRESHOLD at the top tier
#  so late-stage capital only goes into signals strong enough for the wide
#  moonbag, not noise)
BANKROLL_TIERS = [
    # seed bumped 0.20 -> 0.30, Ali, Sept 23 2026: explicitly wants bigger
    # per-trade swings while capital is small and there's little to
    # protect yet -- $10 on $50 felt too small to "aim and attempt big";
    # $15 on $50 is the deliberate choice. Max concurrent stays at 3, so
    # this also raises how much of the $50 can be committed at once
    # (3 * $15 = $45 vs the old 3 * $10 = $30) -- a real, known tradeoff:
    # fewer simultaneous shots survive a bad stretch before the tier caps
    # exposure, in exchange for each shot mattering more if it's the one.
    ("seed",     0,       200,     0.30, 3,  0),
    ("growth",   200,     1_000,   0.12, 5,  0),
    ("scale",    1_000,   5_000,   0.07, 8,  0),
    ("compound", 5_000,   25_000,  0.04, 10, 0),
    ("preserve", 25_000,  None,    0.02, 10, 4),
]


def bankroll_tier(total_wallet_usd: float) -> dict:
    """Which benchmark tier a given wallet size falls into right now, and
    what that tier says position sizing/concurrency/conviction-gating
    should be. Pure function, no state -- call it fresh whenever
    total_wallet_usd changes (e.g. after a real balance check), don't cache
    across a session where the balance could have moved."""
    for name, lo, hi, pct, concurrent, min_conviction in BANKROLL_TIERS:
        if total_wallet_usd >= lo and (hi is None or total_wallet_usd < hi):
            return {
                "tier": name, "position_pct": pct, "max_concurrent": concurrent,
                "min_conviction_score": min_conviction, "range": (lo, hi),
            }
    # Below $0 shouldn't happen, but fail into the most conservative shape
    # rather than crash.
    return {"tier": "seed", "position_pct": 0.30, "max_concurrent": 3,
            "min_conviction_score": 0, "range": (0, 200)}


@dataclass
class ExecutorConfig:
    # --- master switch -- must be the literal string "true" ---
    execution_enabled: bool = field(default_factory=lambda: _env("EXECUTION_ENABLED") == "true")

    # --- hot wallet keys -- a DEDICATED trading wallet, never Ali's main
    # holdings. Never logged, never included in any alert text. ---
    solana_private_key: Optional[str] = field(default_factory=lambda: _env("EXECUTION_SOLANA_PRIVATE_KEY"))
    bsc_private_key: Optional[str] = field(default_factory=lambda: _env("EXECUTION_BSC_PRIVATE_KEY"))
    rhc_private_key: Optional[str] = field(default_factory=lambda: _env("EXECUTION_RHC_PRIVATE_KEY"))

    # --- public RPC endpoints, no account needed for any of these ---
    # solana_rpc_url / bsc_rpc_url / rhc_rpc_url are SINGLE-endpoint manual
    # override/fallback fields only -- real RPC calls for ALL THREE chains
    # should go through executor.rpc_pool.rpc_call(chain, method, params),
    # which fails over across each chain's pool (see rpc_pool.py's
    # RPC_ENDPOINT_POOLS + module docstring). ALL THREE chains' pools are
    # now CONFIRMED live by Ali: Solana's 3 endpoints across 3 rounds, and
    # BSC's 3 + Robinhood Chain's 1 in round 4, all Sept 23 2026 (Robinhood
    # Chain's candidate is still flagged rate-limited per Robinhood's own
    # docs, even though it answered cleanly on this one test call -- see
    # rpc_pool.py's docstring).
    # These fields stay as explicit-override escape hatches (e.g. pointing
    # at a local test validator) -- the matching env var still wins if
    # set, same override philosophy as everything else in this file.
    solana_rpc_url: str = field(default_factory=lambda: _env(
        "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    bsc_rpc_url: str = field(default_factory=lambda: _env(
        "BSC_RPC_URL", "https://bsc-dataseed.binance.org"))
    rhc_rpc_url: str = field(default_factory=lambda: _env("RHC_RPC_URL"))  # no public default confirmed yet -- see rpc_pool.py's robinhood_chain pool (1 untested candidate)

    # --- position sizing, per Ali's Sept 22 split (example $100 wallet) ---
    stage1_position_usd: float = field(default_factory=lambda: _env_float("STAGE1_POSITION_USD", 20.0))  # bumped from $12 to $20, Ali, Sept 22 2026
    stage2_position_usd: float = field(default_factory=lambda: _env_float("STAGE2_POSITION_USD", 17.0))
    stage1_budget_pct: float = field(default_factory=lambda: _env_float("STAGE1_BUDGET_PCT", 0.60))
    stage2_budget_pct: float = field(default_factory=lambda: _env_float("STAGE2_BUDGET_PCT", 0.40))

    # --- Stage 2 confirmation gate ---
    stage2_mcap_multiplier: float = field(default_factory=lambda: _env_float("STAGE2_MCAP_MULTIPLIER", 2.0))
    stage2_mcap_floor_usd: float = field(default_factory=lambda: _env_float("STAGE2_MCAP_FLOOR_USD", 150000.0))

    # --- circuit breaker ---
    max_consecutive_losses: int = field(default_factory=lambda: _env_int("MAX_CONSECUTIVE_LOSSES", 3))
    max_session_loss_pct: float = field(default_factory=lambda: _env_float("MAX_SESSION_LOSS_PCT", 0.30))

    # --- moonbag trimming -- the "let one gem run to hundreds of millions"
    # piece (Ali, Sept 22, 2026): de-risk a winning position in stages as it
    # multiplies, while leaving an untouched remainder to ride uncapped for
    # the outlier outcome, instead of an all-or-nothing manual exit. Default
    # on; the ladder itself lives in executor/moonbag.py next to the other
    # trigger constants (same pattern as triggers.py's venue maps). ---
    moonbag_enabled: bool = field(default_factory=lambda: _env("MOONBAG_ENABLED", "true") == "true")

    # --- total automation wallet size, used to translate budget_pct above
    # into a dollar cap. Set this to whatever's actually funded into the
    # dedicated hot wallet -- NOT Ali's total holdings. ---
    total_wallet_usd: float = field(default_factory=lambda: _env_float("TOTAL_WALLET_USD", 50.0))  # real starting capital, Ali, Sept 23 2026 (was a $100 placeholder)

    def stage1_budget_usd(self) -> float:
        return self.total_wallet_usd * self.stage1_budget_pct

    def stage2_budget_usd(self) -> float:
        return self.total_wallet_usd * self.stage2_budget_pct

    def __post_init__(self):
        """Applies the bankroll tier's position sizing UNLESS STAGE1_
        POSITION_USD/STAGE2_POSITION_USD were explicitly set in the
        environment -- an explicit override always wins, same as every
        other field here. Stage2 keeps the same ~0.85 ratio to Stage 1 it
        always has. With the real $50 default and the 30% seed-tier rate
        (Ali, Sept 23 2026), this computes to stage1=$15.00, stage2=$12.75
        out of the box, with no env vars set at all."""
        self.bankroll_tier_info = bankroll_tier(self.total_wallet_usd)
        if os.environ.get("STAGE1_POSITION_USD") is None:
            self.stage1_position_usd = round(self.total_wallet_usd * self.bankroll_tier_info["position_pct"], 2)
        if os.environ.get("STAGE2_POSITION_USD") is None:
            self.stage2_position_usd = round(self.stage1_position_usd * 0.85, 2)

    def ready_for_chain(self, chain: str) -> bool:
        if not self.execution_enabled:
            return False
        if chain == "solana":
            return bool(self.solana_private_key)
        if chain == "bsc":
            return bool(self.bsc_private_key)
        if chain == "robinhood_chain":
            return bool(self.rhc_private_key) and bool(self.rhc_rpc_url)
        return False


EXECUTOR_CONFIG = ExecutorConfig()

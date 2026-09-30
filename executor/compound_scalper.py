"""
Compound scalper -- Ali's explicit ask (Sept 29 2026): "a fast, aggressive
attempt towards a target... continuously entering on coins... getting 2-3x,
getting out, getting another coin... compounding... on a small capital,
$25-30... tens to hundreds of transactions... 24 to 48 hours."

This is a SEPARATE, ISOLATED capital pool and position-management mode --
it never touches Stage 1/Stage 2's budget, wallet, or position_state.py's
own position namespace (see COMPOUND_STATE_KEY below, a dedicated state
key). It reuses the exact same entry/exit primitives everything else here
does: layers/layer0_scoring for the entry signal, executor.swap_executor
for the real buy/sell -- which is still UNTESTED/inert until
EXECUTION_ENABLED="true" plus a real wallet key are both set (see that
module's own docstring). Nothing here creates a new way to move money; it's
a new decision layer over the same swap plumbing, same EXECUTION_ENABLED /
Section-4 boundary as everywhere else in this repo. No key is read, typed,
or stored here -- swap_executor.py owns that, unchanged.

HONESTY FLAG, stated plainly rather than buried: the entry filter this
module gates on (score band A/B) is only as good as layers/layer0_scoring's
real, MEASURED credibility -- 43.8% overall, 16.7% rug-filtering, as of the
Sept 29 2026 validate-scoring run made BEFORE BIRDEYE_API_KEY was added as
a GitHub secret (a re-validation with the key live is queued separately).
Running that same filter MORE OFTEN -- the whole point of this module --
does NOT make each individual entry more reliable; it just takes more
shots at the same underlying odds. This module's actual, honest job is
narrower than "find more moonshots faster": (1) never take a shot whose
estimated entry+exit round-trip cost eats the intended edge, (2) size each
shot as a real fraction of the CURRENT pool balance so a string of small
wins actually compounds instead of staying flat, and (3) get out fast, in
both directions (hit target OR stall OR reverse), rather than let a
position sit. It does not, and cannot, fix the underlying hit-rate -- only
layers/layer0_scoring (and a clean re-validation with BIRDEYE_API_KEY live)
can do that. Do not read a good run of this module as proof the filter
itself improved.

WHY SEQUENTIAL, NOT PARALLEL: with a $25-30 pool, positions opened
simultaneously divide an already-small stake into smaller slices, each one
proportionally MORE exposed to the same near-fixed per-trade slippage/fee
floor (see estimate_round_trip_cost_pct) -- fragmenting the pool makes the
friction problem worse, not better. One open position at a time, sized as
a real fraction of the pool, full exit before the next entry -- that is
the deliberate design choice for THIS pool. Stage 1/Stage 2's own
concurrent-position model is untouched and unaffected by any of this.

WIRING: executor/entrypoint.py's handle_compound_scalper_candidate() is the
single call a live poll loop makes on a freshly-scored candidate (mirrors
handle_stage1_candidate's shape). check_and_manage() below is the per-cycle
call for the pool's one open position (mirrors moonbag.check_and_trim /
defensive_sell.check_and_defend). Both are wired into scheduler.py's real
cycle functions (_handle_scored and _run_position_management_cycle) --
see those functions' own comments for exactly where.

STARTING THE POOL IS A DELIBERATE, MANUAL STEP -- NOT AUTOMATIC. Even with
COMPOUND_SCALPER_ENABLED="true", entry_gate() refuses every candidate until
init_pool() has been called at least once (mirrors EXECUTION_ENABLED's own
fail-safe-by-default posture). This is intentional: turning the mode "on"
in config and actually committing the first real dollar of a session are
two different decisions, and only the second one should need a second,
explicit action.
"""
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import state
import executor.swap_executor as swap_executor


# --- module-local env helpers (small, deliberate duplication of
# executor/config.py's _env/_env_float/_env_int rather than importing
# private helpers across modules -- same "module owns its own tunables"
# pattern moonbag.py and layer8_momentum_override.py already use for their
# own constants) ---
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


def sprint_mode() -> bool:
    """5-day sprint (Oct 1 2026, see executor/sprint.py): the compound
    scalper with sprint defaults, stricter entries, auto-start and
    auto-resume. Explicit COMPOUND_* env values still win."""
    return (os.environ.get("SPRINT_MODE", "") or "").strip().lower() == "true"


def _sd(normal, sprint):
    return sprint if sprint_mode() else normal


@dataclass
class CompoundScalperConfig:
    # Master switch -- same "must be the literal string true" fail-safe
    # pattern as EXECUTION_ENABLED itself. Off by default.
    enabled: bool = field(default_factory=lambda: _env("COMPOUND_SCALPER_ENABLED", "false") == "true" or sprint_mode())

    # Starting pool size -- Ali's own stated range, Sept 29 2026 ("$25,
    # $30"). Midpoint default, overridable.
    seed_usd: float = field(default_factory=lambda: _env_float("COMPOUND_SEED_USD", _sd(27.5, 100.0)))

    # Fraction of CURRENT pool balance committed to each single sequential
    # position. Deliberately aggressive per Ali's explicit ask ("aggressive
    # compounding"), not a conservative default -- the 1-risk_pct remainder
    # stays uncommitted as a buffer against this module's cost estimate
    # being wrong in practice, not as a second concurrent position.
    risk_pct: float = field(default_factory=lambda: _env_float("COMPOUND_RISK_PCT", _sd(0.75, 0.9)))

    # Take-profit target as a price multiple vs entry mcap. Set above a
    # flat 2.0x on purpose: after round-trip cost (see
    # estimate_round_trip_cost_pct -- commonly 5-10% on Solana, more on a
    # thin pool), a gross 2.0x nets meaningfully less than 2x; 2.5x leaves
    # real margin over that friction rather than just clearing it.
    # Sept 30 2026 retune from backtest_trades.py's scalper grid (72 simulated
    # trades on the labeled coins): stop 55% / take-profit 1.5x / 3 h time
    # stop / 35% trail = 71% wins, +$648 vs 32% wins, -$44 for the original
    # 30% / 2.5x / 20 min / 20% -- the tight stop and 20-min clock cut coins
    # before their move. Paper ledger confirms live (checklist 2.8).
    take_profit_multiple: float = field(default_factory=lambda: _env_float("COMPOUND_TAKE_PROFIT_MULTIPLE", 1.5))

    # Fraction of the ORIGINAL position sold at the take-profit trigger --
    # the rest keeps riding under the trailing stop below, so a token that
    # keeps running past the target isn't fully exited at the first tag.
    # Same "bank real cash, let a slice ride" shape as moonbag.py's ladder,
    # compressed to one rung since this pool's whole premise is fast
    # in/out, not a multi-rung hold.
    partial_tp_pct: float = field(default_factory=lambda: _env_float("COMPOUND_PARTIAL_TP_PCT", 0.60))

    # Once the take-profit partial has fired, the riding remainder exits
    # fully if price pulls back this fraction from its own peak multiple --
    # protects the post-target remainder from giving back the gain the
    # partial sale already locked in.
    trail_stop_pct: float = field(default_factory=lambda: _env_float("COMPOUND_TRAIL_STOP_PCT", 0.35))

    # Hard stop-loss, as a fraction BELOW entry mcap, before any take-profit
    # has fired -- cuts a loser fast rather than let it sit hoping for a
    # reversal. This is gross price movement, not net-of-cost -- the real
    # realized loss will run somewhat deeper than this once round-trip cost
    # is subtracted, which is the honest reason this stays relatively tight
    # (30%, not 50%+): waiting longer for a bigger stop just compounds the
    # cost problem on a loser.
    hard_stop_pct: float = field(default_factory=lambda: _env_float("COMPOUND_HARD_STOP_PCT", 0.55))

    # If neither the hard stop nor the take-profit has fired within this
    # many minutes, exit fully regardless -- the momentum thesis this
    # module trades on is a fast one; a position that's done neither after
    # this long is a stalled entry, not a slow-building one, and capital
    # sitting idle in it is capital not compounding. Tight on purpose, to
    # fit "tens to hundreds of transactions" inside a 24-48h window.
    time_stop_minutes: float = field(default_factory=lambda: _env_float("COMPOUND_TIME_STOP_MINUTES", 180.0))

    # Entry is refused if estimate_round_trip_cost_pct's HEURISTIC estimate
    # (see that function's own honesty flag) exceeds this -- a real gate
    # against feeding this pool's small size into a pool so thin that
    # slippage alone eats the position before any price move even
    # registers as a real gain.
    max_round_trip_cost_pct: float = field(default_factory=lambda: _env_float("COMPOUND_MAX_ROUND_TRIP_COST_PCT", 0.12))

    # Minimum structural score band admitted -- "B" means A or B fire,
    # C/D never do, regardless of any other signal. Matches triggers.py's
    # own score-band fire condition, not a new bar.
    min_score_band: str = field(default_factory=lambda: _env("COMPOUND_MIN_SCORE_BAND", "B"))

    # Circuit breaker -- own session-scoped counters, separate from
    # executor/circuit_breaker.py's Stage 1/2 breaker (different pool,
    # different budget, shouldn't trip on each other's losses).
    max_consecutive_losses: int = field(default_factory=lambda: _env_int("COMPOUND_MAX_CONSECUTIVE_LOSSES", 3))
    max_session_loss_pct: float = field(default_factory=lambda: _env_float("COMPOUND_MAX_SESSION_LOSS_PCT", _sd(0.50, 0.75)))

    # Absolute dollar floor -- stop entirely (not just "trip until Ali
    # resets it") once the pool is this small, since a pool this size can
    # no longer clear its own round-trip cost on any real position size.
    floor_usd: float = field(default_factory=lambda: _env_float("COMPOUND_FLOOR_USD", _sd(5.0, 20.0)))

    # Session window, hours -- Ali's own stated range ("24 to 48 hours").
    # Default to the wide end; narrower is one env var away.
    session_hours: float = field(default_factory=lambda: _env_float("COMPOUND_SESSION_HOURS", _sd(48.0, 120.0)))

    # Sanity ceiling only, not a target -- Ali said "tens to hundreds"; this
    # just stops a bug (or an unexpectedly fast market) from spinning the
    # pool through an unbounded number of trades in one session.
    max_trades_per_session: int = field(default_factory=lambda: _env_int("COMPOUND_MAX_TRADES_PER_SESSION", _sd(80, 400)))


SCALPER_CONFIG = CompoundScalperConfig()

BAND_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}

COMPOUND_STATE_KEY = "compound_scalper:pool"

# --- round-trip cost model -----------------------------------------------
# HEURISTIC, not measured against this repo's own real fill data -- no real
# trade has executed yet anywhere in this codebase (swap_executor.py is
# still UNTESTED against live networks, same flag it already carries).
# Flagged explicitly rather than presented as a measured figure. Used only
# to GATE entries (skip a trade whose estimated friction is too high);
# real realized cost, once trades actually happen, comes from the ACTUAL
# filled_usd difference recorded in check_and_manage below, not from this
# estimate.
CHAIN_SWAP_FEE_PCT = {
    "solana": 0.010,           # pump.fun bonding-curve / Raydium-Jupiter route fee, per leg
    "bsc": 0.0025,              # PancakeSwap v2/v3 typical per-leg fee
    "robinhood_chain": 0.003,   # Uniswap v4 typical per-leg fee -- RHC untested live, same as elsewhere
}
SOLANA_BUY_SLIPPAGE = 0.010    # matches swap_executor.py's Jupiter buy quote, slippageBps=100
SOLANA_SELL_SLIPPAGE = 0.015   # matches swap_executor.py's Jupiter sell quote, slippageBps=150
# BSC/RHC: swap_executor.py's *_SLIPPAGE_TOLERANCE constants are a wide
# CEILING meant to guarantee a thin-pool tx lands, not a typical realized
# cost (see that module's own comment). Using the full ceiling here as the
# cost estimate is deliberately conservative -- real BSC/RHC cost is very
# likely lower in practice, but with zero live fill data for either chain
# yet, this stays conservative rather than guessing a lower number down.
# Practical effect: BSC/RHC will rarely clear max_round_trip_cost_pct in
# this mode until real fill data replaces this estimate -- an honest
# consequence of no live data, not a bug to work around.
# Sept 30 2026: these were set to the swap TOLERANCE ceilings (20%/25%),
# which made every BSC/RHC round trip "cost" ~45% vs ~5% on Solana -- that
# skewed paper-ledger and backtest P&L and blocked every BSC scalp. Expected
# slippage is now 2x Solana's (thinner pools), still conservative; the
# on-chain tolerance ceilings in swap_executor.py are unchanged, and pool
# size is priced in separately by _liquidity_impact_penalty. Replace with
# measured values once real fills exist (the test trade logs real cost).
BSC_RHC_BUY_SLIPPAGE = 0.02
BSC_RHC_SELL_SLIPPAGE = 0.03


def _liquidity_impact_penalty(position_usd: float, liquidity_usd: Optional[float]) -> float:
    """Extra estimated price-impact cost for a position that's a real bite
    of the pool's own liquidity, on top of the flat slippage/fee
    assumptions above -- a $10 buy into a $500 pool moves price far more
    than the same $10 into a $50,000 pool. Pure heuristic ramp, same
    honesty flag as the rest of this cost model. Unknown liquidity gets a
    moderate assumed penalty rather than zero -- missing data should never
    look cheaper than known-thin data."""
    if not liquidity_usd or liquidity_usd <= 0:
        return 0.05
    ratio = position_usd / liquidity_usd
    if ratio <= 0.005:
        return 0.0
    if ratio <= 0.02:
        return 0.02
    if ratio <= 0.05:
        return 0.06
    return 0.15


def estimate_round_trip_cost_pct(chain: str, position_usd: float, liquidity_usd: Optional[float] = None) -> float:
    """Estimated total fraction of position value lost to buy+sell
    slippage, DEX/bonding-curve fees, and price impact. See module-level
    comment above this function for the honesty flag on this whole model."""
    if chain == "solana":
        base = SOLANA_BUY_SLIPPAGE + SOLANA_SELL_SLIPPAGE + 2 * CHAIN_SWAP_FEE_PCT["solana"]
    elif chain in ("bsc", "robinhood_chain"):
        base = BSC_RHC_BUY_SLIPPAGE + BSC_RHC_SELL_SLIPPAGE + 2 * CHAIN_SWAP_FEE_PCT.get(chain, 0.005)
    else:
        base = 0.10  # unknown chain -- conservative flat assumption, never a guessed-low default
    return base + _liquidity_impact_penalty(position_usd, liquidity_usd)


# --- pool state ------------------------------------------------------------
def _default_pool() -> dict:
    return {
        "balance_usd": None,
        "seed_usd": None,
        "session_start_ts": None,
        "open_position": None,
        "trades": [],
        "consecutive_losses": 0,
        "trades_this_session": 0,
        "tripped": False,
        "tripped_reason": None,
    }


def _get_pool() -> dict:
    return state.get_value(COMPOUND_STATE_KEY) or _default_pool()


def _save_pool(pool: dict):
    state.set_value(COMPOUND_STATE_KEY, pool)


def init_pool(seed_usd: Optional[float] = None, force_new_session: bool = False) -> dict:
    """Starts (or restarts) the compounding pool. Idempotent by default --
    calling this again mid-session does nothing unless force_new_session is
    explicitly True, so a scheduler or dashboard action that calls this
    defensively on every cycle can't accidentally wipe a live session.
    force_new_session is a deliberate reset (Ali starting a fresh attempt),
    never automatic."""
    pool = _get_pool()
    if pool.get("session_start_ts") is not None and not force_new_session:
        return pool
    seed = seed_usd if seed_usd is not None else SCALPER_CONFIG.seed_usd
    pool = _default_pool()
    pool["balance_usd"] = seed
    pool["seed_usd"] = seed
    pool["session_start_ts"] = time.time()
    _save_pool(pool)
    return pool


def reset_pool():
    """Manual restart -- not automatic, same philosophy as
    executor.circuit_breaker.reset_session(). Wipes trade history too,
    since restarting this pool is a genuinely new attempt, not a new
    day within the same one."""
    _save_pool(_default_pool())


def session_expired(pool: dict) -> bool:
    if not pool.get("session_start_ts"):
        return False
    elapsed_hours = (time.time() - pool["session_start_ts"]) / 3600.0
    return elapsed_hours >= SCALPER_CONFIG.session_hours


def status() -> dict:
    """Read-only snapshot for dashboard/reporting -- never mutates state."""
    pool = _get_pool()
    trades = pool.get("trades", [])
    realized_pnl = sum(t.get("pnl_usd", 0.0) for t in trades)
    seed = pool.get("seed_usd")
    balance = pool.get("balance_usd")
    return {
        "enabled": SCALPER_CONFIG.enabled,
        "started": pool.get("session_start_ts") is not None,
        "balance_usd": balance,
        "seed_usd": seed,
        "multiple_of_seed": (balance / seed) if (balance is not None and seed) else None,
        "realized_pnl_usd": realized_pnl,
        "trades_closed": len(trades),
        "trades_this_session": pool.get("trades_this_session", 0),
        "consecutive_losses": pool.get("consecutive_losses", 0),
        "tripped": pool.get("tripped", False),
        "tripped_reason": pool.get("tripped_reason"),
        "session_expired": session_expired(pool),
        "open_position": pool.get("open_position"),
        "banked_usd": pool.get("banked_usd", 0.0),
        "milestones_hit": pool.get("milestones_hit", []),
        "sprint": sprint_mode(),
    }


# --- entry -------------------------------------------------------------
@dataclass
class ScalpDecision:
    should_fire: bool
    reason: str
    position_usd: float = 0.0


MOMENTUM_MIN_SCORE_BAND = os.environ.get("COMPOUND_MOMENTUM_MIN_BAND", "C")


def signal_qualifies(score_band: Optional[str], momentum: bool = False) -> bool:
    """Signal-only half of the entry gate (no pool state) -- also used to
    paper-trade scalper candidates while the pool is off. A Layer 14
    momentum revival may enter one band lower (default C): the momentum IS
    the thesis, the exits are fast, and hard red flags never get this far."""
    floor = MOMENTUM_MIN_SCORE_BAND if momentum else SCALPER_CONFIG.min_score_band
    return BAND_RANK.get(score_band, -1) >= BAND_RANK.get(floor, 2)


def entry_gate(chain: str, token: str, score_band: Optional[str],
               liquidity_usd: Optional[float] = None, momentum: bool = False,
               lane: bool = False) -> ScalpDecision:
    """Pure decision logic, no network -- same should-vs-how split as
    executor/triggers.py. Call once per poll cycle per freshly-scored
    candidate."""
    if not SCALPER_CONFIG.enabled:
        return ScalpDecision(False, "compound scalper disabled")

    pool = _get_pool()
    if pool.get("session_start_ts") is None and sprint_mode():
        pool = init_pool()          # sprint: turning SPRINT_MODE on IS the deliberate start
    if pool.get("session_start_ts") is None:
        return ScalpDecision(False, "pool not started -- call init_pool() first (deliberate, manual step)")

    if sprint_mode():
        from executor.sprint import maybe_resume, apply_milestones
        pool = apply_milestones(pool)
        if pool.get("tripped"):
            pool = maybe_resume(pool)

    if pool.get("tripped"):
        return ScalpDecision(False, f"circuit tripped: {pool.get('tripped_reason')}")

    if session_expired(pool):
        return ScalpDecision(False, "session window elapsed -- pool stopped, awaiting review")

    balance = pool.get("balance_usd")
    if balance is None or balance < SCALPER_CONFIG.floor_usd:
        return ScalpDecision(False, f"pool balance ${balance or 0:.2f} at/below floor "
                                     f"(${SCALPER_CONFIG.floor_usd:.2f}) -- stopped")

    if pool.get("trades_this_session", 0) >= SCALPER_CONFIG.max_trades_per_session:
        return ScalpDecision(False, f"session trade cap reached ({SCALPER_CONFIG.max_trades_per_session})")

    if pool.get("open_position") is not None:
        return ScalpDecision(False, "a position is already open -- sequential mode, one at a time")

    if not lane and not signal_qualifies(score_band, momentum):
        return ScalpDecision(False, f"band {score_band or '?'} below minimum "
                                     f"({MOMENTUM_MIN_SCORE_BAND if momentum else SCALPER_CONFIG.min_score_band}) for this mode")

    position_usd = round(balance * SCALPER_CONFIG.risk_pct, 2)
    if position_usd <= 0:
        return ScalpDecision(False, "computed position size is $0")

    cost_pct = estimate_round_trip_cost_pct(chain, position_usd, liquidity_usd)
    if cost_pct > SCALPER_CONFIG.max_round_trip_cost_pct:
        return ScalpDecision(False, f"estimated round-trip cost {cost_pct*100:.1f}% exceeds cap "
                                     f"({SCALPER_CONFIG.max_round_trip_cost_pct*100:.0f}%) -- liquidity too thin")

    return ScalpDecision(True, f"band {score_band}, est. round-trip cost {cost_pct*100:.1f}%", position_usd)


_BUY_FN = {
    "solana": swap_executor.execute_buy_solana,
    "bsc": swap_executor.execute_buy_bsc,
    "robinhood_chain": swap_executor.execute_buy_robinhood_chain,
}


def open_scalp(chain: str, token: str, position_usd: float, entry_mcap: Optional[float], reason: str,
               profile: Optional[str] = None) -> dict:
    """Executes the real buy via swap_executor (inert until
    EXECUTION_ENABLED + a real key -- unchanged from every other buy path
    in this repo), and on success records the position onto the pool and
    debits the balance. On failure, still counts against
    trades_this_session (a failed attempt still used a cycle) but leaves
    the pool otherwise untouched so the same candidate isn't silently
    retried forever by a caller that doesn't check has an open position."""
    buy_fn = _BUY_FN.get(chain)
    if buy_fn is None:
        return {"ok": False, "reason": f"no buy path for chain '{chain}'"}

    result = buy_fn(token, position_usd)

    pool = _get_pool()
    pool["trades_this_session"] = pool.get("trades_this_session", 0) + 1

    if not result.ok:
        _save_pool(pool)
        return {"ok": False, "reason": result.reason, "swap_result": result}

    pool["open_position"] = {
        "chain": chain, "token": token, "entry_mcap": entry_mcap,
        "position_usd": position_usd, "amount_tokens": result.filled_amount_tokens or 0.0,
        "opened_ts": time.time(), "peak_multiple": 1.0, "partial_tp_done": False,
        "reason": reason, "profile": profile,
    }
    pool["balance_usd"] = round(pool.get("balance_usd", 0.0) - position_usd, 2)
    _save_pool(pool)
    return {"ok": True, "swap_result": result}


# --- exit ----------------------------------------------------------------
@dataclass
class ExitDecision:
    should_exit: bool
    reason: str
    pct_to_sell: float = 0.0   # 1.0 = full exit, <1.0 = partial (take-profit only)
    exit_type: str = ""        # "take_profit_partial" | "trail_stop" | "hard_stop" | "time_stop" | "hold"


def evaluate_exit(current_mcap_usd: Optional[float]) -> ExitDecision:
    """Pure decision logic, no network. Reads (does not write) the pool's
    open_position -- check_and_manage below is responsible for persisting
    peak_multiple updates before calling this."""
    pool = _get_pool()
    pos = pool.get("open_position")
    if not pos:
        return ExitDecision(False, "no open scalp position")

    entry_mcap = pos.get("entry_mcap")
    if not entry_mcap or not current_mcap_usd or entry_mcap <= 0:
        return ExitDecision(False, "missing entry or current mcap -- can't evaluate")

    mult = current_mcap_usd / entry_mcap
    peak = max(pos.get("peak_multiple", 1.0), mult)
    elapsed_min = (time.time() - pos.get("opened_ts", time.time())) / 60.0
    return scalp_exit_decision(mult, peak, elapsed_min, bool(pos.get("partial_tp_done")), pos.get("profile"))


def _exit_params(profile: Optional[str]) -> tuple:
    """(hard_stop_pct, take_profit_multiple, partial_tp_pct, trail_stop_pct,
    time_stop_minutes). "quick" = the sprint momentum lane (layer16)."""
    if profile == "quick":
        return (_env_float("LANE_STOP_PCT", 0.08), _env_float("LANE_TP_MULT", 1.12),
                _env_float("LANE_TP_SELL_PCT", 0.70), _env_float("LANE_TRAIL_PCT", 0.06),
                _env_float("LANE_TIME_STOP_MIN", 20.0))
    return (SCALPER_CONFIG.hard_stop_pct, SCALPER_CONFIG.take_profit_multiple, SCALPER_CONFIG.partial_tp_pct,
            SCALPER_CONFIG.trail_stop_pct, SCALPER_CONFIG.time_stop_minutes)


def scalp_exit_decision(mult: float, peak: float, elapsed_min: float, partial_tp_done: bool,
                        profile: Optional[str] = None) -> ExitDecision:
    """PURE scalper exit rules (split out Sept 30 2026 so the paper ledger
    and backtest_trades run exactly the same logic as the live pool)."""
    hard_stop_pct, tp_mult, tp_sell, trail_pct, time_stop = _exit_params(profile)
    if partial_tp_done and profile == "quick" and elapsed_min >= time_stop:
        return ExitDecision(True, f"{elapsed_min:.0f} min -- quick lane closes the remainder",
                             pct_to_sell=1.0, exit_type="time_stop")
    if not partial_tp_done and mult <= (1 - hard_stop_pct):
        return ExitDecision(True, f"{mult:.2f}x hit hard stop "
                                    f"({hard_stop_pct*100:.0f}% below entry)",
                             pct_to_sell=1.0, exit_type="hard_stop")

    if not partial_tp_done and mult >= tp_mult:
        return ExitDecision(True, f"{mult:.2f}x reached take-profit target "
                                    f"({tp_mult}x)",
                             pct_to_sell=tp_sell, exit_type="take_profit_partial")

    if partial_tp_done:
        pullback = (peak - mult) / peak if peak > 0 else 0.0
        if pullback >= trail_pct:
            return ExitDecision(True, f"{mult:.2f}x pulled back {pullback*100:.0f}% from peak "
                                        f"{peak:.2f}x after partial take-profit",
                                 pct_to_sell=1.0, exit_type="trail_stop")
        return ExitDecision(False, f"riding remainder, {mult:.2f}x (peak {peak:.2f}x)")

    if elapsed_min >= time_stop:
        return ExitDecision(True, f"{elapsed_min:.0f} min elapsed with no target hit "
                                    f"({mult:.2f}x) -- momentum thesis invalidated",
                             pct_to_sell=1.0, exit_type="time_stop")

    return ExitDecision(False, f"holding, {mult:.2f}x, {elapsed_min:.0f} min elapsed")


def check_and_manage(current_mcap_usd: Optional[float]) -> Optional[dict]:
    """Call this each poll cycle whenever the pool has an open position and
    a fresh mcap read for its token is available -- same shape as
    moonbag.check_and_trim / defensive_sell.check_and_defend. Returns None
    if there's no open position for this pool. Updates peak_multiple every
    call (even ones that don't exit) so the trailing stop has a real,
    persisted peak to measure from."""
    pool = _get_pool()
    pos = pool.get("open_position")
    if not pos:
        return None

    entry_mcap = pos.get("entry_mcap")
    if current_mcap_usd and entry_mcap:
        mult = current_mcap_usd / entry_mcap
        pos["peak_multiple"] = max(pos.get("peak_multiple", 1.0), mult)
        pool["open_position"] = pos
        _save_pool(pool)

    decision = evaluate_exit(current_mcap_usd)
    if not decision.should_exit:
        return {"exit_decision": decision, "sell_attempted": False}

    amount_to_sell = pos.get("amount_tokens", 0.0) * decision.pct_to_sell
    result = swap_executor.execute_sell(
        chain=pos["chain"], token_address=pos["token"], amount_tokens=amount_to_sell,
        reason=f"compound scalper {decision.exit_type}: {decision.reason}",
    )

    pool = _get_pool()  # re-read -- execute_sell makes network calls, state may have moved since
    pos = pool.get("open_position")
    if not pos:
        return {"exit_decision": decision, "sell_attempted": True, "sell_result": result}

    if result.ok and result.filled_usd is not None:
        cost_basis = pos["position_usd"] * decision.pct_to_sell
        pnl = result.filled_usd - cost_basis
        pool["balance_usd"] = round(pool.get("balance_usd", 0.0) + result.filled_usd, 2)
        pool["consecutive_losses"] = pool.get("consecutive_losses", 0) + 1 if pnl < 0 else 0
        pool.setdefault("trades", []).append({
            "chain": pos["chain"], "token": pos["token"], "exit_type": decision.exit_type,
            "reason": decision.reason, "pct_sold": decision.pct_to_sell,
            "filled_usd": result.filled_usd, "pnl_usd": pnl, "ts": time.time(), "profile": pos.get("profile"),
        })

        if decision.exit_type == "take_profit_partial":
            remaining = 1 - decision.pct_to_sell
            pos["amount_tokens"] = pos.get("amount_tokens", 0.0) * remaining
            pos["position_usd"] = pos["position_usd"] * remaining
            pos["partial_tp_done"] = True
            pool["open_position"] = pos
        else:
            pool["open_position"] = None  # fully closed -- entry_gate can fire again next cycle

        _evaluate_trip(pool)

    _save_pool(pool)
    return {"exit_decision": decision, "sell_attempted": True, "sell_result": result}


def _evaluate_trip(pool: dict):
    """Mirrors executor/circuit_breaker.py's trip logic, on this pool's own
    counters -- deliberately NOT the same breaker Stage 1/2 uses, since
    this is a separate pool with its own budget and shouldn't trip (or stay
    silent) on the other's losses."""
    if pool.get("consecutive_losses", 0) >= SCALPER_CONFIG.max_consecutive_losses:
        pool["tripped"] = True
        pool["tripped_reason"] = (f"{pool['consecutive_losses']} consecutive losses "
                                    f"(cap: {SCALPER_CONFIG.max_consecutive_losses})")
        pool["tripped_ts"] = time.time()
        pool["tripped_kind"] = "losing_streak"
        return
    seed = pool.get("seed_usd")
    if not seed:
        return
    realized_pnl = sum(t.get("pnl_usd", 0.0) for t in pool.get("trades", []))
    loss_pct = -realized_pnl / seed
    if loss_pct >= SCALPER_CONFIG.max_session_loss_pct:
        pool["tripped"] = True
        pool["tripped_reason"] = (f"session loss {loss_pct*100:.1f}% of seed "
                                    f"(cap: {SCALPER_CONFIG.max_session_loss_pct*100:.0f}%)")
        pool["tripped_ts"] = time.time()
        pool["tripped_kind"] = "session_loss"

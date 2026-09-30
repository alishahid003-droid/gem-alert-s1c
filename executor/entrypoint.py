"""
Integration entrypoint -- the single call a live poll loop makes per
candidate, instead of having to know the right order to call triggers.py,
position_state.py, and moonbag.py itself. This is glue, not new decision
logic: everything it does is delegate to those three modules in the correct
sequence, and it makes no network calls of its own.

STATUS: built and tested. As of Sept 25, 2026 this DOES call the real
swap_executor.execute_buy_* functions and record real fills on a fire --
previously it only decided a position WOULD be opened. It remains safe by
construction either way: execute_buy_* refuses unless EXECUTION_ENABLED is
"true" and a wallet key is configured, so nothing here moves real money
until Ali turns that on. handle_stage1_candidate/handle_stage2_candidate
are wired into scheduler.py's live poll loop (see _handle_scored).

Usage (once wired): for each candidate Layers 0/0b, 1, 2 have already
scored on a poll cycle, call handle_stage1_candidate with whatever's known
at launchpad level. For a coin graduating with Fomo convergence, call
handle_stage2_candidate. Both return a dict describing what happened either
way (fired or not, and why) so the caller can log or alert on the outcome
without re-deriving it.

handle_compound_scalper_candidate (added Sept 29 2026, Ali's explicit ask
for a fast/aggressive compounding mode -- see executor/compound_scalper.py's
own module docstring for the full honesty flag and design rationale) is a
SEPARATE, independent entry path alongside the two above -- it evaluates
its own isolated pool via executor.compound_scalper, never touches Stage 1/
Stage 2's position_state or budget, and is a no-op unless
COMPOUND_SCALPER_ENABLED="true" AND compound_scalper.init_pool() has
already been called (deliberate manual start, not automatic).
"""
from typing import Optional

import executor.position_state as position_state
import executor.triggers as triggers
import executor.moonbag as moonbag
import executor.swap_executor as swap_executor
import executor.compound_scalper as compound_scalper
import executor.paper_ledger as paper_ledger
import executor.entry_guards as entry_guards

# Refusals that are about MONEY (budget, position cap, loss breaker) rather
# than signal quality -- the paper ledger still records those candidates so
# the win-rate stats measure the signal, not the wallet (Sept 30 2026).
_MONEY_LIMIT_MARKERS = ("budget exhausted", "max concurrent", "circuit breaker")


def _paper_eligible(decision) -> bool:
    return decision.should_fire or any(m in (decision.reason or "") for m in _MONEY_LIMIT_MARKERS)


_BUY_FUNCTIONS = {
    "solana": swap_executor.execute_buy_solana,
    "bsc": swap_executor.execute_buy_bsc,
    "robinhood_chain": swap_executor.execute_buy_robinhood_chain,
}


def _attempt_buy_and_record_fill(chain: str, token: str, usd_amount: float, stage: Optional[str] = None) -> dict:
    """Actually calls the real buy (guarded by EXECUTION_ENABLED -- inert
    by construction until that's turned on, same guard every execute_buy_*
    already has) and, on success, records the REAL filled token quantity
    onto the position so moonbag.check_and_trim() has real numbers to work
    with. Previously this module only decided a position WOULD be opened
    and never called swap_executor at all -- meaning even with
    EXECUTION_ENABLED=true, nothing here actually bought anything, and even
    if it had, the trim ladder had no real token quantity to trim against.

    Fixed Sept 28 2026: on an outright buy failure (result.ok is False),
    tags the stage entry position_state.record_stage_entry already
    committed before this call as buy_status='failed' -- otherwise that
    usd_amount permanently counted against stage_committed_usd's budget
    forever, with zero tokens ever received. Does NOT un-fire the stage
    (has_stage() stays True) -- see position_state.mark_stage_buy_failed's
    docstring for why retrying a persistently-failing token every cycle
    would be its own bug. `stage` is optional only so this function stays
    callable the old way; both real callers below always pass it."""
    buy_fn = _BUY_FUNCTIONS.get(chain)
    if buy_fn is None:
        reason = f"no buy function wired for chain '{chain}'"
        if stage is not None:
            position_state.mark_stage_buy_failed(chain, token, stage, reason=reason)
        return {"attempted": False, "ok": False, "reason": reason}
    result = buy_fn(token, usd_amount)
    if result.ok:
        position_state.record_fill(chain, token, result.filled_amount_tokens, tx_signature=result.tx_signature)
    elif stage is not None:
        position_state.mark_stage_buy_failed(chain, token, stage, reason=result.reason)
    return {
        "attempted": True, "ok": result.ok, "reason": result.reason,
        "tx_signature": result.tx_signature, "filled_amount_tokens": result.filled_amount_tokens,
    }


def handle_stage1_candidate(chain: str, token: str, score_band: Optional[str],
                             deployer_tier: Optional[str], convergence_count: int,
                             entry_mcap: Optional[float], insider_ratio: Optional[float] = None,
                             has_news_catalyst: bool = False,
                             signal_coverage: Optional[float] = None,
                             liquidity_usd: Optional[float] = None,
                             entry_ctx: Optional[dict] = None) -> dict:
    """Evaluates the Stage 1 trigger and, if it fires, records the position
    and locks in its moonbag ladder in the same step. entry_mcap is the
    launchpad-level mcap at the moment of firing -- the caller (a future
    scheduler integration) already has this from Layer 0/0b's own scan, so
    it isn't re-fetched here."""
    decision = triggers.evaluate_stage1(chain, token, score_band, deployer_tier, convergence_count,
                                        signal_coverage=signal_coverage)
    signal = paper_ledger.classify_stage1_signal(score_band, deployer_tier, convergence_count, signal_coverage)
    ctx = dict(entry_ctx or {})
    ctx.setdefault("liquidity_usd", liquidity_usd)
    ctx.setdefault("signals", 1 + int(deployer_tier in ("elite", "good")) + int(convergence_count >= 2))
    guard_ok, guard_why = entry_guards.check(score_band, triggers.stage1_position_usd_for_band(score_band), ctx)
    if signal and _paper_eligible(decision):
        paper_ledger.open_paper(chain, token, "stage1", signal,
                                triggers.stage1_position_usd_for_band(score_band), entry_mcap,
                                band=score_band, liquidity_usd=liquidity_usd,
                                guard="pass" if guard_ok else "blocked")
    if decision.should_fire:
        allowed, why = paper_ledger.signal_allowed(signal)
        if not allowed:
            return {"fired": False, "stage": "stage1", "reason": f"paper-record gate: {why}"}
        if not guard_ok:
            return {"fired": False, "stage": "stage1", "reason": f"entry guard: {guard_why}"}
    if not decision.should_fire:
        return {"fired": False, "stage": "stage1", "reason": decision.reason}

    position_state.record_stage_entry(chain, token, "stage1", decision.position_usd, entry_mcap, decision.reason)
    conviction = moonbag.assess_conviction(
        chain, token, deployer_tier=deployer_tier, convergence_count=convergence_count,
        double_confirmed=False,  # a lone Stage 1 fire can't be double-confirmed yet
        insider_ratio=insider_ratio, has_news_catalyst=has_news_catalyst,
    )
    buy_result = _attempt_buy_and_record_fill(chain, token, decision.position_usd, stage="stage1")
    return {
        "fired": True, "stage": "stage1", "position_usd": decision.position_usd,
        "reason": decision.reason, "conviction_score": conviction["score"],
        "trim_ladder": conviction["ladder"], "buy": buy_result,
    }


def handle_stage2_candidate(chain: str, token: str, current_mcap_usd: Optional[float],
                             fomo_convergence_count: int, graduated: bool,
                             deployer_tier: Optional[str] = None, insider_ratio: Optional[float] = None,
                             has_news_catalyst: bool = False,
                             signal_name: str = "fomo_convergence") -> dict:
    """Evaluates the Stage 2 trigger and, if it fires, records the position
    and (re-)assesses conviction -- picking up double-confirmation if a
    Stage 1 entry already exists on this token. See moonbag.assess_conviction's
    docstring for why a second scoring call can only upgrade a position's
    ladder, never downgrade one Stage 1 already locked in."""
    decision = triggers.evaluate_stage2(chain, token, current_mcap_usd, fomo_convergence_count, graduated)
    if _paper_eligible(decision):
        from executor.config import EXECUTOR_CONFIG
        paper_ledger.open_paper(chain, token, "stage2", signal_name,
                                EXECUTOR_CONFIG.stage2_position_usd, current_mcap_usd,
                                tags={"traders": fomo_convergence_count})
    if decision.should_fire:
        allowed, why = paper_ledger.signal_allowed(signal_name)
        if not allowed:
            return {"fired": False, "stage": "stage2", "reason": f"paper-record gate: {why}"}
    if not decision.should_fire:
        return {"fired": False, "stage": "stage2", "reason": decision.reason}

    position_state.record_stage_entry(chain, token, "stage2", decision.position_usd, current_mcap_usd, decision.reason)
    double_confirmed = triggers.is_double_confirmed(chain, token)
    conviction = moonbag.assess_conviction(
        chain, token, deployer_tier=deployer_tier, convergence_count=fomo_convergence_count,
        double_confirmed=double_confirmed, insider_ratio=insider_ratio, has_news_catalyst=has_news_catalyst,
    )
    buy_result = _attempt_buy_and_record_fill(chain, token, decision.position_usd, stage="stage2")
    return {
        "fired": True, "stage": "stage2", "position_usd": decision.position_usd,
        "reason": decision.reason, "double_confirmed": double_confirmed,
        "conviction_score": conviction["score"], "trim_ladder": conviction["ladder"], "buy": buy_result,
    }


def handle_compound_scalper_candidate(chain: str, token: str, score_band: Optional[str],
                                       entry_mcap: Optional[float],
                                       liquidity_usd: Optional[float] = None,
                                       momentum: bool = False,
                                       entry_ctx: Optional[dict] = None) -> dict:
    """Evaluates executor.compound_scalper's own entry gate and, if it
    fires, opens the scalp position through that module. Independent of
    Stage 1/Stage 2 -- a token can fire this AND a stage entry in the same
    cycle, since they draw from separate pools/budgets. No-op (should_fire
    always False) unless COMPOUND_SCALPER_ENABLED="true" and
    compound_scalper.init_pool() has already been called -- see that
    module's own docstring for why the pool start is a deliberate manual
    step rather than automatic."""
    # Paper-trade every scalper-quality signal even while the pool is off, so
    # the scalper's own win rate is measured before any money goes in.
    ctx = dict(entry_ctx or {})
    ctx.setdefault("liquidity_usd", liquidity_usd)
    guard_ok, guard_why = entry_guards.check(score_band, paper_ledger.PAPER_SCALP_USD, ctx, momentum=momentum)
    if compound_scalper.signal_qualifies(score_band, momentum):
        paper_ledger.open_paper(chain, token, "scalper",
                                "scalper_momentum" if momentum else f"scalper_band_{score_band}",
                                paper_ledger.PAPER_SCALP_USD, entry_mcap, band=score_band,
                                liquidity_usd=liquidity_usd, strategy="scalper",
                                guard="pass" if guard_ok else "blocked")
    decision = compound_scalper.entry_gate(chain, token, score_band, liquidity_usd=liquidity_usd,
                                           momentum=momentum)
    if decision.should_fire and not guard_ok:
        return {"fired": False, "mode": "compound_scalper", "reason": f"entry guard: {guard_why}"}
    if not decision.should_fire:
        return {"fired": False, "mode": "compound_scalper", "reason": decision.reason}

    open_result = compound_scalper.open_scalp(chain, token, decision.position_usd, entry_mcap, decision.reason)
    return {
        "fired": True, "mode": "compound_scalper", "position_usd": decision.position_usd,
        "reason": decision.reason, "open": open_result,
    }


def moonshot_enabled() -> bool:
    import os
    return os.environ.get("MOONSHOT_ENABLED", "").strip().lower() == "true"


def _env_f(name: str, default: float) -> float:
    import os
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def bankroll_equity() -> float:
    """Starting wallet (TOTAL_WALLET_USD) plus realized P&L of closed real
    positions -- the non-aggressor side of the account (the compound scalper
    keeps its own pool)."""
    from executor.config import EXECUTOR_CONFIG
    try:
        realized = position_state.realized_pnl_summary().get("total_realized_pnl_usd") or 0.0
    except Exception:  # noqa: BLE001
        realized = 0.0
    return max(0.0, float(EXECUTOR_CONFIG.total_wallet_usd) + realized)


# Moonshot stake ladder (Oct 1 2026, Ali: "$5 for a moonshot is useless -- it
# gets eaten by fees; at least $30-40, and $50-60 once the account is 5-7x").
# (equity from, stake): $30 from the start, $40 from $300, $60 from $700;
# above that MOONSHOT_EQUITY_PCT (8%) of equity, capped at MOONSHOT_MAX_USD
# (memecoin pools can't absorb much more). Never more than MOONSHOT_MAX_SHARE
# (35%) of equity. MOONSHOT_POSITION_USD, if set, overrides everything.
MOONSHOT_LADDER = ((0.0, 30.0), (300.0, 40.0), (700.0, 60.0))


def moonshot_position_usd(equity: Optional[float] = None) -> float:
    import os
    if os.environ.get("MOONSHOT_POSITION_USD", "").strip():
        return _env_f("MOONSHOT_POSITION_USD", 30.0)
    equity = bankroll_equity() if equity is None else equity
    stake = 0.0
    for lo, usd in MOONSHOT_LADDER:
        if equity >= lo:
            stake = usd
    stake = max(stake, min(equity * _env_f("MOONSHOT_EQUITY_PCT", 0.08), _env_f("MOONSHOT_MAX_USD", 2000.0)))
    return round(min(stake, equity * _env_f("MOONSHOT_MAX_SHARE", 0.35)), 2)


def moonshot_max_open(equity: Optional[float] = None) -> int:
    """One moonshot at a time until the account passes $300, then two."""
    import os
    if os.environ.get("MOONSHOT_MAX_OPEN", "").strip():
        return int(_env_f("MOONSHOT_MAX_OPEN", 1))
    equity = bankroll_equity() if equity is None else equity
    return 1 if equity < 300 else 2


def open_moonshots_at_risk() -> int:
    """Moonshots whose stake is still at risk (a free-riding runner, stake
    already recovered, doesn't count)."""
    n = 0
    for pos in position_state.list_open_positions():
        if "moonshot" in (pos.get("stages") or {}) and pos.get("amount_tokens") and not pos.get("breakeven_locked"):
            n += 1
    return n


def handle_moonshot_candidate(chain: str, token: str, score_band: Optional[str],
                              entry_mcap: Optional[float], liquidity_usd: Optional[float],
                              moonshot_score: int, entry_ctx: Optional[dict] = None) -> dict:
    """Layer 15 entry path (Sept 30 2026). Always paper-trades the moonshot
    (profile "moonshot": stake back at 2x, half rides as a runner). Real money
    only with MOONSHOT_ENABLED=true, band B+ safety (entry guards, momentum
    rules), the normal position cap, and a buy path on the chain."""
    usd = moonshot_position_usd()
    ctx = dict(entry_ctx or {})
    ctx.setdefault("liquidity_usd", liquidity_usd)
    guard_ok, guard_why = entry_guards.check(score_band, usd, ctx, momentum=True)
    paper_ledger.open_paper(chain, token, "moonshot", "moonshot", usd, entry_mcap,
                            band=score_band, liquidity_usd=liquidity_usd,
                            tags={"moonshot_score": moonshot_score},
                            guard="pass" if guard_ok else "blocked", exit_profile="moonshot")
    if not moonshot_enabled():
        return {"fired": False, "stage": "moonshot", "reason": "MOONSHOT_ENABLED is off (paper + alert only)"}
    if chain not in _BUY_FUNCTIONS:
        return {"fired": False, "stage": "moonshot", "reason": f"no buy path on {chain} yet (paper only)"}
    if not guard_ok:
        return {"fired": False, "stage": "moonshot", "reason": f"entry guard: {guard_why}"}
    if position_state.has_stage(chain, token, "moonshot"):
        return {"fired": False, "stage": "moonshot", "reason": "already holding this moonshot"}
    block = triggers._concurrency_block()
    if block:
        return {"fired": False, "stage": "moonshot", "reason": block}
    if open_moonshots_at_risk() >= moonshot_max_open():
        return {"fired": False, "stage": "moonshot",
                "reason": f"already {moonshot_max_open()} moonshot(s) with stake at risk (limit for this account size)"}
    if usd < 10:
        return {"fired": False, "stage": "moonshot", "reason": f"account too small for a meaningful moonshot (${usd:.2f})"}
    allowed, why = paper_ledger.signal_allowed("moonshot")
    if not allowed:
        return {"fired": False, "stage": "moonshot", "reason": f"paper-record gate: {why}"}
    position_state.record_stage_entry(chain, token, "moonshot", usd, entry_mcap,
                                      f"Layer 15 moonshot score {moonshot_score}")
    position_state.set_exit_profile(chain, token, "moonshot")
    buy_result = _attempt_buy_and_record_fill(chain, token, usd, stage="moonshot")
    return {"fired": True, "stage": "moonshot", "position_usd": usd, "reason": f"moonshot score {moonshot_score}",
            "buy": buy_result}

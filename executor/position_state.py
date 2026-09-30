"""
Position tracking across Stage 1 / Stage 2 -- reuses state.py's existing
get_value/set_value backend (Upstash when configured, local JSON otherwise),
same pattern as every other layer's cross-cycle state.

A "position" here is keyed by (chain, token_address) and records which
stage(s) have fired on it, so:
  - Stage 2 doesn't need Stage 1 to have fired first (per Ali's direction --
    they're independent triggers), but if Stage 1 DID fire, Stage 2 knows to
    stack onto the existing position rather than treating it as fresh.
  - Neither stage fires twice on the same token.
  - The double-confirmed tier (both stages fired) is just "check both flags
    are set" -- no separate bookkeeping needed.
"""
import time
from typing import Optional

import state


def _key(chain: str, token: str) -> str:
    return f"exec_position:{chain}:{token.lower()}"


def get_position(chain: str, token: str) -> Optional[dict]:
    return state.get_value(_key(chain, token))


def record_stage_entry(chain: str, token: str, stage: str, usd_amount: float,
                        entry_mcap: Optional[float], reason: str):
    """stage: 'stage1' or 'stage2'. Adds to the position, doesn't overwrite
    the other stage's entry if one already exists."""
    pos = get_position(chain, token) or {
        "chain": chain, "token": token, "stages": {}, "total_usd": 0.0,
        "opened_ts": time.time(), "status": "open",
    }
    pos["stages"][stage] = {
        "usd_amount": usd_amount, "entry_mcap": entry_mcap,
        "reason": reason, "ts": time.time(),
    }
    pos["total_usd"] = sum(s["usd_amount"] for s in pos["stages"].values())
    pos["double_confirmed"] = "stage1" in pos["stages"] and "stage2" in pos["stages"]
    state.set_value(_key(chain, token), pos)
    return pos


def has_stage(chain: str, token: str, stage: str) -> bool:
    pos = get_position(chain, token)
    return bool(pos and stage in pos.get("stages", {}))


def record_fill(chain: str, token: str, amount_tokens: Optional[float], tx_signature: Optional[str] = None):
    """Adds a REAL, on-chain-confirmed token quantity to the position's
    running total -- called once per successful buy, with
    ExecutionResult.filled_amount_tokens (never the requested USD size).
    Without this, moonbag.check_and_trim() reads amount_tokens=0 off every
    position and every trim silently sells nothing. A None or non-positive
    amount is deliberately ignored (never treated as "add 0") rather than
    zeroing out an existing total -- a fill-parsing failure on one call
    shouldn't erase a real quantity recorded by an earlier one (e.g. Stage 1
    filled fine, Stage 2's fill-amount parse failed for an unrelated reason).

    Real gap this closes (Sept 28 2026): a buy whose on-chain tx SUCCEEDED
    but whose fill-amount parsing failed (or a "sent but could not confirm"
    result that still carries a tx_signature) used to look IDENTICAL to a
    position that was opened but never bought into at all -- amount_tokens
    stays unset either way, so moonbag/defensive_sell can't tell "we have
    no idea how many tokens we hold" apart from "we hold none." Now that
    case is tagged fill_status="unconfirmed_amount" with the tx_signature
    recorded, so it's visible for manual reconciliation instead of silently
    indistinguishable from an unopened position."""
    if amount_tokens is None or amount_tokens <= 0:
        pos = get_position(chain, token)
        if pos and tx_signature:
            pos["fill_status"] = "unconfirmed_amount"
            pos.setdefault("unconfirmed_fills", []).append({"tx_signature": tx_signature, "ts": time.time()})
            state.set_value(_key(chain, token), pos)
        return
    pos = get_position(chain, token)
    if not pos:
        return
    pos["amount_tokens"] = pos.get("amount_tokens", 0.0) + amount_tokens
    pos["fill_status"] = "confirmed"
    state.set_value(_key(chain, token), pos)


def mark_stage_buy_failed(chain: str, token: str, stage: str, reason: str = ""):
    """Called when the real buy for a stage that just fired failed outright
    (ExecutionResult.ok is False). Deliberately does NOT remove the stage
    entry or clear has_stage() -- triggers.evaluate_stage1/2 use has_stage()
    to guarantee a stage only ever fires ONCE per token
    (test_stage1_already_fired_blocks_second_entrypoint_call encodes this on
    purpose: retrying a persistently-failing token every single poll cycle
    forever would be its own bug, hammering the same dead RPC/liquidity
    problem repeatedly). A failed buy is still a real, final outcome for
    that stage -- it just isn't a MONEY outcome.

    Real bug this closes instead (Sept 28 2026): record_stage_entry runs
    BEFORE the buy is attempted (the trade decision, including committing
    usd_amount to total_usd, is locked in ahead of the network call) -- so a
    buy that fails outright used to leave that usd_amount permanently
    counted in stage_committed_usd() forever, with zero tokens ever
    received, silently shrinking the real trading budget on every failed
    buy with no recovery path. Tagging the stage entry buy_status='failed'
    lets stage_committed_usd() exclude it from committed budget (see that
    function) while has_stage() still reports the stage as fired."""
    pos = get_position(chain, token)
    if not pos or stage not in pos.get("stages", {}):
        return None
    pos["stages"][stage]["buy_status"] = "failed"
    pos["stages"][stage]["fail_reason"] = reason
    # Recompute total_usd (real cost basis) the same way stage_committed_usd
    # now does -- a failed stage never actually spent its usd_amount, so it
    # shouldn't inflate the basis used for close_position's pnl_usd either.
    pos["total_usd"] = sum(
        s["usd_amount"] for s in pos["stages"].values() if s.get("buy_status") != "failed"
    )
    state.set_value(_key(chain, token), pos)
    return pos


def close_position(chain: str, token: str, reason: str, exit_usd: Optional[float] = None):
    pos = get_position(chain, token)
    if not pos:
        return None
    pos["status"] = "closed"
    pos["close_reason"] = reason
    pos["closed_ts"] = time.time()
    if exit_usd is not None:
        pos["exit_usd"] = exit_usd
        pos["pnl_usd"] = exit_usd - pos["total_usd"]
    state.set_value(_key(chain, token), pos)
    _record_deployer_outcome_for_close(chain, token, pos, reason)
    return pos


def _record_deployer_outcome_for_close(chain: str, token: str, pos: dict, reason: str):
    """Ties this position's real, priced outcome to the token's deployer
    wallet so state.py's deployer track record builds up from our own
    trade history (Sept 30 2026, Ali: "build that and also do that vice
    versa... blacklist this developer and avoid coins launched from him").

    Solana only -- layer0_scoring.fetch_solana_token_deployer is
    Solana-RPC-specific, same restriction as every other deployer-wallet
    lookup in this codebase. Best-effort and deliberately swallows any
    lookup failure: a deployer-reputation write is a nice-to-have on top of
    a real close_position() call, and must never be the thing that breaks
    one. Skipped entirely on an unpriced close (pnl_usd is None) -- no real
    outcome to attribute to the deployer either way."""
    if chain != "solana":
        return
    pnl_usd = pos.get("pnl_usd")
    if pnl_usd is None:
        return
    outcome = "rug" if reason.startswith("defensive_sell") else ("win" if pnl_usd > 0 else "loss")
    try:
        from layers.layer0_scoring import fetch_solana_token_deployer
        deployer_wallet = fetch_solana_token_deployer(token)
    except Exception as e:
        print(f"[position_state] deployer lookup skipped for {token[:8]}: {e}")
        return
    if not deployer_wallet:
        return
    pos["deployer_wallet"] = deployer_wallet
    state.set_value(_key(chain, token), pos)
    state.record_deployer_outcome(deployer_wallet, token, chain, outcome, pnl_usd)
    print(f"[position_state] deployer {deployer_wallet[:8]} outcome={outcome} "
          f"pnl=${pnl_usd:.2f} for {token[:8]}")


def list_open_positions() -> list:
    """NOTE: state.py has no native "list keys by prefix" op (Upstash's REST
    API doesn't cheaply support SCAN over a single GET/SET pattern) -- so
    this relies on a separate index list maintained alongside individual
    position records, updated by record_stage_entry/close_position below.

    Fetches every indexed position in ONE pipelined state.get_values() call
    rather than one state.get_value() per key (Ali, Sept 30 2026 -- this
    was the root cause of the dashboard's /api/data hanging for minutes:
    with dozens of positions accumulated from weeks of testing, the old
    per-key loop meant dozens of sequential HTTP round trips, each subject
    to utils/http.py's own retry/backoff stack)."""
    index = state.get_value("exec_position_index") or []
    records = state.get_values(index)
    return [pos for pos in records.values() if pos and pos.get("status") == "open"]


def list_closed_positions(limit: int = 100) -> list:
    """Mirrors list_open_positions() but for status == 'closed' -- feeds
    the dashboard's Closed Positions table (Tasks Left #3/#5, Sept 25
    2026). Newest-closed first. Same pipelined-batch-fetch fix as
    list_open_positions() above."""
    index = state.get_value("exec_position_index") or []
    records = state.get_values(index)
    closed = [pos for pos in records.values() if pos and pos.get("status") == "closed"]
    closed.sort(key=lambda p: p.get("closed_ts", 0), reverse=True)
    return closed[:limit]


def realized_pnl_summary() -> dict:
    """Sum of pnl_usd across every closed position that has one (a
    position closed without a priceable exit_usd -- e.g. a sell whose
    filled_usd genuinely couldn't be computed -- is excluded from the
    total rather than counted as 0, so the number stays honest)."""
    closed = list_closed_positions(limit=100000)
    priced = [p for p in closed if p.get("pnl_usd") is not None]
    total = sum(p["pnl_usd"] for p in priced)
    wins = sum(1 for p in priced if p["pnl_usd"] > 0)
    return {
        "closed_count": len(closed), "priced_count": len(priced),
        "total_realized_pnl_usd": total, "wins": wins,
        "losses": len(priced) - wins,
    }


def _index_add(chain: str, token: str):
    index = state.get_value("exec_position_index") or []
    key = _key(chain, token)
    if key not in index:
        index.append(key)
        state.set_value("exec_position_index", index)


# wrap record_stage_entry to keep the index updated -- done as a thin
# override rather than editing the function above, so the index-maintenance
# concern stays visibly separate from the position-record logic itself.
_record_stage_entry_inner = record_stage_entry


def record_stage_entry(chain: str, token: str, stage: str, usd_amount: float,
                        entry_mcap: Optional[float], reason: str):
    pos = _record_stage_entry_inner(chain, token, stage, usd_amount, entry_mcap, reason)
    _index_add(chain, token)
    return pos


def record_moonbag_trim(chain: str, token: str, tier_multiple: float,
                         pct_of_original: float, exit_usd: Optional[float]):
    """Records that a moonbag trim tier fired and sold pct_of_original of
    the ORIGINAL position (not of whatever remains) -- so tiers are additive
    and independent of each other, and re-checking after a restart still
    knows exactly which rungs already fired. Position stays 'open' after a
    trim; only defensive_sell's rug detection or an eventual full manual
    close actually closes it -- the un-trimmed remainder (the moonbag
    itself) is meant to keep riding."""
    pos = get_position(chain, token)
    if not pos:
        return None
    trims = pos.get("moonbag_trims", {})
    trims[str(tier_multiple)] = {
        "pct_of_original": pct_of_original, "exit_usd": exit_usd, "ts": time.time(),
    }
    pos["moonbag_trims"] = trims
    state.set_value(_key(chain, token), pos)
    return pos


def set_trim_ladder(chain: str, token: str, ladder_name: str, score: int, rungs: Optional[list] = None):
    """Called once, right after a Stage 1/Stage 2 fire, to lock in which
    moonbag trim ladder this position uses for its whole life. Locked at
    entry rather than recomputed on every poll so a position doesn't flip
    ladders mid-flight as signals wobble -- the conviction call is made
    once, with the best information available at entry time.

    rungs (added Sept 28 2026): an optional pre-computed (multiple, pct)
    ladder specific to THIS position -- used for the real hard-dollar-target
    ladder (moonbag.compute_hard_target_ladder), where the right multiples
    depend on this position's own entry size, not a fixed shared table like
    LADDERS_BY_NAME. None clears any previously-stored rungs (e.g. a
    downgrade path, though none exists today) so a stale custom ladder
    never survives a ladder_name change to something that shouldn't have one."""
    pos = get_position(chain, token)
    if not pos:
        return None
    pos["trim_ladder"] = ladder_name
    pos["moonshot_score"] = score
    if rungs:
        pos["trim_ladder_rungs"] = rungs
    else:
        pos.pop("trim_ladder_rungs", None)
    state.set_value(_key(chain, token), pos)
    return pos


def get_trim_ladder(chain: str, token: str) -> str:
    """Defaults to 'default' (the standard aggressive ladder) for any
    position that never had a conviction score computed for it -- e.g. an
    older position, or a caller that skipped moonbag.assess_conviction."""
    pos = get_position(chain, token)
    return (pos or {}).get("trim_ladder", "default")


def remaining_pct(chain: str, token: str) -> float:
    """1.0 minus everything already trimmed off -- what's still riding."""
    pos = get_position(chain, token)
    if not pos:
        return 0.0
    trims = pos.get("moonbag_trims", {})
    return max(0.0, 1.0 - sum(t["pct_of_original"] for t in trims.values()))


def stage_committed_usd(stage: str) -> float:
    """Sum of capital still tied up in a given stage's entries across all
    OPEN positions -- i.e. the original stage amount minus whatever
    fraction moonbag.py has already trimmed off. This is what
    triggers.py checks a new fire against the stage's budget: without it,
    evaluate_stage1/evaluate_stage2 would keep firing on every qualifying
    candidate with no regard for how much of the wallet is already
    committed, which is fine against an unlimited paper wallet but not
    against a real $50-100 one."""
    total = 0.0
    for pos in list_open_positions():
        entry = pos.get("stages", {}).get(stage)
        if not entry:
            continue
        if entry.get("buy_status") == "failed":
            # See mark_stage_buy_failed's docstring: a failed buy still
            # counts as "fired" (has_stage stays True, no re-fire) but
            # never actually spent this budget, so it must not keep
            # counting against it forever.
            continue
        remaining = remaining_pct(pos["chain"], pos["token"])
        total += entry["usd_amount"] * remaining
    return total

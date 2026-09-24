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


def record_fill(chain: str, token: str, amount_tokens: Optional[float]):
    """Adds a REAL, on-chain-confirmed token quantity to the position's
    running total -- called once per successful buy, with
    ExecutionResult.filled_amount_tokens (never the requested USD size).
    Without this, moonbag.check_and_trim() reads amount_tokens=0 off every
    position and every trim silently sells nothing. A None or non-positive
    amount is deliberately ignored rather than zeroing out an existing
    total -- a fill-parsing failure on one call shouldn't erase a real
    quantity recorded by an earlier one (e.g. Stage 1 filled fine, Stage 2's
    fill-amount parse failed for an unrelated reason)."""
    if amount_tokens is None or amount_tokens <= 0:
        return
    pos = get_position(chain, token)
    if not pos:
        return
    pos["amount_tokens"] = pos.get("amount_tokens", 0.0) + amount_tokens
    state.set_value(_key(chain, token), pos)


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
    return pos


def list_open_positions() -> list:
    """NOTE: state.py has no native "list keys by prefix" op (Upstash's REST
    API doesn't cheaply support SCAN over a single GET/SET pattern) -- so
    this relies on a separate index list maintained alongside individual
    position records, updated by record_stage_entry/close_position below."""
    index = state.get_value("exec_position_index") or []
    open_positions = []
    for key in index:
        pos = state.get_value(key)
        if pos and pos.get("status") == "open":
            open_positions.append(pos)
    return open_positions


def list_closed_positions(limit: int = 100) -> list:
    """Mirrors list_open_positions() but for status == 'closed' -- feeds
    the dashboard's Closed Positions table (Tasks Left #3/#5, Sept 25
    2026). Newest-closed first."""
    index = state.get_value("exec_position_index") or []
    closed = []
    for key in index:
        pos = state.get_value(key)
        if pos and pos.get("status") == "closed":
            closed.append(pos)
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


def set_trim_ladder(chain: str, token: str, ladder_name: str, score: int):
    """Called once, right after a Stage 1/Stage 2 fire, to lock in which
    moonbag trim ladder this position uses for its whole life. Locked at
    entry rather than recomputed on every poll so a position doesn't flip
    ladders mid-flight as signals wobble -- the conviction call is made
    once, with the best information available at entry time."""
    pos = get_position(chain, token)
    if not pos:
        return None
    pos["trim_ladder"] = ladder_name
    pos["moonshot_score"] = score
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
        remaining = remaining_pct(pos["chain"], pos["token"])
        total += entry["usd_amount"] * remaining
    return total

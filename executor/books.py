"""Books: every trading module has its own virtual balance, its own target and
timeline (where it has one), and its own stake sizing (Ali, Oct 12 2026:
"balance is split ... each runs on its own pace and compounds on its own
balance toward its target").

  sprint    moonshot book  $100 -> $10,000 in 96 hours     (SPRINT_*)  [Ali Oct 13: first target once live]
  core1     moonshot book  $100 -> $125,000 in ~30 days    (CORE1_*)
  core2     moonshot book  $100 -> $1,000,000 in ~180 days (CORE2_*, stretch $1.5M)
  compound  daily-profit book: small steady gains, daily goal (COMPOUND_DAILY_GOAL_PCT)
  fomo_followers / fomo_new / leaderboard / dev_list: copy books, no target, own P&L

Balance = seed + realized P&L of that book's closed PAPER trades (runners
included). Real money stays governed by EXECUTION_ENABLED etc.; nothing here
places an order. Pace is a gauge of what the target needs, not a promise.
"""
import math
import os
import time
from typing import Dict, Optional

import state

BOOKS_KEY = "books_v1"

# id -> defaults. seed/target/days overridable via env <ID>_SEED_USD etc.
BOOKS: Dict[str, dict] = {
    "sprint": {"name": "Core 1 (Sprint 96h)", "kind": "moonshot", "seed": 100.0, "target": 10_000.0, "days": 4.0,
               "shot_pct": 0.30, "max_share": 0.45, "bank_pct": 0.20, "cashout": True},
    "core1": {"name": "Core 2", "kind": "moonshot", "seed": 100.0, "target": 125_000.0, "days": 30.0,
              "shot_pct": 0.20, "max_share": 0.35, "bank_pct": 0.25},
    "core2": {"name": "Core 3", "kind": "moonshot", "seed": 100.0, "target": 1_000_000.0, "days": 180.0,
              "shot_pct": 0.10, "max_share": 0.25, "bank_pct": 0.25},
    "compound": {"name": "Compound", "kind": "daily", "seed": 100.0, "target": None, "days": None,
                 "shot_pct": 0.50, "max_share": 0.60, "daily_goal_pct": 5.0},
    "fomo_followers": {"name": "Fomo Followers", "kind": "copy", "seed": 50.0, "target": None, "days": None,
                       "shot_pct": 0.10, "max_share": 0.25},
    "fomo_new": {"name": "Fomo New Wallets", "kind": "copy", "seed": 50.0, "target": None, "days": None,
                 "shot_pct": 0.10, "max_share": 0.25},
    "leaderboard": {"name": "Leaderboard Copy", "kind": "copy", "seed": 50.0, "target": None, "days": None,
                    "shot_pct": 0.10, "max_share": 0.25},
    "dev_list": {"name": "Dev Launch", "kind": "copy", "seed": 50.0, "target": None, "days": None,
                 "shot_pct": 0.10, "max_share": 0.25},
    # 1 Nov 2026 (Ali): isolated Solana PAPER-ONLY pattern-tracking books. Ali wants Solana launches
    # tracked (entries, peaks, rugs, 2x/10x/moonshot outcomes) to find a real filter combination later,
    # WITHOUT mixing into sprint/compound/core1/core2's live win-rate/equity while those are being
    # finalized tonight on robinhood_chain+base alone. worker_early_watch.py's act() remaps
    # chain=="solana" positions to these solana_* books instead of the shared ones -- same exit-rung
    # logic and entry scoring, fully separate ledger/equity/win-rate. Never wired into EARLY_RULES'
    # take-fractions on purpose; reuses "sprint"'s profile (see worker_early_watch.py's _PROFILE) so the
    # exit engine still has a real rule to run, it just books the trade under a separate id.
    "solana_sprint": {"name": "Solana Watch (Sprint profile)", "kind": "moonshot", "seed": 100.0, "target": 10_000.0,
                       "days": 4.0, "shot_pct": 0.30, "max_share": 0.45, "bank_pct": 0.20, "cashout": True},
    "solana_compound": {"name": "Solana Watch (Compound profile)", "kind": "daily", "seed": 100.0, "target": None,
                         "days": None, "shot_pct": 0.50, "max_share": 0.60, "daily_goal_pct": 5.0},
    "solana_core1": {"name": "Solana Watch (Core 2 profile)", "kind": "moonshot", "seed": 100.0, "target": 125_000.0,
                      "days": 30.0, "shot_pct": 0.20, "max_share": 0.35, "bank_pct": 0.25},
    "solana_core2": {"name": "Solana Watch (Core 3 profile)", "kind": "moonshot", "seed": 100.0, "target": 1_000_000.0,
                      "days": 180.0, "shot_pct": 0.10, "max_share": 0.25, "bank_pct": 0.25},
}

# Moonshot floors (fees eat anything smaller: Ali, Oct 1 2026): equity from -> min stake.
STAKE_FLOORS = ((0.0, 30.0), (300.0, 40.0), (700.0, 60.0))


def _f(name: str, default: Optional[float]) -> Optional[float]:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def cfg(book_id: str) -> dict:
    b = dict(BOOKS[book_id])
    p = book_id.upper()
    b["seed"] = _f(f"{p}_SEED_USD", b["seed"])
    if b["kind"] == "moonshot":
        b["target"] = _f(f"{p}_TARGET_USD", b["target"])
        b["days"] = _f(f"{p}_DAYS", b["days"])
        b["shot_pct"] = _f(f"{p}_SHOT_PCT", b["shot_pct"])
    b["bank_pct"] = _f(f"{p}_BANK_PCT", b.get("bank_pct", 0.30 if b["kind"] == "daily" else 0.0))
    if b["kind"] == "daily":
        b["daily_goal_pct"] = _f("COMPOUND_DAILY_GOAL_PCT", b["daily_goal_pct"])
    return b


def _rows(book_id: str) -> list:
    from executor import paper_ledger
    return paper_ledger.module_rows(book_id, include_runners=True)


CASHOUT_KEY = "books_cashouts_v1"


def cashouts(book_id: str) -> list:
    rec = state.get_value(CASHOUT_KEY)
    rec = rec if isinstance(rec, dict) else {}
    return list(rec.get(book_id) or [])


def withdrawn(book_id: str) -> float:
    return round(sum(float(c.get("amount") or 0.0) for c in cashouts(book_id)), 2)


def realized_pnl(book_id: str) -> float:
    """Paper P&L of the current cycle (cashed-out profit is subtracted: it left the book)."""
    gross = sum(float(r.get("pnl_usd") or 0.0) for r in _rows(book_id))
    return round(gross - withdrawn(book_id) - bust_offset(book_id), 2)


BUST_KEY = "books_busts_v1"


def busts(book_id: str) -> list:
    rec = state.get_value(BUST_KEY)
    rec = rec if isinstance(rec, dict) else {}
    return list(rec.get(book_id) or [])


def bust_offset(book_id: str) -> float:
    """Realized P&L the book had when it was reseeded after its MOST RECENT bust (those losses are
    history now, wiped by the reseed). 27 Oct 2026 fix: each bust's "realized_at_bust" snapshot is
    already the full cumulative gross-minus-withdrawn total at that instant, not a delta -- a book
    that busts more than once (compound busted 3x in under 3 minutes tonight) was having every past
    snapshot SUMMED together here, double- and triple-counting the same historical losses. Subtracted
    from realized_pnl below, that oversized negative offset ADDED thousands of dollars of phantom
    balance back onto the book (confirmed live: compound showed equity=$1,309.74 on a $100 seed that
    was actually bleeding money), which is what fed the $1,000+ paper stakes far beyond any tier cap.
    Only the latest bust's snapshot should ever be subtracted -- each new bust already captures all
    losses since the book began, superseding every prior snapshot, not adding to them."""
    bs = busts(book_id)
    if not bs:
        return 0.0
    latest = max(bs, key=lambda b: b.get("ts") or 0)
    return round(float(latest.get("realized_at_bust") or 0.0), 2)


def check_bust(book_id: str, now: Optional[float] = None) -> Optional[dict]:
    """A moonshot/daily book whose total value fell below BUST_BELOW_USD (default $10 of a $100 seed)
    is BUST: log it (the bust count is a headline metric -- how often does $100 die?), reseed the
    book at its seed and restart its clock so the 24h evidence run keeps producing trades.
    Paper only. Oct 13 2026: all three target books were empty 3 hours into the first run.

    26 Oct 2026: the "let the open position resolve first" guard below had no timeout. Found via
    a live state check that core1/sprint/compound each sat at exactly $0.00 for 3+ hours straight,
    each blocked by a SINGLE open "early" runner that was still legitimately priceable (not stuck/
    dead -- a real, possibly-winning position just taking a long time). Every other signal for
    that book got refused the whole time with no way to act on anything. The runner itself is left
    alone (never force-closed -- it may still be a real winner), but the book no longer waits on
    it forever: past BUST_WAIT_MINUTES (default 60) of being under the bust floor, it reseeds and
    keeps trading in parallel while the stuck runner continues riding independently."""
    if BOOKS[book_id]["kind"] not in ("moonshot", "daily"):
        return None
    c = cfg(book_id)
    if total_value(book_id) >= (_f("BUST_BELOW_USD", 10.0) or 10.0):
        return None
    now = now if now is not None else time.time()
    from executor import paper_ledger
    blockers = [p for p in paper_ledger._open().values()
                if p.get("strategy") == "early" and p.get("module") == book_id]
    if blockers:
        oldest_open = min((p.get("opened_ts") or now) for p in blockers)
        if now - oldest_open < _f("BUST_WAIT_MINUTES", 60.0) * 60.0:
            return None         # let this book's open sniper slice(s) resolve first, but not forever
    rec = state.get_value(BUST_KEY)
    rec = rec if isinstance(rec, dict) else {}
    rec.setdefault(book_id, []).append({"ts": now, "realized_at_bust": realized_pnl(book_id) + bust_offset(book_id),
                                        "value": total_value(book_id)})
    state.set_value(BUST_KEY, rec)
    starts = state.get_value(BOOKS_KEY)
    starts = starts if isinstance(starts, dict) else {}
    starts[book_id] = {"start_ts": now}
    state.set_value(BOOKS_KEY, starts)
    return rec[book_id][-1]


def force_reseed(book_id: str, now: Optional[float] = None, reason: str = "manual") -> dict:
    """Explicit, on-demand reseed to exactly this book's current seed (cfg(book_id)["seed"]),
    wiping all prior realized P&L from the running total -- the SAME mechanism check_bust() already
    uses (a bust-snapshot of realized_pnl, subtracted going forward by bust_offset()), just invoked
    directly instead of waiting for the book to fall under BUST_BELOW_USD on its own.

    31 Oct 2026 (Ali): "the only part should be left on my end is to fund the wallet ... paper
    testing should be run till I fund the wallet and let you know." Sprint and compound both carry
    real paper P&L right now (sprint +$90.44/38 trades, compound +$1,533.63/83 trades as of today) --
    Ali confirmed that balance doesn't matter while still paper-testing, but wants a clean $50 start
    the moment the wallet is funded and this goes live. Call this (via go_live.py) at that moment,
    for sprint and compound, AFTER setting EXECUTION_ENABLED/live config -- it is intentionally a
    manual, explicit action, never automatic, so a real-money run always starts from a known, clean
    $50 and nothing from the paper-testing history leaks into the live P&L figures."""
    now = now if now is not None else time.time()
    rec = state.get_value(BUST_KEY)
    rec = rec if isinstance(rec, dict) else {}
    rec.setdefault(book_id, []).append({"ts": now, "realized_at_bust": realized_pnl(book_id) + bust_offset(book_id),
                                        "value": total_value(book_id), "reason": reason})
    state.set_value(BUST_KEY, rec)
    starts = state.get_value(BOOKS_KEY)
    starts = starts if isinstance(starts, dict) else {}
    starts[book_id] = {"start_ts": now}
    state.set_value(BOOKS_KEY, starts)
    # compound's staged bank stages are derived from realized_pnl too (see compound_banked()) --
    # resetting realized_pnl to 0 via the bust snapshot above automatically resets the stage ladder
    # as well, with no separate state to clear.
    return rec[book_id][-1]


def check_cashout(book_id: str, now: Optional[float] = None) -> Optional[dict]:
    """Sprint-style books: when total value reaches the target, CASH OUT everything above the
    reseed (SPRINT_RESEED_USD, default $200), log it, restart the cycle. Ali withdraws the money."""
    c = cfg(book_id)
    if not BOOKS[book_id].get("cashout") or not c.get("target"):
        return None
    if total_value(book_id) < c["target"]:
        return None
    now = now if now is not None else time.time()
    reseed = _f(f"{book_id.upper()}_RESEED_USD", 200.0)
    amount = round(total_value(book_id) - reseed, 2)
    rec = state.get_value(CASHOUT_KEY)
    rec = rec if isinstance(rec, dict) else {}
    rec.setdefault(book_id, []).append({"ts": now, "amount": amount, "reseed": reseed})
    state.set_value(CASHOUT_KEY, rec)
    starts = state.get_value(BOOKS_KEY)
    starts = starts if isinstance(starts, dict) else {}
    starts[book_id] = {"start_ts": now}
    state.set_value(BOOKS_KEY, starts)
    return rec[book_id][-1]


def total_value(book_id: str) -> float:
    """Everything the book has made: seed + realized paper P&L (trading balance + banked vault)."""
    return max(0.0, round(cfg(book_id)["seed"] + realized_pnl(book_id), 2))


# 31 Oct 2026 (Ali): staged profit-banking for the $50->$20,000 attempt. Ali's own structure,
# verbatim from his message: "build 50 to 1,000 or $1,500 ... stage out 30%, 35% ... another run
# from that $1,000 going up to $5,000, $6,000 ... staging out 35% to 40% ... reach to 10K, staging
# out 50 to 60% ... from remaining trying to achieve [$20,000]" -- and critically, "the amount
# taken out is already contributing to the amount for $20,000" (banked money counts toward the
# goal, it is not set aside separately from it). Modeled with a bootstrap Monte Carlo against
# compound's real observed per-trade multiples (robinhood_chain+base) before implementing: an
# EQUAL time budget across the 4 stages gave p50=$228, 1.3% chance of $20k, because stage 1 (a
# 20x from $50) is the slot-starved hard part and was getting shortchanged on time. Weighting the
# 96h trade budget toward stage 1 (55/25/12/8% split) gave p50=$1,728, 5.8% chance of $20k, and a
# median of $373.56 PERMANENTLY LOCKED IN regardless of what happens after -- reported to Ali and
# approved ("Yes wire this up"). This replaces the old flat BANK_AFTER_MULT/bank_pct scheme for
# compound ONLY (sprint/core1/core2 keep that original mechanism unchanged below).
COMPOUND_STAGES = (
    # (total_value threshold the book must CLEAR to bank at this stage, fraction of total_value banked)
    (1_000.0, 0.325),
    (5_000.0, 0.375),
    (10_000.0, 0.55),
    (20_000.0, 0.0),   # final target stage: no further banking, this IS the goal
)


def _compound_total_value_raw(book_id: str) -> float:
    """compound's seed + realized P&L, WITHOUT subtracting any bank -- the undiscounted total this
    book has ever made, used only to figure out which stage line has been crossed."""
    c = cfg(book_id)
    return max(0.0, round(c["seed"] + realized_pnl(book_id), 2))


def compound_banked(book_id: str) -> float:
    """Staged version of banked() for compound: walks COMPOUND_STAGES in order, banking bank_pct of
    the total value AT THE MOMENT each threshold is first crossed (not continuously re-banking a
    percentage of ever-growing equity, which would double-bank). Stateless and idempotent: recomputed
    fresh from realized_pnl() every call, like the rest of this module -- no separate stage-tracking
    state to get out of sync. A book that has retraced below a threshold it once crossed keeps the
    bank from having crossed it (money already taken off the table stays off), since each stage bank
    amount is fixed at the threshold VALUE itself, not the book's current (possibly lower) value."""
    raw = _compound_total_value_raw(book_id)
    total_banked = 0.0
    for threshold, bank_pct in COMPOUND_STAGES:
        if raw >= threshold and bank_pct > 0:
            total_banked += threshold * bank_pct
    return round(total_banked, 2)


def banked(book_id: str) -> float:
    """Profit taken OUT of play (the vault).
    compound: staged escalating bank-out per COMPOUND_STAGES above (Ali, 31 Oct 2026).
    sprint/core1/core2/copy books: original mechanism -- once realized profit passes
    BANK_AFTER_MULT x seed (default 3x), bank_pct of the profit above that line is locked away.
    Both are stateless: derived from realized P&L, so neither ever gets out of sync."""
    if book_id == "compound":
        return compound_banked(book_id)
    c = cfg(book_id)
    line = float(os.environ.get("BANK_AFTER_MULT", "3")) * c["seed"]
    return round(max(0.0, (c.get("bank_pct") or 0.0) * (realized_pnl(book_id) - line)), 2)


def equity(book_id: str) -> float:
    """TRADING balance (staked from): total value minus the banked vault."""
    return max(0.0, round(total_value(book_id) - banked(book_id), 2))


def _start(book_id: str, now: float) -> float:
    rec = state.get_value(BOOKS_KEY)
    rec = rec if isinstance(rec, dict) else {}
    if book_id not in rec:
        rec[book_id] = {"start_ts": now}
        state.set_value(BOOKS_KEY, rec)
    return float(rec[book_id]["start_ts"])


def pace(book_id: str, now: Optional[float] = None, eq: Optional[float] = None) -> dict:
    """Target progress for a book that has one: path_now, on_pace, needed daily growth."""
    now = now if now is not None else time.time()
    c = cfg(book_id)
    eq = total_value(book_id) if eq is None else eq      # progress counts banked profit too
    if not c.get("target") or not c.get("days"):
        return {"has_target": False}
    start_ts = _start(book_id, now)
    elapsed = max(0.0, (now - start_ts) / 86400.0)
    seed, target, days = c["seed"], c["target"], c["days"]
    frac = min(1.0, elapsed / days)
    path_now = seed * (target / seed) ** frac
    left = max(days - elapsed, 1.0)
    need = (target / max(eq, 1e-9)) ** (1.0 / left) - 1.0 if eq > 0 else None
    return {"has_target": True, "target": target, "days": days, "elapsed_days": round(elapsed, 1),
            "days_left": round(max(0.0, days - elapsed), 1), "path_now": round(path_now, 2),
            "ratio_to_path": round(eq / path_now, 3) if path_now else None,
            "on_pace": eq >= path_now, "pct_of_target": round(100.0 * eq / target, 4),
            "needed_daily_growth_pct": round(100.0 * need, 2) if need is not None else None,
            "expired": elapsed >= days and eq < target}


def max_open(book_id: str, eq: Optional[float] = None) -> int:
    """Moonshot books take MORE small shots (lottery odds need shots on goal);
    other books stay tighter.

    1 Nov 2026 (Ali): real overnight data showed compound genuinely stuck -- 5 legitimate open
    positions (all actively managed, unpriced_cycles=0, rungs firing correctly, none stuck or
    silently failing) against a cap of only 2 (+2 strong-signal extra = 4) at its ~$700 equity
    band, so it refused every new entry for hours while 5 real winners were still riding.
    Root cause: compound's own EARLY_RULES exit design (wide hard_stop/trail_giveback, see
    paper_ledger.py) deliberately holds winners 11-21+ hours -- the position-count cap was never
    sized for how long compound's own exits are designed to hold a trade. First fix (same night):
    gave "daily"-kind books the same wider step ladder moonshot books get.

    2 Nov 2026 (Ali audit): that step ladder fix was NOT ENOUGH -- compound went silent a SECOND
    time overnight. Real data: equity $2,558 (still under the $3,000 step boundary) with 8 real
    open positions (all green or near-flat, none stuck), but the step ladder only allowed 4 (+2
    strong = 6) at that equity band. $2,036 of free capital (80% of equity) sat idle behind a pure
    position-COUNT ceiling while real signals kept getting refused. A static step ladder will
    always eventually lag a book whose equity is compounding this fast -- any fixed band gets
    outgrown before the next threshold arrives. Real fix: for "daily"-kind books (compound), size
    the cap off actual committed capital instead of a step count -- allow as many positions as fit
    under max_share of equity at this book's typical per-position stake, with a floor of 4 so a
    small/early book isn't starved before it has meaningful capital. "moonshot" books (sprint/
    core1/core2) keep the step ladder unchanged -- their stake sizes are small and roughly fixed,
    so the old count-based limit was never the bottleneck for them (confirmed: they are NOT
    showing this self-starvation pattern, only compound is)."""
    eq = equity(book_id) if eq is None else eq
    c = cfg(book_id)
    if c["kind"] == "daily":
        # Capital-based cap (2 Nov 2026): how many of this book's REAL, currently-committed average
        # stake sizes fit under max_share of equity -- using stake_usd()'s actual return (which
        # already applies every real cap: hard_cap=seed*3, max_share, free_equity, pace adjustment),
        # not a re-derived formula. A re-derivation using shot_pct alone was tried first and was
        # wrong -- stake_usd()'s hard_cap (seed*MAX_STAKE_SEED_MULT) dominates for any book whose
        # equity has grown past a few multiples of its seed, so the real typical stake is far
        # smaller than shot_pct*equity suggests. Falls back to committed_usd()/open_count() (the
        # book's own actual average open stake) when fresh stake_usd() returns 0 (e.g. book is
        # fully committed right now) so the cap still reflects reality instead of guessing.
        n_open_now = open_count(book_id)
        committed = committed_usd(book_id)
        avg_open_stake = (committed / n_open_now) if n_open_now else 0.0
        # Use the book's own REAL average open stake as the "typical" size, not a freshly computed
        # max-sized next stake (stake_usd() often returns the hard_cap, which overstates the size
        # of a typical position and under-counts real capacity). Falls back to stake_usd() only
        # when nothing is open yet (avg_open_stake is undefined with zero positions).
        typical_stake = max(avg_open_stake or stake_usd(book_id), 5.0)
        capacity = int((eq * c.get("max_share", 0.6)) / typical_stake) if typical_stake else 4
        return max(4, capacity)
    steps = (3, 4, 5, 6) if c["kind"] == "moonshot" else (1, 2, 3, 4)
    return steps[0] if eq < 300 else steps[1] if eq < 3_000 else steps[2] if eq < 30_000 else steps[3]


def committed_usd(book_id: str) -> float:
    """Cost of this book's open (not yet closed) paper positions still at risk."""
    from executor import paper_ledger
    return round(paper_ledger.module_open_cost(book_id), 2)


def free_equity(book_id: str) -> float:
    return max(0.0, round(equity(book_id) - committed_usd(book_id), 2))


def open_count(book_id: str) -> int:
    from executor import paper_ledger
    return paper_ledger.module_open_count(book_id)


def stake_usd(book_id: str, now: Optional[float] = None) -> float:
    """Pace-aware stake for the next entry in this book.
    base = shot_pct of equity, lifted to the fee floor; behind the target path
    by >25% -> x1.25, ahead by >50% -> x0.8 (bank the lead); capped at max_share
    of equity. Returns 0 when the book is broke."""
    c = cfg(book_id)
    eq = equity(book_id)
    if eq < 5.0:
        return 0.0
    base = eq * c["shot_pct"]
    if c["kind"] == "moonshot":
        floor = 0.0
        for lo, usd in STAKE_FLOORS:
            if eq >= lo:
                floor = usd
        base = max(base, min(floor, eq * c["max_share"]))
        p = pace(book_id, now)
        r = p.get("ratio_to_path")
        if r is not None and r < 0.75:
            base *= 1.25
        elif r is not None and r > 1.5:
            base *= 0.8
    out = min(base, eq * c["max_share"], free_equity(book_id))
    # 27 Oct 2026: absolute backstop, independent of the equity math above. Every cap until now was
    # RELATIVE to equity() -- exactly the single point of failure a now-fixed accounting bug exploited
    # (a phantom equity reading let a single $100-seed book size a $1,289 stake, all caps "working
    # correctly" relative to the wrong number). This ceiling is pinned to the book's actual SEED, not
    # its current (possibly wrong) equity, so no future bug anywhere upstream of this line can ever
    # again produce a single stake many times the book's real size.
    hard_cap = _f(f"{book_id.upper()}_MAX_STAKE_USD", None) or c["seed"] * _f("MAX_STAKE_SEED_MULT", 3.0)
    out = min(out, hard_cap)
    return round(max(0.0, out), 2) if out >= 5.0 else 0.0


# ---- conviction tiers (Oct 13 2026) -------------------------------------------------------------
# Stake = tier % of the book's trading balance, scaled per book so the 4-day sprint is the most
# aggressive and the 180-day book the calmest, then pace-adjusted like stake_usd(). Hard caps: the tier
# max, free cash, and a fraction of the coin's own market cap so the fill stays realistic.
TIER_PCT = {"probe": 0.15, "standard": 0.40, "conviction": 0.60}
TIER_ORDER = ("probe", "standard", "conviction")
TIER_BOOK_SCALE = {"sprint": 1.0, "core1": 0.8, "core2": 0.6}
TIER_MAX_SHARE = 0.80


def loss_streak(book_id: str) -> int:
    """Consecutive losing closes (newest first) for this book's sniper positions."""
    from executor import paper_ledger
    rows = [r for r in paper_ledger._closed() if (r.get("module") or "") == book_id and r.get("strategy") == "early"]
    rows.sort(key=lambda r: r.get("closed_ts") or r.get("opened_ts") or 0, reverse=True)
    n = 0
    for r in rows:
        if float(r.get("pnl_usd") or 0) < 0:
            n += 1
        else:
            break
    return n


def tier_after_brake(tier: str, book_id: str) -> str:
    """Losing-streak brake: every 3 straight losses drops one tier until a win resets it."""
    i = TIER_ORDER.index(tier) if tier in TIER_ORDER else 0
    return TIER_ORDER[max(0, i - loss_streak(book_id) // 3)]


def tier_stake_usd(book_id: str, tier: str, mcap: Optional[float] = None, now: Optional[float] = None) -> float:
    c = cfg(book_id)
    eq = equity(book_id)
    if eq < 5.0 or c["kind"] != "moonshot" or book_id not in TIER_BOOK_SCALE:
        return stake_usd(book_id, now)
    tier = tier_after_brake(tier, book_id)
    base = eq * TIER_PCT[tier] * TIER_BOOK_SCALE[book_id]
    r = pace(book_id, now).get("ratio_to_path")
    if r is not None and r < 0.75:
        base *= 1.25
    elif r is not None and r > 1.5:
        base *= 0.8
    cap_mc = _f("TIER_MAX_MCAP_FRAC", 0.02) * mcap if mcap else float("inf")
    out = min(base, eq * TIER_MAX_SHARE, free_equity(book_id), cap_mc)
    # 27 Oct 2026: same absolute backstop as stake_usd() below, pinned to the book's real seed rather
    # than its (possibly corrupted) current equity -- see that comment for the full reasoning. This is
    # the path the $1,289.59 oversized stake actually went through; every cap above it was relative to
    # equity() and all of them "passed" because equity() itself was wrong.
    hard_cap = _f(f"{book_id.upper()}_MAX_STAKE_USD", None) or c["seed"] * _f("MAX_STAKE_SEED_MULT", 3.0)
    out = min(out, hard_cap)
    return round(out, 2) if out >= 5.0 else 0.0


def can_open(book_id: str, strong: bool = False) -> tuple:
    """strong=True (a Standard/Conviction signal with >=5 confirmations) may use BOOK_STRONG_EXTRA_SLOTS (default 2)
    beyond the normal position limit, and when even those are full may swap out the stalest flat position
    (paper_ledger.swap_out) -- at most one swap per book every 5 minutes."""
    check_cashout(book_id)
    check_bust(book_id)
    eq = equity(book_id)
    if eq < 5.0:
        return False, f"{book_id} book is empty (${eq:.2f})"
    limit = max_open(book_id, eq)
    extra = int(_f("BOOK_STRONG_EXTRA_SLOTS", 2) or 0) if strong else 0
    n_open = open_count(book_id)
    need_cash = free_equity(book_id) < 5.0
    if n_open >= limit + extra or (need_cash and strong):
        if strong and _try_swap(book_id):
            n_open, need_cash = open_count(book_id), free_equity(book_id) < 5.0
        if n_open >= limit + extra:
            return False, f"{book_id}: {limit} open position(s) is the limit at ${eq:,.0f}" + (f" (+{extra} for strong signals, all used)" if extra else "")
    if need_cash:
        return False, f"{book_id}: balance is fully committed (${committed_usd(book_id):,.0f} open)"
    return True, "ok" if n_open < limit else "ok (strong-signal extra slot)"


def _try_swap(book_id: str, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    key = f"swap_last:{book_id}"
    last = state.get_value(key)
    if isinstance(last, (int, float)) and now - last < 300:
        return False
    from executor import paper_ledger
    row = paper_ledger.swap_out(book_id, now)
    if row:
        state.set_value(key, now)
        return True
    return False


def daily_pnl(book_id: str, now: Optional[float] = None) -> float:
    now = now if now is not None else time.time()
    day0 = now - (now % 86400)
    return round(sum(float(r.get("pnl_usd") or 0.0) for r in _rows(book_id) if (r.get("closed_ts") or 0) >= day0), 2)


def status(book_id: str, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    check_cashout(book_id, now)
    check_bust(book_id, now)
    c = cfg(book_id)
    eq = equity(book_id)
    out = {"busts": len(busts(book_id)), "id": book_id, "name": c["name"], "kind": c["kind"], "seed": c["seed"], "equity": eq,
           "banked": banked(book_id), "cashed_out": withdrawn(book_id), "cashouts": cashouts(book_id), "total_value": total_value(book_id),
           "realized_pnl": realized_pnl(book_id), "open": open_count(book_id),
           "stake_next": stake_usd(book_id, now), "max_open": max_open(book_id, eq)}
    out.update(pace(book_id, now))
    if c["kind"] == "daily":
        goal = round(eq * c["daily_goal_pct"] / 100.0, 2)
        today = daily_pnl(book_id, now)
        out.update({"daily_goal_usd": goal, "daily_goal_pct": c["daily_goal_pct"], "today_pnl": today,
                    "goal_hit": today >= goal})
    return out


def line(book_id: str) -> str:
    s = status(book_id)
    if s.get("has_target"):
        return (f"{s['name']}: ${s['total_value']:,.2f} (vault ${s['banked']:,.2f}) of ${s['target']:,.0f} (day {s['elapsed_days']:.0f}/{s['days']:.0f}, "
                f"{'ahead of' if s['on_pace'] else 'behind'} path ${s['path_now']:,.2f}, needs +{s['needed_daily_growth_pct']}%/day)")
    if s["kind"] == "daily":
        return f"{s['name']}: ${s['equity']:,.2f}, today {s['today_pnl']:+.2f} vs goal {s['daily_goal_usd']:+.2f}"
    return f"{s['name']}: ${s['equity']:,.2f} (P&L {s['realized_pnl']:+.2f})"

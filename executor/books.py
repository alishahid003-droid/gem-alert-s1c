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
    # 7 Nov 2026 (Ali): sprint's 4-day target raised 10k -> 20k, as part of differentiating
    # sprint/core1/core2's exit behavior (see EARLY_RULES/EARLY_TRAIL_RATCHET in paper_ledger.py) --
    # previously all three shared an identical exit ladder despite having different targets/horizons.
    "sprint": {"name": "Core 1 (Sprint 96h)", "kind": "moonshot", "seed": 100.0, "target": 20_000.0, "days": 4.0,
               "shot_pct": 0.30, "max_share": 0.45, "bank_pct": 0.20, "cashout": True},
    "core1": {"name": "Core 2", "kind": "moonshot", "seed": 100.0, "target": 125_000.0, "days": 30.0,
              "shot_pct": 0.20, "max_share": 0.35, "bank_pct": 0.25},
    "core2": {"name": "Core 3", "kind": "moonshot", "seed": 100.0, "target": 1_000_000.0, "days": 180.0,
              "shot_pct": 0.10, "max_share": 0.25, "bank_pct": 0.25},
    # 8 Nov 2026 (Ali): real audit found compound's live equity ($298.53) with committed capital
    # already OVER its own 60%-of-equity cap (6 real open positions vs max_open=4), causing every
    # new signal to be refused as "balance fully committed" -- not a bug, a genuine capacity wall.
    # Raised max_share 0.60 -> 0.75 (still leaves 25% headroom, same direction as the 1 Nov/2 Nov
    # max_open fixes above) so capacity scales correctly as equity grows; see max_open()'s own
    # floor bump below for what actually fixes TODAY's real number.
    "compound": {"name": "Compound", "kind": "daily", "seed": 100.0, "target": None, "days": None,
                 "shot_pct": 0.50, "max_share": 0.75, "daily_goal_pct": 5.0},
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


DEPOSITS_KEY = "books_deposits_v1"


def deposits(book_id: str) -> list:
    """Capital credited INTO a book from outside its own trading -- currently only
    check_compound_split()'s automatic $200 distribution (3 Nov 2026, Ali), but kept generic
    (not named after that one source) in case a future manual top-up ever needs the same ledger.
    Mirrors cashouts()/CASHOUT_KEY's shape exactly, just the opposite direction."""
    rec = state.get_value(DEPOSITS_KEY)
    rec = rec if isinstance(rec, dict) else {}
    return list(rec.get(book_id) or [])


def deposited(book_id: str) -> float:
    return round(sum(float(d.get("amount") or 0.0) for d in deposits(book_id)), 2)


def realized_pnl(book_id: str) -> float:
    """Paper P&L of the current cycle (cashed-out profit is subtracted: it left the book;
    deposited-in capital is added: it's new seed money, not trading profit, but it must still
    show up in total_value() the same way a reseed's seed does)."""
    gross = sum(float(r.get("pnl_usd") or 0.0) for r in _rows(book_id))
    return round(gross - withdrawn(book_id) + deposited(book_id) - bust_offset(book_id), 2)


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
    # 6 Nov 2026: realized_at_bust must be computed AND both keys written inside one lock --
    # computing it, then reading/updating BUST_KEY/BOOKS_KEY outside a lock (even via one combined
    # write) let a concurrent writer's own read-modify-write clobber this one, the same lost-update
    # pattern found and fixed in paper_ledger.py's close paths (see state.atomic_multi_fn()).
    snapshot_value = realized_pnl(book_id) + bust_offset(book_id)
    snapshot_total = total_value(book_id)
    def _apply(cur, _book_id=book_id, _now=now, _rab=snapshot_value, _val=snapshot_total):
        rec = cur.get(BUST_KEY)
        rec = dict(rec) if isinstance(rec, dict) else {}
        rec[_book_id] = list(rec.get(_book_id) or []) + [{"ts": _now, "realized_at_bust": _rab, "value": _val}]
        starts = cur.get(BOOKS_KEY)
        starts = dict(starts) if isinstance(starts, dict) else {}
        starts[_book_id] = {"start_ts": _now}
        return {BUST_KEY: rec, BOOKS_KEY: starts}
    result = state.atomic_multi_fn([BUST_KEY, BOOKS_KEY], _apply)
    return result[BUST_KEY][book_id][-1]


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
    # 6 Nov 2026: same full read-modify-write-under-lock fix as check_bust() above.
    snapshot_value = realized_pnl(book_id) + bust_offset(book_id)
    snapshot_total = total_value(book_id)
    def _apply(cur, _book_id=book_id, _now=now, _rab=snapshot_value, _val=snapshot_total, _reason=reason):
        rec = cur.get(BUST_KEY)
        rec = dict(rec) if isinstance(rec, dict) else {}
        rec[_book_id] = list(rec.get(_book_id) or []) + [
            {"ts": _now, "realized_at_bust": _rab, "value": _val, "reason": _reason}]
        starts = cur.get(BOOKS_KEY)
        starts = dict(starts) if isinstance(starts, dict) else {}
        starts[_book_id] = {"start_ts": _now}
        return {BUST_KEY: rec, BOOKS_KEY: starts}
    result = state.atomic_multi_fn([BUST_KEY, BOOKS_KEY], _apply)
    # compound's staged bank stages are derived from realized_pnl too (see compound_banked()) --
    # resetting realized_pnl to 0 via the bust snapshot above automatically resets the stage ladder
    # as well, with no separate state to clear.
    return result[BUST_KEY][book_id][-1]


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
    # 6 Nov 2026: same full read-modify-write-under-lock fix as check_bust() above.
    def _apply(cur, _book_id=book_id, _now=now, _amount=amount, _reseed=reseed):
        rec = cur.get(CASHOUT_KEY)
        rec = dict(rec) if isinstance(rec, dict) else {}
        rec[_book_id] = list(rec.get(_book_id) or []) + [{"ts": _now, "amount": _amount, "reseed": _reseed}]
        starts = cur.get(BOOKS_KEY)
        starts = dict(starts) if isinstance(starts, dict) else {}
        starts[_book_id] = {"start_ts": _now}
        return {CASHOUT_KEY: rec, BOOKS_KEY: starts}
    result = state.atomic_multi_fn([CASHOUT_KEY, BOOKS_KEY], _apply)
    return result[CASHOUT_KEY][book_id][-1]


# 13 Nov 2026 (Ali, NEW cascade, corrected version -- replaces the 11 Nov
# check_sprint_compound_cascade() $200-single-source/$200-to-core1+core2 version above, which
# is itself now fully removed, not just made a no-op, per Ali's explicit instruction ("you
# have to remove both of them altogether"). Ali's exact words (13 Nov 2026, confirmed via
# explicit clarifying question after an earlier drafting pass got the recipients wrong):
# "Compound alone funds it; Sprint, Core1, Core2 each get $100." This matches Ali's real
# funding plan -- Compound goes live FIRST, alone, with Sprint/Core1/Core2 all still
# unfunded/paper-only, and this cascade is what brings all three of them online with real
# seed money the first time Compound's OWN banked vault proves itself out.
#
# Mechanics: compound's own banked() (the real bank-vault total -- NOT combined with sprint,
# since sprint has no funded capital to bank anything from yet at this point in the plan) is
# checked every cycle. The first time it reaches SC_CASCADE_TRIGGER_USD ($500-600), $300 is
# pulled OUT of compound and split evenly 3 ways -- $100 each to sprint, core1, and core2 --
# as fresh deposited-in seed money. Fires at most once across the system's lifetime (guarded
# by SC_CASCADE_KEY). Once seeded, sprint/core1/core2 trade under the exact same stake_usd()/
# pool-borrowing rules as any other seeded book, nothing special-cased. Core1/core2 then run
# fully independently of the phase-target engine (their win rate and equity never feed back
# into _combined_equity_for_milestones(), which stays scoped to PHASE_ENGINE_BOOKS =
# compound+sprint only). Sprint, once seeded by this cascade, DOES join compound in chasing
# the phase targets -- that's the PHASE_ENGINE_BOOKS scope, unchanged by this cascade.
SC_CASCADE_KEY = "sprint_compound_cascade_v1"
SC_CASCADE_TRIGGER_USD = 500.0     # compound's OWN banked() crossing this fires the cascade
SC_CASCADE_BANK_USD = 300.0        # amount pulled out of compound and split 3 ways
SC_CASCADE_FUNDER = "compound"     # compound alone funds this -- never sprint, never combined
SC_CASCADE_RECIPIENTS = ("sprint", "core1", "core2")  # even three-way split, $100 each
COMPOUND_SPLIT_RECIPIENTS = ()  # RETIRED 11 Nov 2026 -- kept as empty tuple, see can_open() below


def check_sprint_compound_cascade(now: Optional[float] = None) -> Optional[dict]:
    """13 Nov 2026 (Ali, NEW, corrected): fires ONCE, the first time COMPOUND's own banked()
    (profit already taken off the table via its own ladder/stage mechanism) reaches
    SC_CASCADE_TRIGGER_USD ($500). Pulls SC_CASCADE_BANK_USD ($300) entirely out of compound
    (compound alone funds this -- there is no proportional split with sprint, since sprint has
    no banked profit of its own to contribute at this stage of the plan) and credits sprint,
    core1, and core2 $100 each as fresh deposited-in seed money via deposits()/DEPOSITS_KEY.
    They then trade under the exact same stake_usd()/pool-borrowing rules as any other seeded
    book, nothing special-cased. Core1/core2 run fully independently from here on -- their win
    rate and equity never feed into the phase-trigger math. Sprint, once seeded, joins compound
    in chasing the phase targets (PHASE_ENGINE_BOOKS already covers compound+sprint, unchanged).

    Guarded by SC_CASCADE_KEY so it can never fire twice even if compound's banked total dips
    and re-crosses $500 later.

    Call once per cycle for compound (can_open() wires this in, same call site the 11 Nov
    version used). REPLACES the retired 11 Nov check_sprint_compound_cascade() ($200 single-
    source, either compound-or-sprint -> $100/$100 core1+core2) -- fully removed per Ali's
    explicit instruction, not left as a no-op -- and the already-retired-as-no-ops 3 Nov
    check_compound_split() and 8 Nov _maybe_fire_phase1_cascade(), both further below, kept
    for history only.

    LIVE-ONLY GATE (unchanged from 11 Nov, Ali never asked to change this part): "this should
    only fire once we deploy live money, not on the paper testing any further." Checks
    _live_books_from_env() -- fires only once compound (the sole funder) is listed in
    LIVE_BOOKS. In today's paper-only run (LIVE_BOOKS unset/empty), this function is a safe
    no-op on every call -- paper trading continues completely undisturbed. The guard
    (SC_CASCADE_KEY) is only set once it actually fires with compound live, so a paper-only
    run crossing $500 banked repeatedly never consumes the one-time trigger."""
    now = now if now is not None else time.time()
    guard = state.get_value(SC_CASCADE_KEY)
    if isinstance(guard, dict) and guard.get("fired"):
        return None

    live_books = _live_books_from_env()
    if SC_CASCADE_FUNDER not in live_books:
        return None  # compound not yet live-armed -- never fires on paper alone

    if banked(SC_CASCADE_FUNDER) < SC_CASCADE_TRIGGER_USD:
        return None

    share = round(SC_CASCADE_BANK_USD / len(SC_CASCADE_RECIPIENTS), 2)
    shares = {b: share for b in SC_CASCADE_RECIPIENTS}
    leftover = round(SC_CASCADE_BANK_USD - sum(shares.values()), 2)
    if leftover:
        shares[SC_CASCADE_RECIPIENTS[0]] = round(shares[SC_CASCADE_RECIPIENTS[0]] + leftover, 2)

    def _apply(cur, _now=now, _shares=shares):
        done_guard = cur.get(SC_CASCADE_KEY)
        if isinstance(done_guard, dict) and done_guard.get("fired"):
            # Concurrent call already fired this inside the lock -- bail out, no double-credit.
            return {CASHOUT_KEY: cur.get(CASHOUT_KEY), DEPOSITS_KEY: cur.get(DEPOSITS_KEY),
                    SC_CASCADE_KEY: done_guard}
        rec = cur.get(CASHOUT_KEY)
        rec = dict(rec) if isinstance(rec, dict) else {}
        rec[SC_CASCADE_FUNDER] = list(rec.get(SC_CASCADE_FUNDER) or []) + [
            {"ts": _now, "amount": SC_CASCADE_BANK_USD, "reason": "sprint_compound_cascade_13nov2026"}]
        dep = cur.get(DEPOSITS_KEY)
        dep = dict(dep) if isinstance(dep, dict) else {}
        for _book_id, _amount in _shares.items():
            dep[_book_id] = list(dep.get(_book_id) or []) + [
                {"ts": _now, "amount": _amount, "reason": "sprint_compound_cascade_13nov2026"}]
        cascade_result = {"ts": _now, "fired": True, "funder": SC_CASCADE_FUNDER,
                           "cascaded_usd": SC_CASCADE_BANK_USD, "shares": _shares}
        return {CASHOUT_KEY: rec, DEPOSITS_KEY: dep, SC_CASCADE_KEY: cascade_result}

    result = state.atomic_multi_fn([CASHOUT_KEY, DEPOSITS_KEY, SC_CASCADE_KEY], _apply)
    return result[SC_CASCADE_KEY]


def check_compound_split(now: Optional[float] = None) -> Optional[dict]:
    """RETIRED 11 Nov 2026 (Ali) -- superseded by check_sprint_compound_cascade() above. This
    was the original 3 Nov 2026 mechanism: "from $100 to $500 once it reaches, it should
    distribute $200 to the three modules ... make his own one from the remaining $300 ... those
    modules start operating with the $200, which is split amongst the three ... same very logic
    that if someone's capital is sitting idle ... it can pick up that spare capital as well" --
    i.e. the $200 becomes each recipient's normal seed (via deposits()/deposited(), see above),
    so they trade under the exact same stake_usd()/pool-borrowing rules as any other seeded
    book, nothing special-cased. Kept as a permanent no-op (never fires) rather than deleted,
    so the existing test file and historical references above still resolve.

    Fires ONCE, automatically, the first time compound's total_value() crosses
    COMPOUND_SPLIT_TRIGGER_USD ($500) -- guarded by COMPOUND_SPLIT_KEY so it can never fire twice
    even if compound dips back under $500 later and re-crosses. Call this every cycle alongside
    the existing check_cashout()/check_bust() calls (can_open() already does, see below).

    Pulls COMPOUND_SPLIT_BANK_USD ($200) out of compound the same way check_cashout() pulls a
    cashout -- a CASHOUT_KEY record, so compound's own total_value() drops by exactly $200,
    leaving it the real remaining ~$300 (not a fixed $300: compound keeps whatever it actually
    had over $500, minus the flat $200 -- if it reaches $540, it keeps $340, not $300)."""
    return None  # RETIRED 11 Nov 2026 -- dead code below kept only for historical reference
    done = state.get_value(COMPOUND_SPLIT_KEY)
    if isinstance(done, dict) and done.get("fired"):
        return None   # already split once -- never again
    if total_value("compound") < COMPOUND_SPLIT_TRIGGER_USD:
        return None
    now = now if now is not None else time.time()

    # Distribute it evenly across sprint/core1/core2 as fresh deposited-in seed money.
    share = round(COMPOUND_SPLIT_BANK_USD / len(COMPOUND_SPLIT_RECIPIENTS), 2)
    shares = {b: share for b in COMPOUND_SPLIT_RECIPIENTS}
    # Rounding: three-way split of $200 is $66.67/$66.67/$66.66 -- give the last cent(s) to the
    # first recipient so the three shares sum to exactly $200.00, not $199.99 or $200.01.
    leftover = round(COMPOUND_SPLIT_BANK_USD - sum(shares.values()), 2)
    if leftover:
        shares[COMPOUND_SPLIT_RECIPIENTS[0]] = round(shares[COMPOUND_SPLIT_RECIPIENTS[0]] + leftover, 2)

    # 6 Nov 2026: CASHOUT_KEY, DEPOSITS_KEY, AND the COMPOUND_SPLIT_KEY fire-once guard must all be
    # read, modified, and written as one atomic unit under one lock -- the old separate-writes
    # version (even after the first atomic_multi_update pass) could let a concurrent caller see the
    # $200 pulled from compound without it yet being credited to sprint/core1/core2, or worse, let
    # this fire more than once if the guard write landed separately from the money-moving writes.
    def _apply(cur, _now=now, _shares=shares):
        rec = cur.get(CASHOUT_KEY)
        rec = dict(rec) if isinstance(rec, dict) else {}
        rec["compound"] = list(rec.get("compound") or []) + [
            {"ts": _now, "amount": COMPOUND_SPLIT_BANK_USD, "reason": "compound_split_3nov2026"}]
        dep = cur.get(DEPOSITS_KEY)
        dep = dict(dep) if isinstance(dep, dict) else {}
        for _book_id, _amount in _shares.items():
            dep[_book_id] = list(dep.get(_book_id) or []) + [
                {"ts": _now, "amount": _amount, "reason": "compound_split_3nov2026"}]
        done_guard = cur.get(COMPOUND_SPLIT_KEY)
        if isinstance(done_guard, dict) and done_guard.get("fired"):
            # Someone else's concurrent call already fired this inside the lock -- bail out with
            # no changes rather than double-crediting the $200.
            return {CASHOUT_KEY: cur.get(CASHOUT_KEY), DEPOSITS_KEY: cur.get(DEPOSITS_KEY),
                    COMPOUND_SPLIT_KEY: done_guard}
        split_result = {"ts": _now, "fired": True, "banked_from_compound": COMPOUND_SPLIT_BANK_USD,
                         "shares": _shares, "compound_remaining": None}  # filled in below, outside lock
        return {CASHOUT_KEY: rec, DEPOSITS_KEY: dep, COMPOUND_SPLIT_KEY: split_result}

    result = state.atomic_multi_fn([CASHOUT_KEY, DEPOSITS_KEY, COMPOUND_SPLIT_KEY], _apply)
    final = result[COMPOUND_SPLIT_KEY]
    if final.get("compound_remaining") is None and final.get("fired"):
        # compound_remaining is informational only (not used by any guard logic) -- safe to compute
        # just after the lock releases, from the now-updated real total_value().
        final["compound_remaining"] = total_value("compound")
    return final

# ---------------------------------------------------------------------------
# Milestone ladder v2 (Ali, 5 Nov 2026, revised same day): two hard, sequential real-world
# targets, not one open-ended ladder -- "$20,000 is the main lever needed in [the next] 4 to
# 5 [days] ... even if the remaining balance is left around $700, $800 for the next run ...
# more likely it can achieve $117,000 to $118,000 ... we need to secure the profits as well ...
# I don't want to be sitting blank in between."
#
# PHASE 1 (toward the $20,000 trigger): light protective banking on the way up -- SMALLER %
# than the old v1 ladder (10-20% vs 15-50%) so climbing toward the trigger isn't slowed much by
# its own banking, while still making sure a drawdown after a good run can't erase everything
# ("not sitting blank"). The FINAL step, once combined equity reaches PHASE1_CASHOUT_TRIGGER_USD
# ($20,000), is NOT a percentage -- it's a hard, fixed cash-out of PHASE1_CASHOUT_USD ($19,000,
# revised 5 Nov 2026 same day from an earlier $20,000/$750 split -- Ali: "if we achieve $20,000,
# we will cash out $19,000, and the remaining will stay in the system"), leaving PHASE1_RESEED_USD
# ($1,000) behind to keep trading, or the real overshoot above $20,000 if equity cleared the
# trigger before this check ran. This mirrors check_cashout()'s own fixed-reseed pattern, just
# measured on combined 4-book equity instead of one book.
#
# PHASE 2 (toward $117,000-118,000) starts automatically the moment Phase 1's $20,000 cash-out
# fires, using the SAME protective-banking idea, scaled off the $750 reseed instead of the
# original $120 start -- so "securing profits along the way" continues for the second leg too,
# not just the first.
MILESTONE_LADDER_KEY = "milestone_ladder_v2"

# 8 Nov 2026 (Ali, Phase-1-ladder cascade): once the Phase 1 ladder has cumulatively banked
# roughly $600-700 on the way up (the real rung at combined-equity=$2,500 banks 18% = $450,
# bringing the running total to $727 -- the first rung that clears Ali's stated "$600, $700
# plus" threshold), peel off a FIXED $300 from that banked total and credit it as fresh
# deposited-in seed money to sprint/core1/core2 ($100 each), via the same deposits()/
# DEPOSITS_KEY mechanism check_compound_split() already uses for its $200 split. Fires EXACTLY
# ONCE across the whole system's lifetime (guarded by PHASE1_CASCADE_KEY), regardless of how
# much more gets banked later in Phase 1-5 -- Ali's exact words: "only the first one can be
# given to them... remaining bank amount do not need to move it over there."
PHASE1_CASCADE_KEY = "phase1_ladder_cascade_v1"
PHASE1_CASCADE_MIN_BANKED_USD = 600.0   # fires once cumulative Phase-1 ladder banking clears this
PHASE1_CASCADE_AMOUNT_USD = 300.0       # fixed amount peeled off and cascaded
PHASE1_CASCADE_RECIPIENTS = ("sprint", "core1", "core2")

# 8 Nov 2026 (Ali, live-wiring build): which books are allowed to fire REAL-money buys/sells
# through executor.swap_executor, read fresh from the environment on every check (no restart
# needed to re-arm/disarm -- same reasoning as EXECUTION_ENABLED itself being read live rather
# than cached). Comma-separated book ids, e.g. "compound" or "compound,sprint,core1,core2".
# Deliberately independent of EXECUTION_ENABLED/the per-chain key check in
# executor.config.ready_for_chain() -- THIS only decides which BOOK is allowed to go live; the
# chain-level gate still decides whether the wallet/key/kill-switch allow it at all. Both must
# pass. An unknown/misspelled book id in this list is simply never armed (never raises) --
# fails closed, consistent with every other gate in this file.
LIVE_BOOKS_ENV = "LIVE_BOOKS"

# Set to True automatically the instant the Phase-1 cascade fires (see
# _maybe_fire_phase1_cascade below) -- Ali's explicit requirement (8 Nov 2026 AskUserQuestion):
# "Auto-live, but notify you first with a pause window" + "No timeout -- stays paused until you
# explicitly say go". This pause applies ONLY to sprint/core1/core2 (the cascade recipients) --
# compound is not gated by this at all, since compound going live is controlled purely by
# LIVE_BOOKS and isn't tied to the cascade firing. Cleared only by clear_cascade_live_pause()
# below, which Ali must call himself (or ask for it to be called) once he's reviewed the
# cascade and is ready -- nothing in this codebase clears it on its own.
CASCADE_LIVE_PAUSE_KEY = "phase1_cascade_live_pause_v1"


def _live_books_from_env() -> set:
    raw = os.environ.get(LIVE_BOOKS_ENV, "").strip()
    if not raw:
        return set()
    return {b.strip().lower() for b in raw.split(",") if b.strip()}


def is_cascade_live_paused() -> bool:
    """True once the Phase-1 cascade has fired and Ali has not yet explicitly cleared the
    pause. Only meaningful for sprint/core1/core2 -- see CASCADE_LIVE_PAUSE_KEY's comment.
    The stored record's own "paused" field is the single source of truth (not just "is there
    a record at all"), since clear_cascade_live_pause() below replaces the SAME key with a
    record that has paused=False rather than deleting it -- keeping one key with a clear
    history is simpler than a delete-to-mean-cleared convention, and avoids ever treating a
    missing key (e.g. before the cascade has fired at all) as anything other than not-paused."""
    rec = state.get_value(CASCADE_LIVE_PAUSE_KEY)
    return bool(isinstance(rec, dict) and rec.get("paused"))


def clear_cascade_live_pause(reason: str = "manual_go") -> dict:
    """The ONLY way the cascade live-pause is lifted -- must be called explicitly (by Ali, or
    on his explicit instruction), never automatically and never on a timeout. Returns a record
    of when/why it was cleared, for the audit trail."""
    rec = {"paused": False, "cleared": True, "ts": time.time(), "reason": reason}
    state.set_value(CASCADE_LIVE_PAUSE_KEY, rec)
    return rec


def is_live_armed(book_id: str) -> bool:
    """True only if book_id is listed in LIVE_BOOKS AND (for sprint/core1/core2 specifically)
    the cascade live-pause is not currently set. Does NOT check EXECUTION_ENABLED or the
    chain's real key -- that is executor.config.ready_for_chain()'s job, checked separately
    and additionally by the caller before any real buy/sell is attempted. Both gates must
    pass; this function alone is never sufficient to fire a real trade."""
    book_id = (book_id or "").lower()
    if book_id not in _live_books_from_env():
        return False
    if book_id in PHASE1_CASCADE_RECIPIENTS and is_cascade_live_paused():
        return False
    return True

# 7 Nov 2026 (Ali, revised): superseded the original 2-phase ($20k -> $117,500) structure with
# a 5-phase target/cash-out ladder, each phase re-seeding the next from its own overshoot-aware
# reseed floor (same "real overshoot kept, not forced down to a flat number" principle as
# before). Phase 2 and Phase 3 deliberately share the SAME $12,000 trigger (confirmed
# intentional by Ali -- repeat the stage twice rather than one bigger phase 2). Phase 5 is the
# final target ($1,000,000): same as the old phase-2 end-state, no automatic cash-out action is
# wired for reaching it yet -- it only walks its own ladder; ask Ali what to do once it's
# actually close, rather than guessing a final cash-out number now.
#
# PHASE_DEFS[n] = {
#   "trigger": combined equity level that fires this phase's hard cash-out (None for phase 5,
#              which has no coded cash-out yet),
#   "cashout": fixed USD amount cashed out when the trigger fires (None for phase 5),
#   "reseed":  USD left behind (floor) after that cash-out, overshoot-aware like before (None
#              for phase 5),
#   "ladder":  (combined_equity_level, pct_of_current_combined_equity_to_bank) tuples, same
#              lighter-protective-banking mechanism as the original PHASE1_LADDER/PHASE2_LADDER,
#              just rescaled to each phase's own start->trigger span so the rungs land at
#              sensible checkpoints within the SHORTER climbs Ali specified.
# }
PHASE_DEFS: Dict[int, dict] = {
    # 13 Nov 2026 (Ali, NEW targets -- supersedes the 7 Nov 5-phase structure above wholesale):
    # "my first target is an immediate $3,000 arrangement... once we achieve that, I'm going to
    # cash out $2,000, and $1,000 is going to be refeed to phase 2... phase 2 target is going to
    # be $15,000... the split is going to work the same, 65-35 rule... once $15,000 is achieved,
    # $14,000 is going to be taken out and $1,000 is going to be reseeded for phase 3 target,
    # which is going to be $12,000... once phase 3 target is achieved, $10,000 is taken out,
    # $2,000 feeded for the next target of around $105,000... once that is achieved, $100,000
    # taken out, remaining reseeded to phase 5 target, $1 million." Phase 4 trigger confirmed
    # as exactly $105,000 (Ali chose this over $110,000 when asked to pin down "around 105 to
    # 110"), cashout $100,000, reseed $5,000.
    #
    # STRUCTURAL CHANGE from the 7 Nov version, also explicit (Ali, 13 Nov 2026): "to reach
    # these targets for phase one, only compound will [run]... After phase one is reached and
    # the reseeding happens of $1,000 to phase two target, that has to be split 65-35 to
    # compound and sprint. From phase two to phase five, compound and sprint both will
    # collectively work... core1/core2 work separately on their seeded capital [from the
    # cascade] and will not be included in the win percentage." So PHASE_ENGINE_BOOKS-style
    # combined-equity/65-35-split logic applies ONLY from phase 2 onward -- phase 1 is
    # compound-ALONE (see PHASE1_SOLO_BOOK below and its use in check_milestone_ladder()).
    # Ali also confirmed (explicit clarifying question, 13 Nov 2026): the cascade ($300 pulled
    # from compound's own banked() once it crosses $500-600, split $100 each to sprint/core1/
    # core2) MUST fire before phase 1's $3,000 cashout is allowed to complete -- phase 1 is not
    # "done" until sprint/core1/core2 have already been seeded by that cascade. See
    # _phase1_gate_cleared() and its use in check_milestone_ladder() below.
    #
    # Ladder rungs for every phase rescaled proportionally to each phase's NEW start->trigger
    # span (same method used when the 5-phase ladder structure was first built 7 Nov 2026,
    # confirmed with Ali as the right approach for this revision too, 13 Nov 2026) -- phase 5's
    # rungs are left effectively unchanged in absolute terms (old span $25k->$1M vs new $5k->
    # $1M converge to nearly the same checkpoints; recomputed directly off the new span rather
    # than inherited as a fraction, since the old scheme's first rung ($20k) actually sat BELOW
    # its own old start ($25k) -- a relationship that doesn't carry over as a fraction).
    1: dict(
        trigger=3_000.0, cashout=2_000.0, reseed=1_000.0,
        ladder=(
            (144.0,   0.10),
            (246.0,   0.12),
            (422.0,   0.15),
            (803.0,   0.18),
            (1_535.0, 0.20),
            (2_414.0, 0.22),
        ),
    ),
    2: dict(
        trigger=15_000.0, cashout=14_000.0, reseed=1_000.0,
        ladder=(
            (2_000.0,  0.10),
            (4_000.0,  0.12),
            (6_000.0,  0.15),
            (8_333.0,  0.18),
            (11_000.0, 0.20),
            (13_400.0, 0.22),
        ),
    ),
    3: dict(
        trigger=12_000.0, cashout=10_000.0, reseed=2_000.0,
        ladder=(
            (2_750.0,  0.10),
            (4_250.0,  0.12),
            (5_750.0,  0.15),
            (7_500.0,  0.18),
            (9_500.0,  0.20),
            (11_000.0, 0.22),
        ),
    ),
    4: dict(
        trigger=105_000.0, cashout=100_000.0, reseed=5_000.0,
        ladder=(
            (4_687.0,  0.10),
            (15_435.0, 0.12),
            (33_348.0, 0.15),
            (55_739.0, 0.18),
            (78_130.0, 0.20),
            (94_252.0, 0.22),
        ),
    ),
    5: dict(
        # Final target. No automatic cash-out wired yet -- same as the prior version, ask Ali
        # what to do once $1,000,000 is actually close.
        trigger=1_000_000.0, cashout=None, reseed=None,
        ladder=(
            (24_900.0,  0.10),
            (64_700.0,  0.12),
            (154_250.0, 0.15),
            (353_250.0, 0.18),
            (602_000.0, 0.20),
            (850_750.0, 0.22),
        ),
    ),
}

# 13 Nov 2026 (Ali, NEW): phase 1 runs on compound ALONE -- sprint is not yet seeded/running
# toward the phase targets at this stage (it only joins from phase 2 onward, once the 65/35
# reseed split brings it in). Used by check_milestone_ladder()/the _combined_*/_split_*/_bank_*/
# _fixed_cashout_* helpers below to route phase-1-only math to compound alone instead of the
# PHASE_ENGINE_BOOKS (compound+sprint) pair those helpers use for phase 2 onward.
PHASE1_SOLO_BOOK = "compound"

# Backward-compat aliases: some older code/tests may still reference these names directly.
# They now point at Phase 1's numbers under the new 5-phase scheme (Phase 1 is the closest
# analog to the old "first lever"). New code should read PHASE_DEFS instead.
PHASE1_CASHOUT_TRIGGER_USD = PHASE_DEFS[1]["trigger"]
PHASE1_CASHOUT_USD = PHASE_DEFS[1]["cashout"]
PHASE1_RESEED_USD = PHASE_DEFS[1]["reseed"]
PHASE1_LADDER = PHASE_DEFS[1]["ladder"]
PHASE2_TARGET_USD = PHASE_DEFS[2]["trigger"]
PHASE2_LADDER = PHASE_DEFS[2]["ladder"]


# 12 Nov 2026 (Claude, Ali's phase-engine isolation request): the phase 1-5 climb is now scoped
# to compound + sprint ONLY. core1/core2 were getting crowded to near-zero equity share by
# Phase 4-5 under the old proportional-to-equity-share split (compound's bigger Curve C stake
# bands out-grow sprint's in dollar terms every phase, and the old split locked that gap in and
# widened it phase over phase -- confirmed by simulation: sprint's reseed share fell from $100 in
# Phase 1 to $0.06 by Phase 5 at a 60% win rate). Ali's decision: core1/core2 keep their existing
# one-time cascade seed (check_sprint_compound_cascade(), $300 total as of 13 Nov 2026, funded
# entirely by compound) and then run fully independently from that point on -- their own trades,
# their own win rate, their own equity, reported separately, never folded into the phase-climb
# numbers or topped up again by
# any future phase cashout/reseed. Only compound and sprint count toward phase triggers, and only
# compound and sprint receive each phase's cashout/reseed, split a FIXED 65% compound / 35%
# sprint (not proportional to equity share) -- simulation-tested (3000 runs x 3 win-rate
# scenarios: 55/60/65%) to keep sprint meaningfully active through Phase 5 (cumulative sprint
# trades by Phase 5 rose from ~98 to ~278 at 60% win rate) while the grand total moves by under
# 0.1% and the bust rate stays at 0% in every scenario tested.
PHASE_ENGINE_BOOKS = ("sprint", "compound")
PHASE_ENGINE_SPLIT = {"compound": 0.65, "sprint": 0.35}


def _combined_equity_for_milestones(books: Optional[tuple] = None) -> float:
    """Local, self-contained sum -- deliberately NOT importing phase_target.combined_equity()
    here, because phase_target.py already imports THIS module (books.py); importing back would
    create a circular import. 12 Nov 2026: scoped to PHASE_ENGINE_BOOKS (compound + sprint)
    only -- core1/core2 no longer count toward phase triggers, see PHASE_ENGINE_BOOKS comment
    above. 13 Nov 2026 (Ali, NEW): optional `books` override lets phase 1 (compound-ALONE, see
    PHASE1_SOLO_BOOK) reuse this same function with a one-book tuple instead of duplicating the
    sum logic -- defaults to PHASE_ENGINE_BOOKS so every pre-13-Nov call site (phases 2-5)
    behaves exactly as before, unchanged."""
    return round(sum(equity(b) for b in (books or PHASE_ENGINE_BOOKS)), 2)


def _split_fixed_amount_capped(total_usd: float, books: Optional[tuple] = None,
                                split: Optional[dict] = None) -> dict:
    """Splits `total_usd` a FIXED 65/35 (compound/sprint) per PHASE_ENGINE_SPLIT, but never asks
    a book to pay out more than its own current equity -- any shortfall (a book's target share
    exceeds what it actually has) spills to the OTHER phase-engine book instead of silently
    recording a cash-out the book can't cover (equity() floors at 0.0, so an uncapped split would
    have recorded phantom money -- caught in sandbox testing 12 Nov 2026 via a $20k-pnl-into-
    compound-only test fixture, where sprint only had $100 but a naive 35% split tried to take
    $2,975 from it). If BOTH books together can't cover total_usd, pays out everything either
    book has (same 'take what's real' principle _fixed_cashout_combined already used).

    13 Nov 2026 (Ali, NEW): optional `books`/`split` override lets phase 1 (compound-ALONE, see
    PHASE1_SOLO_BOOK) reuse this same capped-spill logic with a one-book 100% split instead of
    duplicating it -- default (None) is PHASE_ENGINE_BOOKS/PHASE_ENGINE_SPLIT, so every pre-13-
    Nov call site (phases 2-5) behaves exactly as before, unchanged."""
    books_list = list(books or PHASE_ENGINE_BOOKS)
    split = split or PHASE_ENGINE_SPLIT
    target = {b: round(total_usd * split[b], 2) for b in books_list}
    # last book absorbs the rounding remainder so the two targets sum to exactly total_usd
    allocated = sum(target[b] for b in books_list[:-1])
    target[books_list[-1]] = round(total_usd - allocated, 2)

    avail = {b: equity(b) for b in books_list}
    final = {}
    shortfall = 0.0
    for b in books_list:
        take = min(target[b], avail[b])
        shortfall += target[b] - take
        final[b] = take
    if shortfall > 0.01:
        # spill the shortfall to whichever book still has room, largest-remaining-capacity first
        for b in sorted(books_list, key=lambda x: avail[x] - final[x], reverse=True):
            room = round(avail[b] - final[b], 2)
            if room <= 0:
                continue
            extra = min(room, shortfall)
            final[b] = round(final[b] + extra, 2)
            shortfall = round(shortfall - extra, 2)
            if shortfall <= 0.01:
                break
    return final


def _bank_combined_pct(pct: float, reason: str, now: float, books: Optional[tuple] = None,
                        split: Optional[dict] = None) -> dict:
    """Banks `pct` of CURRENT combined equity, split a FIXED 65/35 (compound/sprint) via
    _split_fixed_amount_capped -- NOT proportional to equity share, and NOT touching core1/core2
    at all. 12 Nov 2026 change, see PHASE_ENGINE_BOOKS comment above for why. 13 Nov 2026 (Ali,
    NEW): optional `books`/`split` override lets phase 1 (compound-ALONE, see PHASE1_SOLO_BOOK)
    reuse this unchanged -- default (None) is PHASE_ENGINE_BOOKS/PHASE_ENGINE_SPLIT, so phases
    2-5 behave exactly as before."""
    combined = _combined_equity_for_milestones(books)
    bank_total = round(combined * pct, 2)
    rec = state.get_value(CASHOUT_KEY)
    rec = rec if isinstance(rec, dict) else {}
    per_book_banked = {}
    if bank_total > 0:
        split_result = _split_fixed_amount_capped(bank_total, books, split)
        for b, amount in split_result.items():
            if amount <= 0:
                continue
            rec.setdefault(b, []).append({"ts": now, "amount": amount, "reason": reason})
            per_book_banked[b] = amount
        state.set_value(CASHOUT_KEY, rec)
    return {"combined_equity_at_fire": combined, "banked_total": sum(per_book_banked.values()),
            "banked_per_book": per_book_banked}


def _fixed_cashout_combined(cashout_usd: float, reseed_usd: float, reason: str, now: float,
                             books: Optional[tuple] = None, split: Optional[dict] = None) -> dict:
    """Hard, fixed-amount cash-out (not a %) off combined compound+sprint equity, split a FIXED
    65/35 (compound/sprint) via _split_fixed_amount_capped -- NOT proportional to equity share,
    and NOT touching core1/core2 at all. 12 Nov 2026 change, see PHASE_ENGINE_BOOKS comment above
    for why. Leaves exactly `reseed_usd` worth of combined compound+sprint equity behind, same
    65/35 split, same real-overshoot-kept and never-take-more-than-a-book-has principles.

    13 Nov 2026 (Ali, NEW): optional `books`/`split` override lets phase 1 (compound-ALONE, see
    PHASE1_SOLO_BOOK) reuse this unchanged with a one-book 100% split -- default (None) is
    PHASE_ENGINE_BOOKS/PHASE_ENGINE_SPLIT, so phases 2-5 behave exactly as before."""
    combined = _combined_equity_for_milestones(books)
    rec = state.get_value(CASHOUT_KEY)
    rec = rec if isinstance(rec, dict) else {}
    per_book_cashed = {}
    split_result = _split_fixed_amount_capped(cashout_usd, books, split)
    for b, amount in split_result.items():
        if amount <= 0:
            continue
        rec.setdefault(b, []).append({"ts": now, "amount": amount, "reason": reason})
        per_book_cashed[b] = amount
    state.set_value(CASHOUT_KEY, rec)
    return {"combined_equity_at_fire": combined, "cashed_out_total": sum(per_book_cashed.values()),
            "cashed_out_per_book": per_book_cashed,
            "combined_equity_after": _combined_equity_for_milestones(books)}


def _phase1_cumulative_banked() -> float:
    """Sum of every Phase-1 ladder rung's banked amount recorded so far. 8 Nov 2026 fix (caught
    live right after deploy): the live state carries a genuine orphaned record, 'p1_240', left
    over from an OLDER ladder version that predates the 7 Nov 2026 5-phase PHASE_DEFS revision --
    same shape as a real rung but its level (240.0) is not in the CURRENT PHASE_DEFS[1]['ladder']
    (250, 600, 1200, 2500, 5000, 8000). A naive shape-based filter silently summed it in anyway
    ($40 extra), which would have corrupted the cascade trigger. Scoped now to exactly the levels
    PHASE_DEFS[1] defines."""
    done = state.get_value(MILESTONE_LADDER_KEY)
    done = done if isinstance(done, dict) else {}
    real_levels = {float(level) for level, _pct in PHASE_DEFS[1]["ladder"]}
    total = 0.0
    for key, rec in done.items():
        if not isinstance(rec, dict) or rec.get("phase") != 1:
            continue
        if "level" not in rec or "banked_total" not in rec:
            continue
        if float(rec["level"]) not in real_levels:
            continue
        total += float(rec.get("banked_total") or 0.0)
    return round(total, 2)


def _maybe_fire_phase1_cascade(now: float) -> Optional[dict]:
    """RETIRED 11 Nov 2026 (Ali) -- superseded by check_sprint_compound_cascade() (see above,
    near COMPOUND_SPLIT_RECIPIENTS). This was the original 8 Nov 2026 mechanism: once the
    combined Phase-1 ladder had cumulatively banked $600+, peel off a fixed $300 to sprint/
    core1/core2. Kept as a permanent no-op (never fires) rather than deleted -- PHASE1_CASCADE_
    RECIPIENTS and the live-pause machinery (is_cascade_live_paused/is_live_armed) below are
    STILL ACTIVE and unrelated to this retirement; only the $600/$300 firing logic itself is
    dead. See PHASE1_CASCADE_KEY's docstring above for the full original spec."""
    return None  # RETIRED 11 Nov 2026 -- dead code below kept only for historical reference
    guard = state.get_value(PHASE1_CASCADE_KEY)
    if isinstance(guard, dict) and guard.get("fired"):
        return None
    if _phase1_cumulative_banked() < PHASE1_CASCADE_MIN_BANKED_USD:
        return None

    share = round(PHASE1_CASCADE_AMOUNT_USD / len(PHASE1_CASCADE_RECIPIENTS), 2)
    shares = {b: share for b in PHASE1_CASCADE_RECIPIENTS}
    leftover = round(PHASE1_CASCADE_AMOUNT_USD - sum(shares.values()), 2)
    if leftover:
        shares[PHASE1_CASCADE_RECIPIENTS[0]] = round(shares[PHASE1_CASCADE_RECIPIENTS[0]] + leftover, 2)

    def _apply(cur, _now=now, _shares=shares):
        done_guard = cur.get(PHASE1_CASCADE_KEY)
        if isinstance(done_guard, dict) and done_guard.get("fired"):
            return {CASHOUT_KEY: cur.get(CASHOUT_KEY), DEPOSITS_KEY: cur.get(DEPOSITS_KEY),
                    PHASE1_CASCADE_KEY: done_guard}
        rec = cur.get(CASHOUT_KEY)
        rec = dict(rec) if isinstance(rec, dict) else {}
        # 8 Nov 2026 fix (caught in sandboxed test before going live): the $300 must come OUT of
        # compound alone -- compound is the book whose own Phase-1 ladder banking produced this
        # money in the first place. Pulling it proportionally across all 4 books (the first draft
        # of this patch) double-counted: sprint/core1/core2 would lose their own cashout AND gain
        # the deposit at the same time, inflating their net equity by more than the intended $100
        # each (confirmed in sandbox: sprint/core1/core2 gained $50-75 extra beyond the clean
        # $100). Single-book deduction keeps this a clean transfer: compound -$300, recipients
        # +$100 each, net zero across the whole system.
        rec["compound"] = list(rec.get("compound") or []) + [
            {"ts": _now, "amount": PHASE1_CASCADE_AMOUNT_USD, "reason": "phase1_ladder_cascade_8nov2026"}]
        dep = cur.get(DEPOSITS_KEY)
        dep = dict(dep) if isinstance(dep, dict) else {}
        for _book_id, _amount in _shares.items():
            dep[_book_id] = list(dep.get(_book_id) or []) + [
                {"ts": _now, "amount": _amount, "reason": "phase1_ladder_cascade_8nov2026"}]
        cascade_result = {"ts": _now, "fired": True, "cascaded_usd": PHASE1_CASCADE_AMOUNT_USD,
                           "shares": _shares}
        return {CASHOUT_KEY: rec, DEPOSITS_KEY: dep, PHASE1_CASCADE_KEY: cascade_result}

    result = state.atomic_multi_fn([CASHOUT_KEY, DEPOSITS_KEY, PHASE1_CASCADE_KEY], _apply)
    fired_now = result[PHASE1_CASCADE_KEY]
    # 8 Nov 2026 (Ali, live-wiring build): the moment the cascade genuinely fires for the first
    # time (not a repeat call hitting the already-fired guard above), auto-set the live-pause
    # for sprint/core1/core2 -- this is the "notify, then pause with no timeout" gate Ali asked
    # for. Only set it if this call is the one that actually fired it (fired_now.get("fired")
    # and fired_now.get("ts") == now), so a later, unrelated call to this function (which will
    # just return the stored guard record every time after the first fire) never re-arms an
    # already-cleared pause.
    if isinstance(fired_now, dict) and fired_now.get("fired") and fired_now.get("ts") == now:
        if not isinstance(state.get_value(CASCADE_LIVE_PAUSE_KEY), dict):
            state.set_value(CASCADE_LIVE_PAUSE_KEY, {"paused": True, "ts": now,
                                                      "reason": "phase1_cascade_fired_8nov2026"})
    return fired_now


def check_milestone_ladder(now: Optional[float] = None) -> Optional[dict]:
    """Call once per cycle (can_open() wires this in, same as check_compound_split()).

    5-PHASE structure (7 Nov 2026, Ali's revised targets, supersedes the original 2-phase
    version): each phase (1-4) walks its own light protective-banking ladder on the way up,
    THEN once combined equity reaches that phase's trigger, fires a ONE-TIME hard cash-out of a
    fixed amount, leaves that phase's reseed floor behind (real overshoot kept if it's bigger
    than the floor -- same principle as before), and advances to the next phase. Phase 5 (the
    final $1,000,000 target) only walks its own ladder -- no automatic cash-out is wired for
    reaching $1,000,000 itself yet, same as the old code left phase 2's end open.

    The $-trigger cash-out is ALWAYS checked FIRST, ahead of that phase's own lighter ladder
    legs, in every phase from 1-4 -- same ordering guarantee as the original code: a big jump
    that clears both the trigger AND several ladder levels in one cycle must not let the small
    protective banks fire first and drain equity back under the trigger before the real lever
    gets a turn.

    Only ONE event (one ladder level, or one phase cash-out) fires per call, same safety
    property as before -- a big jump across several levels/phases in one cycle still processes
    them one call at a time, each call progressing state by exactly one step. Returns the fired
    record, or None if nothing new fired this call."""
    now = now if now is not None else time.time()
    done = state.get_value(MILESTONE_LADDER_KEY)
    done = done if isinstance(done, dict) else {}
    phase = done.get("phase", 1)
    fired_levels = done.get("fired_levels", [])

    pdef = PHASE_DEFS.get(phase)
    if pdef is None:
        # Defensive: an unknown/corrupted phase number must never crash the trading loop --
        # just go idle on this feature until/unless state is fixed manually.
        return None

    pkey = f"p{phase}"

    # 13 Nov 2026 (Ali, NEW): phase 1 runs on compound ALONE -- sprint joins only from phase 2
    # onward. phase_books/phase_split route every helper below to compound-only (100% split)
    # during phase 1, and to the normal PHASE_ENGINE_BOOKS/PHASE_ENGINE_SPLIT (compound+sprint,
    # 65/35) for phases 2-5, unchanged from the 7 Nov behavior.
    if phase == 1:
        phase_books = (PHASE1_SOLO_BOOK,)
        phase_split = {PHASE1_SOLO_BOOK: 1.0}
    else:
        phase_books = PHASE_ENGINE_BOOKS
        phase_split = PHASE_ENGINE_SPLIT
    combined = _combined_equity_for_milestones(phase_books)

    if phase in (1, 2, 3, 4):
        # The hard cash-out trigger is checked FIRST, ahead of the lighter ladder legs below.
        # This matters: if the smaller percentage-banks fired first every call, repeatedly
        # bleeding equity off the top before the cashout check ever ran, a big jump that
        # clears the trigger AND several ladder levels in one go could drain equity back below
        # the trigger purely from its OWN protective banking, before the real lever -- the
        # actual priority -- ever got to fire.
        #
        # 13 Nov 2026 (Ali, NEW, explicit clarifying question answered): phase 1's cashout is
        # additionally gated on the cross-module cascade having ALREADY fired -- "the cascade
        # must fire first, then phase 1 completes." Phase 1 is not considered done (its $2,000
        # cashout / $1,000 reseed) until compound's own banked() has already crossed $500-600
        # and the $300 cascade has gone out to sprint/core1/core2 (SC_CASCADE_KEY shows fired).
        # This check only applies to phase 1 -- phases 2-4 have no such gate.
        cashout_key = f"{pkey}_cashout"
        phase1_cascade_gate_cleared = True
        if phase == 1:
            guard = state.get_value(SC_CASCADE_KEY)
            phase1_cascade_gate_cleared = isinstance(guard, dict) and guard.get("fired") is True
        if (cashout_key not in fired_levels and combined >= pdef["trigger"]
                and phase1_cascade_gate_cleared):
            cashout = _fixed_cashout_combined(pdef["cashout"], pdef["reseed"],
                                               f"phase{phase}_cashout_13nov2026",
                                               now, phase_books, phase_split)
            fired_levels.append(cashout_key)
            done["fired_levels"] = fired_levels
            done["phase"] = phase + 1
            done[f"phase{phase + 1}_started_ts"] = now
            result = {"ts": now, "phase": phase, "event": f"phase{phase}_cashout",
                       "cashout_usd": pdef["cashout"], "reseed_left_usd": pdef["reseed"],
                       **cashout}
            done[cashout_key] = result
            state.set_value(MILESTONE_LADDER_KEY, done)
            return result

        # Only reached if this phase's trigger has NOT yet been hit (or, for phase 1, the
        # cascade hasn't fired yet even if the $-trigger has technically been cleared) -- safe
        # to bank the lighter protective percentages on the way up without risking the main
        # lever.
        for level, pct in pdef["ladder"]:
            key = f"{pkey}_{int(level)}"
            if key in fired_levels:
                continue
            if combined < level:
                break
            bank = _bank_combined_pct(pct, f"milestone_{pkey}_{int(level)}_13nov2026", now,
                                       phase_books, phase_split)
            fired_levels.append(key)
            done["fired_levels"] = fired_levels
            result = {"ts": now, "phase": phase, "level": level, "pct": pct, **bank}
            done[key] = result
            state.set_value(MILESTONE_LADDER_KEY, done)
            if phase == 1:
                _maybe_fire_phase1_cascade(now)  # retired no-op, kept for historical call site
            check_sprint_compound_cascade(now)  # 13 Nov 2026: new cascade (compound-funded, 3-way split), check every ladder tick
            return result

        return None

    # phase == 5 (final target): ladder banking only, no automatic cash-out coded yet.
    for level, pct in pdef["ladder"]:
        key = f"{pkey}_{int(level)}"
        if key in fired_levels:
            continue
        if combined < level:
            break
        bank = _bank_combined_pct(pct, f"milestone_{pkey}_{int(level)}_7nov2026", now)
        fired_levels.append(key)
        done["fired_levels"] = fired_levels
        result = {"ts": now, "phase": 5, "level": level, "pct": pct, **bank}
        done[key] = result
        state.set_value(MILESTONE_LADDER_KEY, done)
        return result

    return None

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
        # 8 Nov 2026 (Ali): real audit found compound ALREADY over its old floor of 4 (6 real open
        # positions, every new signal refused as "balance fully committed") at $298.53 real equity
        # -- the exact self-starvation pattern the 1 Nov/2 Nov fixes above were written to prevent,
        # recurring because the floor itself was too low for compound specifically (sprint/core1/
        # core2 use the "moonshot" branch below with its own separate floor, unaffected). Floor
        # raised 4 -> 8 for "daily"-kind books (compound) only, so it isn't forced to sit on real,
        # currently-profitable winners refusing every new signal while waiting for one to close.
        floor = 8 if c["kind"] == "daily" else 4
        return max(floor, capacity)
    if c["kind"] == "moonshot":
        # 2 Nov 2026 (Ali audit, Phase A/B/C/D targets): the static step ladder for "moonshot"
        # books (sprint/core1/core2) was ALSO wrong, same class of bug as compound's -- real data
        # at the moment this was checked: sprint had 4 open against a cap of 3 at its $92 equity
        # band, core1 had 7 open against a cap of 3 at $271, core2 had 7 open against a cap of 3 at
        # $188 -- all three already silently refusing every new signal mid-cycle, the exact
        # "good cycle, system drops trades on a count cap" failure Ali flagged.
        #
        # This now uses the SAME effective_max_share()/room math stake_usd() enforces (see that
        # function), so the count cap and the capital ceiling can never contradict each other:
        # capacity is just "how many typical-size shots fit in the room still available under the
        # pace-scaled risk ceiling", using the UNCLAMPED shot size (shot_pct of equity, lifted to
        # the STAKE_FLOORS tier) as the typical-size divisor -- not stake_usd()'s own clamped
        # output, which is circular (it shrinks as the book fills, so dividing by it inflates
        # capacity right when the book has the LEAST room left) and not the historical average of
        # already-open positions (compounding pulls that up, understating how many new small shots
        # current equity actually supports).
        # TOTAL slot count (how many typical-size shots fit under the ceiling), the same stable
        # pattern the "daily" branch above uses -- NOT "room for more" added to what's currently
        # open. An earlier version computed room_left/typical_stake and added that to n_open_now,
        # which is unstable: as more small positions open, committed_usd() creeps up only slightly
        # (stakes are small), so room_left barely shrinks and max_open kept climbing in lockstep
        # with open_count every call -- confirmed on a real run filling $5 positions one at a time.
        # A second version used the fixed STAKE_FLOORS/shot_pct design size as the typical-stake
        # divisor, which is stable but can OVERSTATE typical size vs what a book is actually
        # staking in practice (confirmed: sprint's real average open stake is $11.49, well under
        # the $30 design floor, because its 4 real positions include smaller strong-signal/extra-
        # slot entries) -- that overstatement under-counts real capacity exactly like compound's
        # original bug. Fix: prefer the book's REAL average open stake (reflects what it is
        # actually doing) and fall back to the design-floor estimate only when nothing is open yet
        # to measure from.
        share = effective_max_share(book_id)
        n_open_now = open_count(book_id)
        committed = committed_usd(book_id)
        avg_open_stake = (committed / n_open_now) if n_open_now else 0.0
        if avg_open_stake:
            typical_stake = max(avg_open_stake, 5.0)
        else:
            base = eq * c["shot_pct"]
            floor = 0.0
            for lo, usd in STAKE_FLOORS:
                if eq >= lo:
                    floor = usd
            typical_stake = max(base, min(floor, eq * share), 5.0)
        capacity = int((eq * share) / typical_stake) if typical_stake else 4
        return max(4, capacity)
    fresh = stake_usd(book_id)
    return max(4, int((eq * c.get("max_share", 0.6)) / max(fresh, 5.0)) if fresh else 4)


# 2 Nov 2026 (Ali, pool borrowing): the 4 real books -- sprint/core1/core2/compound -- that
# phase_target.py tracks combined equity across. Capital sharing only ever happens among these 4;
# fomo/leaderboard/dev_list/solana_* copy and pattern-tracking books are never pool sources or
# borrowers (same boundary phase_target.py and can_open()'s pause gate already use).
POOL_BOOKS = ("sprint", "core1", "core2", "compound")

# How much of its OWN committed capital a book may have sourced from borrowing against other
# books' idle capital, as a multiple of what it would normally be able to stake from its own
# equity alone. 2.0 means a book can run with up to 2x its own natural capacity when the rest of
# the pool has room -- enough to stop a hot module starving next to idle siblings, without letting
# one module permanently swallow the whole $100 (the other 3 books' can_open() keeps working off
# their OWN equity, unaffected by what one book borrowed).
POOL_BORROW_MULT = 2.0


def pool_free_usd() -> float:
    """Total UNCOMMITTED capital across the 4 real books right now -- equity minus committed, summed.
    Ali's ask (2 Nov 2026): a fixed $25/module split on a real $100 live seed would starve whichever
    module is busy (e.g. compound mid-signal-cycle) while another sits quiet with idle cash. This is
    the shared-pool number a book can borrow against when its OWN free_equity() is too small for a
    real signal but capital is genuinely sitting idle elsewhere in the same 4-book family."""
    return round(sum(max(0.0, equity(b) - committed_usd(b)) for b in POOL_BOOKS), 2)


def borrow_room_usd(book_id: str) -> float:
    """How much MORE this book could stake right now by drawing on the shared pool, beyond what its
    own equity alone would allow -- 0.0 for any book outside POOL_BOOKS (no borrowing for copy/
    pattern-tracking books). Capped at (POOL_BORROW_MULT - 1) x the book's full natural capacity
    (eq*effective_max_share, AS IF NOTHING WERE COMMITTED) so one module can never fully swallow
    the other 3's capital, and further capped by what the pool genuinely has free right now. A
    trade that borrows this way still counts in full toward the BORROWING module's own P&L/equity
    -- 'home share' is a capital SOURCE, not a profit-attribution boundary (Ali, 2 Nov 2026:
    simplest, matches how phase_target.py already only cares about the 4 books' COMBINED equity,
    never per-module purity).

    2 Nov 2026 fix: an earlier version based the cap on REMAINING room (eq*share - committed),
    which backfires exactly for the module that actually needs to borrow -- a module already AT or
    OVER its own risk ceiling (own_room == 0, Ali's example: compound mid-signal-cycle, already
    committed past eq*share) got ZERO borrow room even with real idle cash sitting in siblings,
    because 0 x anything is 0. The cap must be based on the book's FULL natural capacity
    (eq*share, as if it had nothing committed yet), not what's left of it -- a busy module is
    exactly the one this mechanism exists to help."""
    if book_id not in POOL_BOOKS:
        return 0.0
    eq = equity(book_id)
    share = effective_max_share(book_id)
    full_natural_capacity = eq * share
    extra_allowed = full_natural_capacity * (POOL_BORROW_MULT - 1.0)
    return round(max(0.0, min(extra_allowed, pool_free_usd())), 2)


def committed_usd(book_id: str) -> float:
    """Cost of this book's open (not yet closed) paper positions still at risk."""
    from executor import paper_ledger
    return round(paper_ledger.module_open_cost(book_id), 2)


def free_equity(book_id: str) -> float:
    return max(0.0, round(equity(book_id) - committed_usd(book_id), 2))


# Added 14 Nov 2026 (Ali, explicit instruction, real incident): compound's can_open() gate was
# refusing real-eligible entries ("balance is fully committed ($245 open)") off PAPER equity/
# committed-$ numbers that have zero connection to the real wallet (~$120 real vs $245 "open" on
# paper) -- same root disease as the real-buy sizing bug fixed earlier tonight, just in the GATE
# instead of the SIZING. These three mirror equity()/committed_usd()/free_equity() above but are
# computed from REAL on-chain wallet balance and REAL committed capital (paper_ledger.
# module_open_cost_real(), which only counts successfully-executed real buys at their real sent
# amount). Used by can_open() for compound ONLY -- sprint/core1/core2 are not live-armed tonight
# and keep the original paper-based gate untouched, exactly as before.
def equity_real() -> float:
    """Real, live, total spendable wallet balance across every chain with real execution wired
    up (robinhood_chain + base), in USD. Always a fresh on-chain read (never cached) -- a number
    that gates whether real money can be committed must never be stale."""
    from executor import swap_executor
    return swap_executor.get_total_real_wallet_usd()


def committed_usd_real(book_id: str) -> float:
    """Real dollars still at risk in this book's open positions (successfully-executed real buys
    only -- a failed live_ok=False attempt spent $0 real)."""
    from executor import paper_ledger
    return paper_ledger.module_open_cost_real(book_id)


def free_equity_real(book_id: str) -> float:
    return max(0.0, round(equity_real() - committed_usd_real(book_id), 2))


def open_count(book_id: str, chain: str = None) -> int:
    from executor import paper_ledger
    return paper_ledger.module_open_count(book_id, chain=chain)


def effective_max_share(book_id: str, now: Optional[float] = None) -> float:
    """How much of equity this book may have committed at once, RIGHT NOW -- scales up from the
    book's configured base max_share as it falls behind the pace its own target/window demands,
    capped at a sane multiple so it never goes unbounded.

    2 Nov 2026 (Ali, Phase A/B/C/D targets): "as the volume is increasing, the stake size can also
    increase... build this around in a very smart way, protecting the profits... reaching to these
    volumes at the same time." A FIXED max_share (0.25-0.45 for sprint/core1/core2) was sized for
    the OLD, slower per-book targets; it is too tight for the new tight-window targets ($10k/96h,
    then $40k/5-10 days, etc) where the book genuinely needs to deploy more capital per trade and
    hold more concurrent positions to have any real shot at the target in time. Scaling the ceiling
    with how far behind pace the book is means: on pace or ahead -> stay at the conservative base
    (protect profit already made); meaningfully behind -> widen the ceiling so more capital can
    actually get to work; capped at 2x base no matter how far behind, so a book can never ignore
    risk control entirely just because it is losing the race against the clock."""
    c = cfg(book_id)
    base_share = c.get("max_share", 0.6)
    p = pace(book_id, now)
    r = p.get("ratio_to_path")
    if r is None:
        return base_share
    if r >= 1.0:
        return base_share                       # on pace or ahead -- stay conservative
    # Linearly widen from base_share (at r=1.0, on pace) up to 2x base_share (at r<=0.25, badly
    # behind) -- smooth scaling, no sudden jumps, same spirit as stake_usd()'s existing x1.25/x0.8
    # pace multipliers but applied to the RISK CEILING itself, not just one trade's size.
    behind = max(0.0, min(1.0, (1.0 - r) / 0.75))   # 0 at r=1.0, 1 at r<=0.25
    return round(base_share * (1.0 + behind), 4)    # up to 2x base_share


def stake_usd(book_id: str, now: Optional[float] = None, liquidity_usd: Optional[float] = None,
              already_committed_usd: Optional[float] = None) -> float:
    """Pace-aware stake for the next entry in this book.
    base = shot_pct of equity, lifted to the fee floor; behind the target path
    by >25% -> x1.25, ahead by >50% -> x0.8 (bank the lead); capped at the book's EFFECTIVE
    max_share of equity (see effective_max_share() -- widens when badly behind pace) minus what
    is already committed, so cumulative open stakes can never drift past the risk ceiling no
    matter how many trades have already opened. Returns 0 when the book is broke.

    7 Nov 2026 (Ali): liquidity_usd, when given, caps the stake so it never exceeds
    LIQUIDITY_STAKE_CAP_RATIO (default 2%) of the coin's own pool liquidity -- the same ratio
    threshold executor.compound_scalper._liquidity_impact_penalty() uses before its cost model
    jumps from ~2% to a 15% price-impact penalty. Real incident that motivated this: a compound
    stake (50% shot_pct, no liquidity awareness) sized itself at $90.34 against a thin pool,
    landed in that 15% penalty bracket, and lost 43.6% (-$39.39) on a coin that never even moved
    -- pure slippage/impact cost, nothing to do with the pick being bad or compound's aggressive
    shot_pct itself. shot_pct/max_share are left completely untouched by this -- a coin with
    healthy liquidity for its size gets the exact same stake as before; only a stake that would
    have landed in the expensive-impact bracket gets trimmed back to just under it, so compound
    keeps its full aggressive growth on coins with real liquidity to support it, and only a
    thin-liquidity coin gets a smaller, cost-model-aware bite instead of a full-size one.
    liquidity_usd=None (every existing caller except multichain_sniper.py's act()) is a complete
    no-op -- this never changes behavior unless a caller explicitly opts in by passing it.

    2 Nov 2026 fix: the cap used to be eq*max_share PER TRADE with no check against the TOTAL
    already committed -- confirmed on real data that this let sprint's 4 open positions commit
    $45.97 while its max_share ceiling at that equity was only $41.26, already over budget before
    this fix. Now capped against (eq*effective_max_share - committed_usd), the real remaining
    room under the (now pace-scaled) ceiling, not raw free_equity (which is just eq-committed with
    no risk-share check at all)."""
    c = cfg(book_id)
    eq = equity(book_id)
    if eq < 5.0:
        return 0.0
    # 2 Nov 2026: pass `now` through to effective_max_share() -- it was silently defaulting to
    # real wall-clock time while pace() a few lines below correctly used the caller's `now`,
    # so the two could read DIFFERENT elapsed-time states within a single call to this function
    # whenever a caller passes an explicit now != real time. Confirmed as the root cause of a
    # real, reproducible test flake (test_repeat_grad_no_hot_meta_sprint_stake_full): the SAME
    # function, SAME args, called moments apart with now=T0 both times, gave different answers
    # purely because effective_max_share() ignored T0 on one of the two calls.
    share = effective_max_share(book_id, now)
    base = eq * c["shot_pct"]
    if c["kind"] == "moonshot":
        floor = 0.0
        for lo, usd in STAKE_FLOORS:
            if eq >= lo:
                floor = usd
        base = max(base, min(floor, eq * share))
        p = pace(book_id, now)
        r = p.get("ratio_to_path")
        if r is not None and r < 0.75:
            base *= 1.25
        elif r is not None and r > 1.5:
            base *= 0.8
    room_left = max(0.0, eq * share - committed_usd(book_id))
    # 2 Nov 2026 (Ali, pool borrowing): free_equity(book_id) alone only reflects this book's OWN
    # idle cash -- on a real small live seed (e.g. $25/module of a $100 total), that starves a busy
    # module sitting next to idle siblings (Ali's exact example: compound mid-signal-cycle with
    # sprint/core1/core2 quiet). cash_available adds borrow_room_usd() -- capital genuinely idle
    # elsewhere in the same 4-book family, capped at POOL_BORROW_MULT x this book's own natural
    # capacity so one module can never fully swallow the other 3's capital. A trade sized this way
    # still counts in full toward THIS book's own P&L (see borrow_room_usd()'s docstring) -- the
    # pool is a capital source, not a profit-sharing split.
    cash_available = free_equity(book_id) + borrow_room_usd(book_id)
    out = min(base, eq * share + borrow_room_usd(book_id), room_left + borrow_room_usd(book_id), cash_available)
    # 7 Nov 2026 (Ali): liquidity-aware cap, same ratio threshold compound_scalper's own cost
    # model uses before it jumps into its expensive 15% price-impact penalty bracket (see this
    # function's docstring for the real incident this closes). Only ever TIGHTENS -- never
    # widens -- an already-computed stake, and only when liquidity_usd is actually known and
    # thin relative to the stake; unknown liquidity (None) changes nothing, so every existing
    # caller that doesn't pass it is completely unaffected.
    if liquidity_usd and liquidity_usd > 0:
        liq_cap = liquidity_usd * _f("LIQUIDITY_STAKE_CAP_RATIO", 0.02)
        out = min(out, liq_cap)
    # 8 Nov 2026 (Ali, backtest-confirmed): cross-book exposure cap for sprint/core1/core2 ONLY
    # (compound explicitly excluded -- real data showed compound shares coin exposure with
    # these 3 books in 14 historical cases and often wins big exactly where they lose, e.g. one
    # coin: compound +$100.38 while core2 only made +$1.25 on the identical pick, so capping
    # compound's own stake on a shared coin would cut into its separately-proven edge). Real
    # closed-trade audit found 31% of sprint/core1/core2's 35 real trades (11 of 35) were the
    # SAME coin hit by 2-3 of these 3 books at once, tripling the damage of one bad pick.
    # Chronologically backtested (entry-order-aware: whichever book enters a shared coin FIRST
    # gets exposure room, later entrants get trimmed or refused) against the real 35-trade
    # history: recovered +$180.58 on its own (net -$186.45 vs real -$367.03), +$239.51 combined
    # with the 5+ confirmation floor in multichain_sniper.py's lane routing (net -$127.52).
    # already_committed_usd=None (every caller except multichain_sniper.py's act(), and act()
    # itself for compound) is a complete no-op -- this never changes behavior unless a caller
    # explicitly opts in by passing it, same pattern as the liquidity cap directly above.
    if book_id in ("sprint", "core1", "core2") and already_committed_usd is not None:
        cap_room = max(0.0, _f("CROSS_BOOK_EXPOSURE_CAP_USD", 40.0) - already_committed_usd)
        out = min(out, cap_room)
    # 27 Oct 2026: absolute backstop, independent of the equity math above. Every cap until now was
    # RELATIVE to equity() -- exactly the single point of failure a now-fixed accounting bug exploited
    # (a phantom equity reading let a single $100-seed book size a $1,289 stake, all caps "working
    # correctly" relative to the wrong number). This ceiling is pinned to the book's actual SEED, not
    # its current (possibly wrong) equity, so no future bug anywhere upstream of this line can ever
    # again produce a single stake many times the book's real size.
    # 8 Nov 2026 (Ali, "Curve C"): compound's absolute hard_cap used to be a FLAT seed*3.0 ($300)
    # regardless of how big compound's equity grew -- fine near $100-300 equity, but once equity
    # climbs into the thousands a flat $300 either strangles growth (if share-based sizing above
    # already wanted less) or, more importantly, a flat equity-SHARE with no taper (50% of a
    # $30,000 equity = $15,000 on one trade) becomes genuinely dangerous. Backtested tonight
    # against compound's real 31-trade live history (Robinhood+Base, 61.3% win rate, real -99.1%
    # tail loss included via bootstrap): a taper that steps DOWN as equity grows survives 99.6%+
    # of simulated runs through Phase 1-4 while reaching every phase target faster than the old
    # flat-$300 model (which effectively made Phase 5 unreachable). Three candidate curves (A/B/C)
    # were compared; Ali picked Curve C for best combination of speed to Phase 1-4 and
    # survivability (Curve A: 14.3 cumulative days to Phase 4, 0.3% bust rate; Curve C: 10.3
    # days, 0.4% bust rate -- the two are statistically indistinguishable on safety, C is
    # meaningfully faster). compound ONLY -- sprint/core1/core2 keep their own separate
    # TIER_MAX_SHARE/hard_cap path in tier_stake_usd() below, completely untouched by this.
    if book_id == "compound":
        _CURVE_C_BANDS = (
            (0.0,        600.0,     0.50),
            (600.0,      10_000.0,  0.35),
            (10_000.0,   100_000.0, 0.20),
            (100_000.0,  500_000.0, 0.10),
            (500_000.0,  float("inf"), 0.05),
        )
        _curve_c_pct = _CURVE_C_BANDS[-1][2]
        for _lo, _hi, _pct in _CURVE_C_BANDS:
            if _lo <= eq < _hi:
                _curve_c_pct = _pct
                break
        # 8 Nov 2026 (Ali, final): Curve C's whole purpose is that ITS OWN banded percentage is
        # the risk control -- as equity grows, the % of equity risked on one trade comes DOWN
        # (50% -> 35% -> 20% -> 10% -> 5%), which is what keeps one trade from ever being an
        # oversized bite of a large account (Ali's exact concern: a flat 50% on $60,000 equity
        # would open a $30,000 position -- Curve C's real 10% band at that equity instead caps
        # it at $6,000). No separate absolute-dollar ceiling belongs on top of this -- layering
        # one back in (either a flat $300 or a phase-anchored figure, both tried and rejected
        # tonight) either kills the curve's whole purpose above ~$860 equity or reintroduces a
        # workaround Ali explicitly didn't ask for. The banded percentage itself, applied to the
        # real eq at call time, IS the hard_cap.
        hard_cap = _f("COMPOUND_MAX_STAKE_USD", None) or max(5.0, eq * _curve_c_pct)
    else:
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


EXPOSURE_CAP_BOOKS = ("sprint", "core1", "core2")  # compound deliberately excluded, see below


def _exposure_committed_for_coin(book_id: str, chain: Optional[str], addr: Optional[str]) -> Optional[float]:
    """8 Nov 2026 (Ali, fix #2 backtest-confirmed): cross-book exposure cap for sprint/core1/core2
    ONLY (compound explicitly excluded -- real data showed compound shares coin exposure with
    these 3 books in 14 historical cases and often wins big exactly where they lose, e.g. one real
    coin: compound +$100.38 while core2 only made +$1.25 on the identical pick, so capping
    compound's own stake on a shared coin would cut into its separately-proven edge). Real
    closed-trade audit found 31% of sprint/core1/core2's 35 real trades (11 of 35) were the SAME
    coin hit by 2-3 of these 3 books at once, tripling the damage of one bad pick. Chronologically
    backtested (entry-order-aware: whichever book enters a shared coin FIRST gets exposure room,
    later entrants get trimmed or refused) against the real 35-trade history: recovered +$180.58
    on its own (net -$186.45 vs real -$367.03), +$239.51 combined with the 5+ confirmation floor
    in multichain_sniper.py's lane routing (net -$127.52). Returns None (no-op, caller must not
    cap) when book_id/chain/addr don't make this applicable -- same opt-in-only pattern as the
    liquidity cap in stake_usd()."""
    if book_id not in EXPOSURE_CAP_BOOKS or not chain or not addr:
        return None
    from executor import paper_ledger
    by_book = paper_ledger.committed_usd_by_book_for_coin(chain, addr)
    return round(sum(v for b, v in by_book.items() if b in EXPOSURE_CAP_BOOKS), 2)


def tier_stake_usd(book_id: str, tier: str, mcap: Optional[float] = None, now: Optional[float] = None,
                    liquidity_usd: Optional[float] = None, chain: Optional[str] = None,
                    addr: Optional[str] = None) -> float:
    c = cfg(book_id)
    eq = equity(book_id)
    if eq < 5.0 or c["kind"] != "moonshot" or book_id not in TIER_BOOK_SCALE:
        # 7 Nov 2026 (Ali): compound (kind="daily", not in TIER_BOOK_SCALE) always falls through
        # to here -- pass liquidity_usd through so stake_usd()'s liquidity cap actually reaches
        # compound's real entry path (multichain_sniper.py's act()), not just a theoretical arg.
        return stake_usd(book_id, now, liquidity_usd=liquidity_usd,
                          already_committed_usd=_exposure_committed_for_coin(book_id, chain, addr))
    tier = tier_after_brake(tier, book_id)
    base = eq * TIER_PCT[tier] * TIER_BOOK_SCALE[book_id]
    r = pace(book_id, now).get("ratio_to_path")
    if r is not None and r < 0.75:
        base *= 1.25
    elif r is not None and r > 1.5:
        base *= 0.8
    cap_mc = _f("TIER_MAX_MCAP_FRAC", 0.02) * mcap if mcap else float("inf")
    # 2 Nov 2026 (Ali, pool borrowing): same reasoning as stake_usd() -- let sprint/core1/core2
    # (the TIER_BOOK_SCALE books, which go through THIS function, not stake_usd() directly) draw on
    # genuinely idle capital elsewhere in the 4-book family when their own free_equity() is tapped
    # out but a real signal needs sizing. See borrow_room_usd()'s docstring for the cap/attribution
    # rules.
    cash_available = free_equity(book_id) + borrow_room_usd(book_id)
    out = min(base, eq * TIER_MAX_SHARE + borrow_room_usd(book_id), cash_available, cap_mc)
    # 27 Oct 2026: same absolute backstop as stake_usd() below, pinned to the book's real seed rather
    # than its (possibly corrupted) current equity -- see that comment for the full reasoning. This is
    # the path the $1,289.59 oversized stake actually went through; every cap above it was relative to
    # equity() and all of them "passed" because equity() itself was wrong.
    hard_cap = _f(f"{book_id.upper()}_MAX_STAKE_USD", None) or c["seed"] * _f("MAX_STAKE_SEED_MULT", 3.0)
    out = min(out, hard_cap)
    already_committed = _exposure_committed_for_coin(book_id, chain, addr)
    if already_committed is not None:
        cap_room = max(0.0, _f("CROSS_BOOK_EXPOSURE_CAP_USD", 40.0) - already_committed)
        out = min(out, cap_room)
    return round(out, 2) if out >= 5.0 else 0.0


def can_open(book_id: str, strong: bool = False, chain: str = None) -> tuple:
    """strong=True (a Standard/Conviction signal with >=5 confirmations) may use BOOK_STRONG_EXTRA_SLOTS (default 2)
    beyond the normal position limit, and when even those are full may swap out the stalest flat position
    (paper_ledger.swap_out) -- at most one swap per book every 5 minutes.

    2 Nov 2026 (Ali): a combined-equity phase target (phase_target.py) can pause ALL of sprint/
    core1/core2/compound at once -- either the phase target has been hit (Ali's explicit "the
    system does not take any trade" once $10,000 combined is reached) or the drawdown circuit
    breaker tripped (equity fell too far below its own peak this phase). Checked first, for only
    the 4 real books phase_target.py tracks -- fomo/leaderboard/dev_list/solana_* copy and
    pattern-tracking books are unaffected, same as every other gate in this function.

    7 Nov 2026 (Ali): optional chain param makes the POSITION-COUNT limit chain-aware -- e.g.
    robinhood_chain and base each get their own full slot allowance in the same book, so one
    chain filling its slots never blocks the other (Ali's explicit ask: "an additional slot
    should be an option... separate for both... so that both are performing at its max
    utilization"). chain=None (every caller except multichain_sniper.py) is the original,
    completely unfiltered behavior. Capital gating (free_equity/committed_usd below) is
    DELIBERATELY left whole-book and chain-blind -- real dollars are real dollars regardless of
    which chain is using them; only the position-count ceiling becomes per-chain, never the
    amount of actual capital a book can commit."""
    if book_id in ("sprint", "core1", "core2", "compound"):
        from executor import phase_target
        if phase_target.is_paused():
            return False, f"{book_id}: paused -- phase target hit or drawdown breaker active (see phase_target.status())"

    # 13 Nov 2026 (Ali, NEW, explicit clarifying questions answered): sprint/core1/core2 take NO
    # trades at all -- paper or real -- once compound goes LIVE, until the cross-module cascade
    # (check_sprint_compound_cascade()) has fired. Ali's exact concern: "while compound is
    # trading, what will happen for sprint core1 core2... they won't be having any funds before
    # the cascade... things will get hobnosh... we won't know what is the real equity standing,
    # whether it comprises of paper equity as well." Without this gate, these three books would
    # keep silently accumulating PAPER trades/P&L in the background while compound runs alone
    # for phase 1 -- so when the cascade later deposits $100 real dollars into each of them,
    # their equity() would be a confusing mix of that real $100 plus whatever paper P&L they'd
    # already piled up, with no way to cleanly separate "real" from "paper" after the fact. This
    # gate guarantees each of the three starts from an exactly clean $0 (no trades at all, paper
    # included) right up until the moment the cascade credits their real $100 -- at which point
    # this gate opens automatically (SC_CASCADE_KEY shows fired) and all three begin trading
    # immediately on that clean, real $100 seed, nothing else mixed in.
    #
    # SCOPED TO LIVE MODE ONLY (second explicit clarifying question, 13 Nov 2026 -- Ali: "the
    # paper trail needs to be stopped immediately once we're going to push this thing up...
    # once the wallet is funded, the system is live"): this gate is a no-op entirely while
    # LIVE_BOOKS does not include "compound" -- today's ongoing paper trading on sprint/core1/
    # core2 continues completely undisturbed right up until Ali actually arms compound live
    # tonight. The INSTANT compound is armed (LIVE_BOOKS=compound set and this code deployed),
    # this gate activates and sprint/core1/core2 stop taking any trade at all -- paper or real
    # -- until the cascade fires. compound itself is NEVER gated by this check -- it is the one
    # book that runs (and must run) before the cascade exists at all.
    if book_id in ("sprint", "core1", "core2") and SC_CASCADE_FUNDER in _live_books_from_env():
        _cascade_guard = state.get_value(SC_CASCADE_KEY)
        if not (isinstance(_cascade_guard, dict) and _cascade_guard.get("fired") is True):
            return False, (f"{book_id}: paused -- compound is live and no trades (paper or real) are "
                            f"allowed here until the cross-module cascade seeds this book with its "
                            f"real $100 (see check_sprint_compound_cascade())")

    check_cashout(book_id)
    check_bust(book_id)
    check_compound_split()   # RETIRED 11 Nov 2026 -- permanent no-op, see its own docstring
    if book_id == SC_CASCADE_FUNDER:
        check_sprint_compound_cascade()   # 13 Nov 2026 (Ali, corrected): one-time compound-banked $500 -> $100/$100/$100 to sprint/core1/core2, see its own docstring
    check_milestone_ladder()   # 5 Nov 2026 (Ali): periodic partial cash-outs as combined equity climbs toward $20,000, see its own docstring

    # 14 Nov 2026 (Ali, explicit instruction, real incident): compound is live -- gate its
    # capacity off REAL wallet balance/committed $, not paper equity. Real incident tonight:
    # compound refused new real-eligible entries ("balance is fully committed ($245 open)") on
    # paper numbers with zero connection to the real ~$120 wallet. sprint/core1/core2 are not
    # live-armed and keep the original paper-based gate exactly as before -- this ONLY changes
    # compound's own capacity check.
    _use_real_capital = (book_id == "compound" and SC_CASCADE_FUNDER in _live_books_from_env())
    eq = equity_real() if _use_real_capital else equity(book_id)
    if eq < 5.0:
        return False, f"{book_id} book is empty (${eq:.2f})" + (" [real]" if _use_real_capital else "")
    limit = max_open(book_id, eq)
    extra = int(_f("BOOK_STRONG_EXTRA_SLOTS", 2) or 0) if strong else 0
    n_open = open_count(book_id, chain=chain)
    need_cash = (free_equity_real(book_id) if _use_real_capital else free_equity(book_id)) < 5.0  # 14 Nov 2026: real capital for compound, see _use_real_capital above
    # 9 Nov 2026 (Ali): REVERTED the 8 Nov 2026 compound-only swap-out widening. That change was
    # backtested against a sampled window of past refusals (91 refusals / 67 missed coins) and
    # projected a real recovery at compound's then-measured +55.4%/trade expectancy -- but
    # measured against LIVE trades opened after it deployed, compound's own win rate dropped
    # 65.6% -> 44.2% and swung from +$857.44 to -$85.89 in the post-deploy window. Letting
    # compound swap out of an existing open position purely on cash pressure, with NO
    # confirmation-strength requirement, meant it could (and evidently did) close a genuinely
    # still-developing winner to free cash for an unproven new candidate -- the backtest measured
    # the value of the freed-up entries it would capture, not the cost of the winners it would
    # cut short, and live trading showed that cost was real and larger. Restored to strong-signal-
    # only swaps, matching sprint/core1/core2's original, never-changed gate. Left as a tunable
    # env var (now defaulting off) rather than deleted, in case a future, more carefully-scoped
    # version (e.g. only swapping out positions already flat/negative, not any open position) is
    # worth trying later.
    compound_widened_swap = (book_id == "compound" and need_cash
                              and _f("COMPOUND_SWAP_ALLOW_WEAK", 0.0))
    if n_open >= limit + extra or (need_cash and strong) or compound_widened_swap:
        if (strong or compound_widened_swap) and _try_swap(book_id):
            n_open = open_count(book_id, chain=chain)
            need_cash = (free_equity_real(book_id) if _use_real_capital else free_equity(book_id)) < 5.0
        if n_open >= limit + extra:
            chain_note = f" [{chain}]" if chain else ""
            return False, f"{book_id}{chain_note}: {limit} open position(s) is the limit at ${eq:,.0f}" + (f" (+{extra} for strong signals, all used)" if extra else "")
    if need_cash:
        _committed = committed_usd_real(book_id) if _use_real_capital else committed_usd(book_id)
        return False, f"{book_id}: balance is fully committed (${_committed:,.0f} open)" + (" [real]" if _use_real_capital else "")
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

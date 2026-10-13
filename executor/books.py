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
    """Realized P&L the book had when it was reseeded after a bust (those losses are history now)."""
    return round(sum(float(b.get("realized_at_bust") or 0.0) for b in busts(book_id)), 2)


def check_bust(book_id: str, now: Optional[float] = None) -> Optional[dict]:
    """A moonshot/daily book whose total value fell below BUST_BELOW_USD (default $10 of a $100 seed)
    is BUST: log it (the bust count is a headline metric -- how often does $100 die?), reseed the
    book at its seed and restart its clock so the 24h evidence run keeps producing trades.
    Paper only. Oct 13 2026: all three target books were empty 3 hours into the first run."""
    if BOOKS[book_id]["kind"] not in ("moonshot", "daily"):
        return None
    c = cfg(book_id)
    if total_value(book_id) >= (_f("BUST_BELOW_USD", 10.0) or 10.0):
        return None
    if open_count(book_id) > 0:
        return None            # let open slices resolve first
    now = now if now is not None else time.time()
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


def banked(book_id: str) -> float:
    """Profit taken OUT of play (the vault). Once realized profit passes BANK_AFTER_MULT x seed
    (default 3x), bank_pct of the profit above that line is locked away and no longer staked.
    Stateless: derived from realized P&L, so it never gets out of sync."""
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
    other books stay tighter."""
    eq = equity(book_id) if eq is None else eq
    steps = (3, 4, 5, 6) if cfg(book_id)["kind"] == "moonshot" else (1, 2, 3, 4)
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
    return round(max(0.0, out), 2) if out >= 5.0 else 0.0


def can_open(book_id: str) -> tuple:
    check_cashout(book_id)
    check_bust(book_id)
    eq = equity(book_id)
    if eq < 5.0:
        return False, f"{book_id} book is empty (${eq:.2f})"
    if open_count(book_id) >= max_open(book_id, eq):
        return False, f"{book_id}: {max_open(book_id, eq)} open position(s) is the limit at ${eq:,.0f}"
    if free_equity(book_id) < 5.0:
        return False, f"{book_id}: balance is fully committed (${committed_usd(book_id):,.0f} open)"
    return True, "ok"


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

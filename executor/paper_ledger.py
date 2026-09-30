"""
Paper-trading ledger -- the win-rate engine (GO_LIVE_CHECKLIST Phase 2,
Sept 30 2026).

Every time a buy SIGNAL fires -- whether or not real execution is on, and
even when the real budget or position cap would block it -- a paper position
opens at the real current market cap, with the same estimated round-trip
cost the compound scalper uses (executor.compound_scalper.
estimate_round_trip_cost_pct). Each management cycle it is re-priced from
DexScreener and runs EXACTLY the logic real money runs:
executor.exit_rules.evaluate_exit (stop-loss, breakeven lock, trailing stop,
time stop) plus the moonbag profit ladder, rescaled after a lock the same way.

Closed paper trades feed scoreboard(): win rate, average win/loss,
expectancy and total P&L -- overall and per signal type, chain, band and
source. That is how the 80% win-rate target gets MEASURED instead of
assumed, and signal_allowed() lets executor.triggers keep real money away
from any signal whose measured record is poor (checklist 2.3), while it
keeps paper-trading so it can earn its way back.

Honesty rules: a coin DexScreener can no longer price for
UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF cycles in a row, or whose liquidity
collapses below DEAD_LIQUIDITY_USD, is closed at ~zero -- a rug counts as a
full loss, never silently dropped from the stats.
"""
import os
import time
from typing import Callable, Optional

import state
from executor import exit_rules
from executor.moonbag import DEFAULT_TRIM_LADDER

OPEN_KEY = "paper_open_positions"
CLOSED_KEY = "paper_closed_positions"
MAX_OPEN = 80                      # bounds DexScreener calls per cycle
MAX_CLOSED_KEPT = 3000
UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF = 3      # AND at least UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF
UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF = 30.0  # time-based: the fast watcher ticks every 20 s
DEAD_LIQUIDITY_USD = 500.0
EXECUTABLE_CHAINS = {"solana", "bsc", "robinhood_chain"}
PAPER_SCALP_USD = 25.0   # compound-scalper paper size (the pool's seed scale)


def _cost_pct(chain: str, usd: float, liquidity_usd: Optional[float]) -> float:
    from executor.compound_scalper import estimate_round_trip_cost_pct
    return estimate_round_trip_cost_pct(chain, usd, liquidity_usd)


def classify_stage1_signal(score_band: Optional[str], deployer_tier: Optional[str],
                           convergence_count: int, signal_coverage: Optional[float] = None) -> Optional[str]:
    """Which Stage 1 signal a candidate carries, independent of budget/caps.
    Mirrors executor.triggers.evaluate_stage1's fire conditions and its
    real-data coverage gate."""
    from executor.triggers import STAGE1_MIN_SIGNAL_COVERAGE
    if deployer_tier in ("elite", "good"):
        return "deployer_trusted"
    if convergence_count >= 2:
        return "wallet_convergence"
    if score_band in ("A", "B"):
        if signal_coverage is not None and signal_coverage < STAGE1_MIN_SIGNAL_COVERAGE:
            return None
        return f"score_band_{score_band}"
    return None


def _open() -> dict:
    v = state.get_value(OPEN_KEY)
    return v if isinstance(v, dict) else {}


def _closed() -> list:
    v = state.get_value(CLOSED_KEY)
    return v if isinstance(v, list) else []


def open_paper(chain: str, token: str, source: str, signal: str, usd: float,
               entry_mcap: Optional[float], band: Optional[str] = None,
               liquidity_usd: Optional[float] = None, tags: Optional[dict] = None,
               now: Optional[float] = None, strategy: str = "stage",
               guard: Optional[str] = None) -> Optional[dict]:
    """Opens one paper position; no-op if the same (chain, token, source) is
    already open, the chain has no buy path, or the price is unknown."""
    if chain not in EXECUTABLE_CHAINS or not token or not entry_mcap or entry_mcap <= 0 or not usd:
        return None
    now = now if now is not None else time.time()
    book = _open()
    pid = f"{source}:{chain}:{token}"
    if pid in book or len(book) >= MAX_OPEN:
        return None
    pos = {"id": pid, "chain": chain, "token": token, "source": source, "signal": signal,
           "band": band, "tags": tags or {}, "usd": float(usd), "entry_mcap": float(entry_mcap),
           "peak_mcap": float(entry_mcap), "opened_ts": now, "remaining": 1.0,
           "breakeven_locked": False, "ladder_scale": 1.0, "rungs_fired": [],
           "proceeds_usd": 0.0, "cost_pct": _cost_pct(chain, usd, liquidity_usd),
           "unpriced_cycles": 0, "events": [], "strategy": strategy, "tp_done": False,
           "guard": guard or "n/a"}
    book[pid] = pos
    state.set_value(OPEN_KEY, book)
    return pos


def _sell(pos: dict, pct_of_original: float, mcap: float, why: str, now: float):
    pct = min(pct_of_original, pos["remaining"])
    if pct <= 0:
        return
    mult = mcap / pos["entry_mcap"]
    proceeds = pos["usd"] * pct * mult * (1.0 - pos["cost_pct"])
    pos["proceeds_usd"] += proceeds
    pos["remaining"] = max(0.0, pos["remaining"] - pct)
    pos["events"].append({"ts": now, "why": why, "pct": round(pct, 4), "mult": round(mult, 3),
                          "usd": round(proceeds, 2)})


def _close(pos: dict, exit_type: str, reason: str, now: float) -> dict:
    pos["closed_ts"] = now
    pos["exit_type"] = exit_type
    pos["close_reason"] = reason
    pos["pnl_usd"] = round(pos["proceeds_usd"] - pos["usd"], 4)
    pos["pnl_pct"] = round(pos["pnl_usd"] / pos["usd"] * 100, 2) if pos["usd"] else 0.0
    pos["win"] = pos["pnl_usd"] > 0
    return pos


def _manage_scalper(pos: dict, mcap: float, now: float) -> bool:
    """Compound-scalper exits (executor.compound_scalper.scalp_exit_decision)
    on a paper position. Returns True when the position closed."""
    from executor.compound_scalper import scalp_exit_decision
    mult = mcap / pos["entry_mcap"]
    peak = pos["peak_mcap"] / pos["entry_mcap"]
    d = scalp_exit_decision(mult, peak, (now - pos["opened_ts"]) / 60.0, pos.get("tp_done", False))
    if not d.should_exit:
        return False
    if d.exit_type == "take_profit_partial":
        _sell(pos, d.pct_to_sell, mcap, d.exit_type, now)
        pos["tp_done"] = True
        return False
    _sell(pos, pos["remaining"], mcap, d.exit_type, now)
    _close(pos, d.exit_type, d.reason, now)
    return True


def _batch_snapshots(book: dict, batch_fn) -> dict:
    """{(chain, token): snapshot} via ONE DexScreener batch call per chain
    per 30 tokens (Sept 30 2026: the 20-second fast watcher would otherwise
    make one call per paper position -- up to ~240/min, near DexScreener's
    limit)."""
    by_chain = {}
    for pos in book.values():
        by_chain.setdefault(pos["chain"], []).append(pos["token"])
    out = {}
    for chain, tokens in by_chain.items():
        try:
            pairs = batch_fn(chain, list(dict.fromkeys(tokens))) or {}
        except Exception:
            pairs = {}
        for tok, pair in pairs.items():
            mcap = pair.get("marketCap") or pair.get("fdv")
            out[(chain, tok)] = {"mcap_usd": float(mcap) if mcap else None,
                                 "liquidity_usd": (pair.get("liquidity") or {}).get("usd")}
    return out


def manage(snapshot_fn: Optional[Callable[[str, str], Optional[dict]]] = None, now: Optional[float] = None,
           batch_fn: Optional[Callable] = None) -> dict:
    """Re-price every open paper position and apply exits. Pass batch_fn
    (layers.layer14_revival.fetch_dexscreener_batch) in production; the
    per-token snapshot_fn is kept for tests and as a fallback."""
    now = now if now is not None else time.time()
    book = _open()
    if not book:
        return {"open": 0, "closed_now": 0}
    batched = _batch_snapshots(book, batch_fn) if batch_fn else None
    closed_now = []
    for pid, pos in list(book.items()):
        if batched is not None:
            snap = batched.get((pos["chain"], pos["token"]))
        else:
            try:
                snap = snapshot_fn(pos["chain"], pos["token"])
            except Exception:
                snap = None
        mcap = (snap or {}).get("mcap_usd")
        liq = (snap or {}).get("liquidity_usd")
        if mcap is None:
            pos["unpriced_cycles"] = pos.get("unpriced_cycles", 0) + 1
            pos.setdefault("unpriced_since", now)
            if (pos["unpriced_cycles"] >= UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF
                    and now - pos["unpriced_since"] >= UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF * 60):
                closed_now.append(_close(pos, "written_off", "no longer priceable (delisted/rugged)", now))
                del book[pid]
            continue
        pos["unpriced_cycles"] = 0
        pos.pop("unpriced_since", None)
        pos["peak_mcap"] = max(pos["peak_mcap"], mcap)
        if liq is not None and liq < DEAD_LIQUIDITY_USD:
            _sell(pos, pos["remaining"], mcap, "liquidity collapsed", now)
            closed_now.append(_close(pos, "rug_liquidity", f"liquidity ${liq:,.0f}", now))
            del book[pid]
            continue

        if pos.get("strategy") == "runner":
            d = exit_rules.evaluate_exit(pos["entry_mcap"], mcap, pos["peak_mcap"], pos["opened_ts"], now,
                                         exit_rules.moonshot_runner_pct(), True, pos["cost_pct"])
            if d.action == "exit_all":
                _sell(pos, pos["remaining"], mcap, d.exit_type, now)
                closed_now.append(_close(pos, d.exit_type, d.reason, now))
                del book[pid]
            continue
        if pos.get("strategy") == "scalper":
            if _manage_scalper(pos, mcap, now):
                closed_now.append(pos)
                del book[pid]
            continue
        d = exit_rules.evaluate_exit(pos["entry_mcap"], mcap, pos["peak_mcap"], pos["opened_ts"], now,
                                     pos["remaining"], pos["breakeven_locked"], pos["cost_pct"])
        if d.action == "exit_all":
            _sell(pos, pos["remaining"], mcap, d.exit_type, now)
            closed_now.append(_close(pos, d.exit_type, d.reason, now))
            del book[pid]
            continue
        if d.action == "sell_partial":
            _sell(pos, d.pct_of_original, mcap, d.exit_type, now)
            if d.exit_type == "breakeven_lock":
                pos["breakeven_locked"] = True
                pos["ladder_scale"] = max(0.0, 1.0 - d.pct_of_original)
            else:
                # Only the moonshot runner is left: score the trade now
                # (runner marked to market), and track the runner on its own
                # so the win rate isn't held open by a free ride.
                runner_frac = pos["remaining"]
                runner_value = pos["usd"] * runner_frac * (mcap / pos["entry_mcap"]) * (1.0 - pos["cost_pct"])
                pos["proceeds_usd"] += runner_value
                closed_now.append(_close(pos, d.exit_type, d.reason + " (runner marked to market)", now))
                del book[pid]
                book["runner:" + pid] = {
                    **{k: pos[k] for k in ("chain", "token", "signal", "band", "tags", "entry_mcap",
                                           "peak_mcap", "cost_pct", "guard")},
                    "id": "runner:" + pid, "source": "runner", "strategy": "runner",
                    "usd": pos["usd"] * runner_frac, "opened_ts": now, "remaining": 1.0,
                    "breakeven_locked": True, "ladder_scale": 1.0, "rungs_fired": [], "proceeds_usd": 0.0,
                    "unpriced_cycles": 0, "events": [], "tp_done": False}
                continue

        mult = mcap / pos["entry_mcap"]
        for tier, pct in DEFAULT_TRIM_LADDER:
            if str(tier) in pos["rungs_fired"] or mult < tier:
                continue
            _sell(pos, pct * pos["ladder_scale"], mcap, f"moonbag {tier}x", now)
            pos["rungs_fired"].append(str(tier))
            break  # one rung per cycle, same as moonbag.check_and_trim
        if pos["remaining"] <= 1e-6:
            closed_now.append(_close(pos, "fully_sold", "every slice sold", now))
            del book[pid]

    state.set_value(OPEN_KEY, book)
    if closed_now:
        state.set_value(CLOSED_KEY, (_closed() + closed_now)[-MAX_CLOSED_KEPT:])
    return {"open": len(book), "closed_now": len(closed_now)}


def _stats(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = [r for r in rows if r.get("win")]
    losses = [r for r in rows if not r.get("win")]
    total = sum(r["pnl_usd"] for r in rows)
    return {
        "n": n, "wins": len(wins), "win_rate": round(len(wins) / n, 3),
        "avg_win_pct": round(sum(r["pnl_pct"] for r in wins) / len(wins), 1) if wins else None,
        "avg_loss_pct": round(sum(r["pnl_pct"] for r in losses) / len(losses), 1) if losses else None,
        "expectancy_pct": round(sum(r["pnl_pct"] for r in rows) / n, 1),
        "total_pnl_usd": round(total, 2),
    }


def scoreboard(since_ts: Optional[float] = None) -> dict:
    allrows = [r for r in _closed() if since_ts is None or r.get("closed_ts", 0) >= since_ts]
    rows = [r for r in allrows if r.get("strategy") != "runner"]
    runners = [r for r in allrows if r.get("strategy") == "runner"]
    book = _open()
    out = {"overall": _stats(rows), "open_count": len(book),
           # Moonshot runners: each is the free-ride slice of a trade already
           # scored above; P&L is vs the original entry price.
           "runners": {**_stats(runners), "riding": sum(1 for p in book.values() if p.get("strategy") == "runner"),
                       "best_multiple": round(max([r["peak_mcap"] / r["entry_mcap"] for r in runners]
                                                  + [p["peak_mcap"] / p["entry_mcap"] for p in book.values()
                                                     if p.get("strategy") == "runner"] + [0]), 1)}}
    for field in ("signal", "chain", "band", "source", "exit_type", "guard"):
        groups = {}
        for r in rows:
            groups.setdefault(str(r.get(field) or "-"), []).append(r)
        out[f"by_{field}"] = {k: _stats(v) for k, v in sorted(groups.items())}
    out["recent"] = [{k: r.get(k) for k in ("chain", "token", "signal", "band", "pnl_pct", "pnl_usd",
                                             "exit_type", "closed_ts", "opened_ts")}
                     for r in rows[-25:]][::-1]
    return out


def min_signal_win_rate() -> float:
    try:
        return float(os.environ.get("MIN_SIGNAL_WIN_RATE", "0.5"))
    except ValueError:
        return 0.5


def min_trades_for_verdict() -> int:
    try:
        return int(os.environ.get("MIN_TRADES_FOR_VERDICT", "20"))
    except ValueError:
        return 20


def signal_allowed(signal: Optional[str]) -> tuple:
    """Checklist 2.3: a signal whose paper record over >= MIN_TRADES_FOR_VERDICT
    closed trades has a win rate below MIN_SIGNAL_WIN_RATE, or negative
    expectancy, is kept away from real money (it keeps paper-trading, so it
    can earn its way back). Too little data -> allowed."""
    if not signal:
        return True, "no signal"
    rows = [r for r in _closed() if r.get("signal") == signal and r.get("strategy") != "runner"][-200:]
    st = _stats(rows)
    if st["n"] < min_trades_for_verdict():
        return True, f"{signal}: {st['n']} paper trades so far (verdict at {min_trades_for_verdict()})"
    if st["win_rate"] < min_signal_win_rate() or st["expectancy_pct"] <= 0:
        return False, (f"{signal} paper record {st['wins']}/{st['n']} wins "
                       f"({st['win_rate'] * 100:.0f}%, expectancy {st['expectancy_pct']:+.1f}%) "
                       f"below the {min_signal_win_rate() * 100:.0f}% bar")
    return True, f"{signal} paper win rate {st['win_rate'] * 100:.0f}% over {st['n']}"

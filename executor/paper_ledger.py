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


def _f_env(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default

OPEN_KEY = "paper_open_positions"
CLOSED_KEY = "paper_closed_positions"
MAX_OPEN = 80                      # bounds DexScreener calls per cycle
MAX_CLOSED_KEPT = 3000
UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF = 3      # AND at least UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF
# 26 Oct 2026: all 11 written_off exits in the post-liquidity-fix batch sat unpriced for the
# full ~30 min before being booked -- the coin is dead within the first few unpriced cycles
# (3 cycles already means no price for 60s+ on a 20s ticker); the other ~29 minutes just tied
# up an open-position slot a live signal could have used. Cut to 8 min -- still generous
# against a brief data-provider hiccup, no longer generous against an actually-dead pool.
UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF = _f_env("UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF", 8.0)
# 28 Oct 2026 (Ali): robinhood_chain paper trades were dying at a 96% rate (23/24 post-restart) --
# ALL of them with last_mcap=None, meaning not one single price update ever landed, not even once,
# the entire 8 minutes before write-off. A position that was priced fine for an hour and then briefly
# loses its feed is a totally different situation from one that NEVER got priced even a single time
# since entry -- the first is a data hiccup worth waiting out, the second means the entry signal fired
# on a pool thin/new enough that no price source (DexScreener here) had indexed it yet, which is itself
# the risk signal. The uniform 8-min/3-cycle grace period treated both the same, tying up a book's
# capital against a coin with zero price visibility for the full 8 minutes before finally giving up.
# Never-priced-since-entry now gets a much shorter leash -- still enough cycles to rule out a one-tick
# indexing lag, nowhere near enough to hold a slot hostage on a pool that was never going to resolve.
NEVER_PRICED_CYCLES_BEFORE_WRITE_OFF = _f_env("NEVER_PRICED_CYCLES_BEFORE_WRITE_OFF", 3.0)
NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF = _f_env("NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF", 2.0)
DEAD_LIQUIDITY_USD = 500.0
EXECUTABLE_CHAINS = {"solana", "bsc", "robinhood_chain"}
# Base is paper-tracked too (no buy path yet -- checklist 7.3): the alert
# replay's band-B winners were all on Base, so its record is measured now.
PAPER_CHAINS = EXECUTABLE_CHAINS | {"base"}
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


def _closed_raw() -> list:
    v = state.get_value(CLOSED_KEY)
    return v if isinstance(v, list) else []


# Paper trades opened before the entry-price fix (PR #13, Sept 30 2026 ~15:35
# UTC) were priced against an entry mcap from a different source/unit: the
# dashboard showed 8 closed at 0% wins / -49.5%, with -93..-96% "stops" on
# coins whose real candles were only -5%. They are kept (for history) but
# excluded from every win-rate number and the auto-disable gate.
# Oct 1 2026 ~05:45 UTC: moved again -- trades before the price-jump filter
# (executor/price_sanity.py) include fake +2,000% "wins" from data glitches.
STATS_SINCE_TS = float(os.environ.get("PAPER_STATS_SINCE_TS", "1790833500"))


def _heal(r: dict) -> dict:
    """A rug / drained-pool exit can never be a profit. Rows closed before the Oct 13 2026 fix booked a
    glitched mcap read as a fake gain (+$505 on a $30 stake); count them as the total loss they were."""
    if r.get("exit_type") == "rug_liquidity" and float(r.get("pnl_usd") or 0) > 0:
        r = dict(r)
        r["pnl_usd"] = -float(r.get("usd") or 0)
        r["pnl_pct"] = -100.0
        r["win"] = False
        r["healed"] = True
    return r


def _closed() -> list:
    return [_heal(r) for r in _closed_raw() if (r.get("opened_ts") or 0) >= STATS_SINCE_TS]


import threading as _threading

_OPEN_LOCK = _threading.RLock()     # 14 Oct: concurrent act() threads lost positions (read-modify-write on the whole open book)


def open_paper(*a, **k):
    with _OPEN_LOCK:
        return _open_paper_unlocked(*a, **k)


def ensure_open(pos: dict) -> bool:
    """Re-inserts a position that vanished from the open book (lost write). True when it had to be restored."""
    with _OPEN_LOCK:
        book = _open()
        if pos["id"] in book:
            return False
        book[pos["id"]] = pos
        state.set_value(OPEN_KEY, book)
        return True


def _open_paper_unlocked(chain: str, token: str, source: str, signal: str, usd: float,
               entry_mcap: Optional[float], band: Optional[str] = None,
               liquidity_usd: Optional[float] = None, tags: Optional[dict] = None,
               now: Optional[float] = None, strategy: str = "stage",
               guard: Optional[str] = None, exit_profile: Optional[str] = None,
               module: Optional[str] = None, live: bool = False) -> Optional[dict]:
    """Opens one paper position; no-op if the same (chain, token, source) is
    already open, the chain has no buy path, or the price is unknown."""
    if chain not in PAPER_CHAINS or not token or not entry_mcap or entry_mcap <= 0 or not usd:
        return None
    # Oct 13 2026 (Ali: "retire"): the legacy stage1 / scalper / quick paper traders opened $22-25 positions
    # outside the books and lost $2,298 on 1,045 trades. Sniper entries (strategy "early") are unaffected;
    # LEGACY_PAPER_ENABLED=true brings the old traders back. Positions already open are still managed.
    if strategy in ("stage", "scalper", "quick") and os.environ.get("LEGACY_PAPER_ENABLED", "false").lower() != "true":
        return None
    now = now if now is not None else time.time()
    book = _open()
    pid = f"{source}:{chain}:{token}"
    if pid in book or len(book) >= MAX_OPEN:
        return None
    try:
        from layers import modules as _mods
        module = module or _mods.for_paper(source, signal)
    except Exception:  # noqa: BLE001
        module = module or "own"
    pos = {"id": pid, "module": module, "chain": chain, "token": token, "source": source, "signal": signal,
           "band": band, "tags": tags or {}, "usd": float(usd), "entry_mcap": float(entry_mcap),
           "peak_mcap": float(entry_mcap), "opened_ts": now, "remaining": 1.0,
           "breakeven_locked": False, "ladder_scale": 1.0, "rungs_fired": [],
           "proceeds_usd": 0.0, "cost_pct": _cost_pct(chain, usd, liquidity_usd),
           # 27 Oct 2026: liquidity_usd was accepted as a parameter and used ONCE internally to compute
           # cost_pct, then thrown away -- never saved on the position record itself. Result: every single
           # closed trade in the whole history (253/253 sampled on core1) shows liquidity_usd as missing,
           # even on trades that died as rug_liquidity/written_off moments after entry. There was no way
           # to tell, after the fact, whether a losing trade had thin liquidity at entry or not -- which
           # defeats the actual point of paper testing (find the pattern that predicts losers). Now stored
           # so every future trade carries its real entry liquidity for analysis.
           "liquidity_usd": liquidity_usd,
           "unpriced_cycles": 0, "events": [], "strategy": strategy, "tp_done": False,
           "guard": guard or "n/a", "exit_profile": exit_profile, "live": bool(live)}
    if strategy == "early":
        # pre-early coins: fills are slower/worse than the quoted price (curve moves, priority fee, 1% venue fee)
        pos["cost_pct"] = max(pos["cost_pct"], float(os.environ.get("EARLY_PAPER_COST_PCT", "0.10")))
    book[pid] = pos
    state.set_value(OPEN_KEY, book)
    return pos


def _sell(pos: dict, pct_of_original: float, mcap: float, why: str, now: float):
    pct = min(pct_of_original, pos["remaining"])
    if pct <= 0:
        return
    mult = mcap / pos["entry_mcap"]
    proceeds = pos["usd"] * pct * mult * (1.0 - pos["cost_pct"])
    if pos.get("live"):
        try:                                   # mirror the exit on the real position (sell is never blocked by the kill switch)
            from executor import pumpportal_exec
            pumpportal_exec.sell(pos["token"], min(100.0, pct / max(pos["remaining"], 1e-9) * 100.0))
        except Exception:  # noqa: BLE001
            pass
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


def swap_out(module_id: str, now: Optional[float] = None) -> Optional[dict]:
    """Slot swap (Oct 13 2026): frees a slot for a STRONG new signal by closing this module's stalest flat position.
    Only a position that is old (>= SWAP_MIN_AGE_MIN, default 45), never ran (peak < 1.5x) and is not deep in the
    red (>= SWAP_MIN_MULT, default 0.75x -- anything lower is left to its own stop) qualifies; the oldest goes first.
    The loss is bounded (<= 25% + cost) and the stake goes back to the book. Returns the closed row or None."""
    now = now if now is not None else time.time()
    min_age = float(os.environ.get("SWAP_MIN_AGE_MIN", "45")) * 60.0
    min_mult = float(os.environ.get("SWAP_MIN_MULT", "0.75"))
    book = _open()
    best = None
    for pid, pos in book.items():
        if pos.get("strategy") != "early" or _module_of(pos) != module_id or pos.get("live"):
            continue
        last = pos.get("last_mcap")
        if not last or now - pos["opened_ts"] < min_age:
            continue
        if pos["peak_mcap"] / pos["entry_mcap"] >= 1.5 or last / pos["entry_mcap"] < min_mult or pos.get("rungs_fired"):
            continue
        if best is None or pos["opened_ts"] < best[1]["opened_ts"]:
            best = (pid, pos)
    if best is None:
        return None
    pid, pos = best
    mult = pos["last_mcap"] / pos["entry_mcap"]
    _sell(pos, pos["remaining"], pos["last_mcap"], "slot_swap", now)
    row = _close(pos, "slot_swap", f"swapped out at {mult:.2f}x to free a slot for a stronger signal", now)
    del book[pid]
    state.set_value(OPEN_KEY, book)
    state.set_value(CLOSED_KEY, (_closed_raw() + [row])[-MAX_CLOSED_KEPT:])
    return row


def _manage_scalper(pos: dict, mcap: float, now: float) -> bool:
    """Compound-scalper exits (executor.compound_scalper.scalp_exit_decision)
    on a paper position. Returns True when the position closed."""
    from executor.compound_scalper import scalp_exit_decision
    mult = mcap / pos["entry_mcap"]
    peak = pos["peak_mcap"] / pos["entry_mcap"]
    d = scalp_exit_decision(mult, peak, (now - pos["opened_ts"]) / 60.0, pos.get("tp_done", False),
                            pos.get("exit_profile"))
    if not d.should_exit:
        return False
    if d.exit_type == "take_profit_partial":
        _sell(pos, d.pct_to_sell, mcap, d.exit_type, now)
        pos["tp_done"] = True
        return False
    _sell(pos, pos["remaining"], mcap, d.exit_type, now)
    _close(pos, d.exit_type, d.reason, now)
    return True


# 20 Oct 2026: the "trail" fraction below is how close to peak we REQUIRE
# before selling (mult <= peak * trail), so giveback = 1 - trail. Checked
# against 215 closed trailing_stop trades: configured giveback was 30-55%,
# but the ACTUAL giveback (peak vs. what we sold at) was a median of 64% --
# these coins move in violent jumps between our 20s/60s polls and thin
# liquidity means the price we see often isn't fully sellable anyway. A
# tighter trigger can't fix the poll-gap/liquidity gap, but it narrows how
# far price has to fall before we even START trying to sell, which is the
# one lever available without redesigning the monitor. Tightened +8-10pts
# per lane (fast_moon left alone -- it's the explicit lottery/runner lane).
EARLY_RULES = {
    "sprint": ({2: 0.20, 4: 0.15, 10: 0.10}, 0.35, 0.50),
    # lane: (take-profit ladder {mult: fraction of original}, hard stop mult, trail-from-peak mult after 2x)
    "core2": ({2: 0.20, 4: 0.15, 10: 0.10}, 0.35, 0.50),
    "core1": ({2: 0.20, 4: 0.15, 10: 0.10}, 0.35, 0.50),
    "dev_list": ({2: 0.40, 4: 0.25, 10: 0.15}, 0.55, 0.68),
    # FAST module (14 Oct 2026): earlier first slice, trail starts at 1.4x (see EARLY_TRAIL_AFTER), big runner
    "fast_sprint": ({1.4: 0.30, 2.5: 0.20, 6: 0.15, 15: 0.10}, 0.55, 0.65),
    "fast_core": ({1.5: 0.20, 3: 0.20, 8: 0.10}, 0.45, 0.60),
    "fast_moon": ({5: 0.10, 20: 0.10}, 0.50, 0.50),                 # lottery: 80% rides, trail starts at 3x -- left as-is
    "compound": ({2: 0.60, 3: 0.40}, 0.70, 0.78),
    "leaderboard": ({2: 0.50, 3: 0.50}, 0.65, 0.78),
}


EARLY_TRAIL_AFTER = {"fast_sprint": 1.4, "fast_core": 1.5, "fast_moon": 3.0}      # peak multiple from which the trailing stop replaces the hard stop (default 2.0)


def _manage_early(pos: dict, mcap: float, now: float) -> bool:
    """Exit engine for sniper entries (strategy 'early'): partial take-profits,
    hard stop, trailing stop on the runner after 2x, dead-coin time stop.
    Returns True when the position closed."""
    ladder, stop, trail = EARLY_RULES.get(pos.get("exit_profile") or "", EARLY_RULES["core1"])
    stop = float(os.environ.get("EARLY_STOP_MULT", stop)) if not str(pos.get("exit_profile") or "").startswith("fast_") else stop
    ta = EARLY_TRAIL_AFTER.get(pos.get("exit_profile") or "", 2.0)
    mult = mcap / pos["entry_mcap"]
    peak = pos["peak_mcap"] / pos["entry_mcap"]
    age_min = (now - pos["opened_ts"]) / 60.0
    for tier, frac in sorted(ladder.items()):
        if str(tier) not in pos["rungs_fired"] and mult >= tier and pos["remaining"] > 1e-6:
            _sell(pos, frac, mcap, f"take profit {tier}x", now)
            pos["rungs_fired"].append(str(tier))
    if pos["remaining"] <= 1e-6:
        _close(pos, "fully_sold", "every slice sold", now)
        return True
    why = None
    if peak < ta and mult <= stop:
        why = ("stop_loss", f"hard stop at {mult:.2f}x")
    elif peak >= ta and mult <= peak * trail:
        why = ("trailing_stop", f"{mult:.1f}x is {int((1 - trail) * 100)}% below the {peak:.1f}x peak")
    # 20 Oct 2026: every time_stop exit in 1,090 paper trades fired at exactly
    # ~90 min (never earlier, never later -- it's a hard flush, not catching
    # a real reversal) with a median loss of -35% (range -9% to -67%), zero
    # wins out of 128. Cut the window in half: a coin that hasn't shown signs
    # of life by 45 min isn't going to recover by 90, and leaving it running
    # the extra 45 min only lets the loss drift further before the flush.
    elif age_min >= float(os.environ.get("EARLY_DEAD_MIN", "45")) and peak < 1.5 and mult < 1.2:
        why = ("time_stop", f"no move after {age_min:.0f} min")
    elif age_min >= float(os.environ.get("EARLY_MAX_HOLD_MIN", "1440")):
        why = ("max_hold", "24h hold limit")
    if why:
        _sell(pos, pos["remaining"], mcap, why[0], now)
        _close(pos, why[0], why[1], now)
        return True
    return False


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
           batch_fn: Optional[Callable] = None, source_prefix: Optional[str] = None) -> dict:
    """Re-price every open paper position and apply exits. Pass batch_fn
    (layers.layer14_revival.fetch_dexscreener_batch) in production; the
    per-token snapshot_fn is kept for tests and as a fallback."""
    now = now if now is not None else time.time()
    book = _open()
    _orig_ids = set(book)
    if not book:
        return {"open": 0, "closed_now": 0}
    batched = _batch_snapshots({k: v for k, v in book.items() if not source_prefix or
                                str(v.get("source", "")).startswith(source_prefix)}, batch_fn) if batch_fn else None
    closed_now = []
    for pid, pos in list(book.items()):
        if source_prefix and not str(pos.get("source", "")).startswith(source_prefix):
            continue
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
            never_priced = pos.get("last_mcap") is None
            cycles_needed = NEVER_PRICED_CYCLES_BEFORE_WRITE_OFF if never_priced else UNPRICEABLE_CYCLES_BEFORE_WRITE_OFF
            minutes_needed = NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF if never_priced else UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF
            if (pos["unpriced_cycles"] >= cycles_needed
                    and now - pos["unpriced_since"] >= minutes_needed * 60):
                reason = ("never priceable since entry (pool not indexed / dead on arrival)" if never_priced
                          else "no longer priceable (delisted/rugged)")
                closed_now.append(_close(pos, "written_off", reason, now))
                del book[pid]
            continue
        pos["unpriced_cycles"] = 0
        pos["last_mcap"] = mcap          # dashboard shows the live multiple
        pos.pop("unpriced_since", None)
        # Same entry sanity check as real positions (position_state.
        # sanity_rebase_entry): an entry >5x away from the first live read
        # within 20 min is a data-source mismatch, not a trade -- otherwise it
        # books fake 1000x wins (or fake -99% losses) into the win rate.
        # 27 Oct 2026: REAL incident -- this used to rebase onto "mcap" unconditionally, with no check
        # that the new read itself was sane. One core1 trade got entry_mcap rebased from a real
        # ~$59,303 down to $0.04 (a garbled/near-zero read), turning a $10.76 stake into a fictional
        # $10,152,470 "win" the moment any real price came back in against that near-zero base. A bad
        # read is worse than keeping the original -- only rebase onto a plausible new read
        # (position_state.ENTRY_SANITY_MIN_MCAP_USD, same floor as the real-money path).
        if not pos.get("entry_checked"):
            pos["entry_checked"] = True
            r = mcap / pos["entry_mcap"]
            from executor.position_state import ENTRY_SANITY_MIN_MCAP_USD
            if now - pos["opened_ts"] <= 20 * 60 and (r > 5.0 or r < 0.2) and mcap >= ENTRY_SANITY_MIN_MCAP_USD:
                pos["entry_mcap_original"] = pos["entry_mcap"]
                pos["entry_mcap"] = mcap
                pos["peak_mcap"] = mcap
        if liq is not None and liq < DEAD_LIQUIDITY_USD:
            # Oct 13 2026: this used to sell at the (glitched) mcap read -- a pool with no liquidity
            # booked a fake +$505 on one sprint coin and inflated every stake after it. A drained pool
            # cannot be sold into: the remaining slice is worth ~nothing.
            _sell(pos, pos["remaining"], pos["entry_mcap"] * 0.01, "liquidity collapsed", now)
            closed_now.append(_close(pos, "rug_liquidity", f"liquidity ${liq:,.0f}", now))
            del book[pid]
            continue
        from executor.price_sanity import accept_dict
        if not accept_dict(pos, mcap):
            continue            # data glitch (pair/field switch), not a real move -- see price_sanity
        pos["peak_mcap"] = max(pos["peak_mcap"], mcap)

        if pos.get("strategy") == "runner":
            d = exit_rules.evaluate_exit(pos["entry_mcap"], mcap, pos["peak_mcap"], pos["opened_ts"], now,
                                         exit_rules.runner_pct_for(pos.get("exit_profile")), True, pos["cost_pct"],
                                         profile=pos.get("exit_profile"))
            if d.action == "exit_all":
                _sell(pos, pos["remaining"], mcap, d.exit_type, now)
                closed_now.append(_close(pos, d.exit_type, d.reason, now))
                del book[pid]
            continue
        if pos.get("strategy") == "early":
            if _manage_early(pos, mcap, now):
                closed_now.append(pos)
                del book[pid]
            continue
        if pos.get("strategy") in ("scalper", "quick"):
            if _manage_scalper(pos, mcap, now):
                closed_now.append(pos)
                del book[pid]
            continue
        d = exit_rules.evaluate_exit(pos["entry_mcap"], mcap, pos["peak_mcap"], pos["opened_ts"], now,
                                     pos["remaining"], pos["breakeven_locked"], pos["cost_pct"],
                                     profile=pos.get("exit_profile"))
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
                    "exit_profile": pos.get("exit_profile"), "entry_checked": True,
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

    with _OPEN_LOCK:
        # Positions opened while we were pricing the book must survive this write.
        for _pid, _p in _open().items():
            if _pid not in _orig_ids and _pid not in book:
                book[_pid] = _p
        state.set_value(OPEN_KEY, book)
        if closed_now:
            state.set_value(CLOSED_KEY, (_closed_raw() + closed_now)[-MAX_CLOSED_KEPT:])
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


def _module_of(r: dict) -> str:
    from layers import modules as mods
    return r.get("module") or mods.for_paper(r.get("source"), r.get("signal"))


def module_rows(module_id: str, include_runners: bool = False) -> list:
    return [r for r in _closed() if _module_of(r) == module_id and (include_runners or r.get("strategy") != "runner")]


def module_open_cost(module_id: str) -> float:
    """USD still at risk in this module's open positions (cost x remaining)."""
    return sum(float(p.get("usd") or 0) * float(p.get("remaining", 1.0))
               for p in _open().values() if p.get("strategy") != "runner" and _module_of(p) == module_id)


def module_open_count(module_id: str) -> int:
    return sum(1 for p in _open().values() if p.get("strategy") != "runner" and _module_of(p) == module_id)


def module_scoreboard() -> dict:
    """Per-module paper record: {module_id: {open, n, wins, win_rate, total_pnl_usd, recent[]}}.
    Positions opened before modules existed are classified on read."""
    from layers import modules as mods

    def mid(r):
        return r.get("module") or mods.for_paper(r.get("source"), r.get("signal"))

    out = {}
    rows = [r for r in _closed() if r.get("strategy") != "runner"]
    for m in mods.MODULES:
        mine = [r for r in rows if mid(r) == m["id"]]
        st = _stats(mine)
        out[m["id"]] = {"open": 0, "stats": st,
                        "recent": [{k: r.get(k) for k in ("chain", "token", "signal", "pnl_pct", "pnl_usd", "closed_ts")}
                                   for r in mine[-5:]][::-1]}
    for p in _open().values():
        if p.get("strategy") == "runner":
            continue
        out.setdefault(mid(p), {"open": 0, "stats": _stats([]), "recent": []})["open"] += 1
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

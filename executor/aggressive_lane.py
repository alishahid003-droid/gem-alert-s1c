"""Aggressive Lane: a fully standalone, paper-only, 2x/3x-exposure sprint
lane (Ali, 6 Nov 2026 -- "a separate layer... without touching the existing
code of our 4 modules which are running and the Solana separate modules
which are running... I don't want you to be touching or messing around
with my existing code and altering anything").

WHAT THIS IS
------------
A brand-new, isolated paper-trading experiment testing whether a wider
stake-sizing multiplier (2x or 3x the normal per-trade stake) can turn a
small seed ($100) into a stretch target ($20,000, i.e. 200x) within 48
hours, riding the SAME real alert signals the live system already
generates -- but on its own books, its own state key, its own process,
with its own exit logic. Paper only. Never executes a real trade. Never
sets or reads EXECUTION_ENABLED.

HARD ISOLATION GUARANTEES (why this file touches nothing real)
----------------------------------------------------------------
1. Imports ONLY two things from the existing codebase, both pure/shared
   infrastructure that every part of the system already shares and that
   carries no module-specific trading logic of its own:
     - state.py's get_value/set_value/get_alert_feed/record_runner_heartbeat
       (the generic key-value store + the dashboard alert feed; reading
       this feed does not require importing or calling anything in
       worker_early_watch.py, multichain_sniper.py, or books.py)
     - layers/layer0_scoring.py's fetch_dexscreener_snapshot(chain, address)
       (a pure, read-only network call to DexScreener for live price/mcap/
       liquidity -- no side effects, no shared mutable state, the exact
       function worker_early_watch.py's own position manager already uses
       to re-price positions)
   It does NOT import executor/books.py, executor/paper_ledger.py,
   worker_early_watch.py, multichain_sniper.py, executor/swap_executor.py,
   or executor/phase_target.py. It cannot call check_cashout(),
   check_milestone_ladder(), can_open(), or any real-book function, because
   it never imports the module those functions live in.
2. Uses ONE new, never-before-used state key, "aggressive_lane_v1", for
   all of its own book/ledger/stats. No existing key (books_v1,
   milestone_ladder_v2, paper_closed_positions, dashboard_alert_feed,
   runner_heartbeats, etc.) is ever written to by this file -- only read
   (the alert feed, for signals; nothing else).
3. Has its own runner (run_aggressive_lane.py) invoked as a SEPARATE
   process from local_runner.py. Starting, stopping, or crashing this
   lane cannot start, stop, pause, or affect local_runner.py's threads
   (early-watch, multichain sniper, radar) in any way -- they are
   different OS processes with no shared mutable Python objects.
4. Never touches EXECUTION_ENABLED, never calls any swap_executor.py
   function, never reads or writes a private key or wallet env var.
   100% paper simulation, start to finish.

HOW IT TRADES (paper simulation only)
--------------------------------------
Every cycle: reads the real dashboard_alert_feed (same real signals the
live system already sees -- real tokens, real chains, real score bands),
picks up new high-conviction alerts (Score band A, or layer15 moonshot
tags -- the same conviction markers Ali's real system treats as its best
setups) not already in an open aggressive-lane position, and opens a
paper position sized at EXPOSURE_MULTIPLIER x the lane's own per-trade
base stake (not the real books' stake -- entirely separate sizing, driven
off this lane's own equity). Manages open positions by polling
fetch_dexscreener_snapshot() every cycle and applying a simple, documented
exit ladder (faster/looser than the real system's, intentionally -- this
is the aggressive experiment Ali asked for): take-profit legs at 1.5x/3x/
6x mcap-multiple, hard stop at -35% from entry, and a 90-minute time-stop
if a position never gets going. All exits are proportional paper sells
against this lane's own ledger only.

This file's own target/stop numbers are a reasonable, clearly-labeled
STARTING GUESS for what "aggressive" should mean, calibrated loosely off
the real system's own exit philosophy (take partial profits on strength,
cut losers hard, don't let a stale position sit forever) -- not a
backtested-optimal parameter set. Tune LANE_LADDER / LANE_STOP_LOSS_PCT /
LANE_TIME_STOP_SECONDS below once a day or two of this lane's own paper
data comes in.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import state
from layers.layer0_scoring import fetch_dexscreener_snapshot

# ---------------------------------------------------------------------------
# Config -- all of this lane's own numbers, isolated from every real book's
# config in executor/books.py and .env. Nothing here reads a real-book env
# var; AGGR_* vars are brand new names that cannot collide with anything
# existing config.py or books.py already reads.
# ---------------------------------------------------------------------------

STATE_KEY = "aggressive_lane_v1"

SEED_USD = float(os.environ.get("AGGR_SEED_USD", "100.0"))
EXPOSURE_MULTIPLIER = float(os.environ.get("AGGR_EXPOSURE_MULTIPLIER", "3.0"))  # 2x or 3x per Ali
BASE_STAKE_PCT_OF_EQUITY = float(os.environ.get("AGGR_BASE_STAKE_PCT", "0.12"))  # 12% of lane equity per trade, pre-multiplier
MAX_CONCURRENT_POSITIONS = int(os.environ.get("AGGR_MAX_CONCURRENT", "4"))
TARGET_USD = float(os.environ.get("AGGR_TARGET_USD", "20000.0"))
WINDOW_HOURS = float(os.environ.get("AGGR_WINDOW_HOURS", "48.0"))  # Ali: "stretch this 24 hours target to 48 hours"

# Which real alert-feed entries count as "high conviction enough to paper-buy"
# -- same conviction markers the live system already treats as its best
# setups (band A, or a layer15 moonshot tag), read purely off the tags dict
# the real system already writes into dashboard_alert_feed.
ELIGIBLE_SCORE_BANDS = {"band a", "band s"}
ELIGIBLE_LAYER_PREFIXES = ("layer15",)

# Exit ladder: (mcap_multiple_from_entry, fraction_of_REMAINING_position_to_sell)
LANE_LADDER = (
    (1.5, 0.33),
    (3.0, 0.40),
    (6.0, 1.00),  # sell what's left
)
LANE_STOP_LOSS_PCT = float(os.environ.get("AGGR_STOP_LOSS_PCT", "0.35"))  # hard stop at -35% from entry mcap
LANE_TIME_STOP_SECONDS = float(os.environ.get("AGGR_TIME_STOP_SECONDS", "90")) * 60  # 90 min flat -> close
LANE_TIME_STOP_MOVE_PCT = 0.08  # "never got going" = within +/-8% of entry after the time stop window

CYCLE_SECONDS = float(os.environ.get("AGGR_CYCLE_SECONDS", "15"))


# ---------------------------------------------------------------------------
# State shape (all under STATE_KEY, nothing shared with any real book)
# ---------------------------------------------------------------------------

def _default_state() -> dict:
    now = time.time()
    return {
        "started_ts": now,
        "equity_usd": SEED_USD,
        "banked_usd": 0.0,
        "open_positions": {},   # token_address -> position dict
        "closed_trades": [],    # list of closed-trade dicts, capped below
        "seen_tokens": [],      # token_addresses already acted on (buy attempted), capped below
        "status": "running",    # running | target_hit | window_expired | stopped
        "last_cycle_ts": now,
    }


def _load() -> dict:
    v = state.get_value(STATE_KEY)
    if not isinstance(v, dict):
        v = _default_state()
        state.set_value(STATE_KEY, v)
    return v


def _save(s: dict) -> None:
    state.set_value(STATE_KEY, s)


def get_status() -> dict:
    """Read-only snapshot for status checks / reporting. Safe to call anytime."""
    s = _load()
    elapsed_h = (time.time() - s["started_ts"]) / 3600.0
    return {
        "equity_usd": round(s["equity_usd"], 2),
        "banked_usd": round(s["banked_usd"], 2),
        "combined_usd": round(s["equity_usd"] + s["banked_usd"], 2),
        "open_positions": len(s["open_positions"]),
        "closed_trades": len(s["closed_trades"]),
        "elapsed_hours": round(elapsed_h, 2),
        "window_hours": WINDOW_HOURS,
        "target_usd": TARGET_USD,
        "status": s["status"],
        "progress_pct_of_target": round(100.0 * (s["equity_usd"] + s["banked_usd"]) / TARGET_USD, 2),
    }


# ---------------------------------------------------------------------------
# Signal intake -- real alerts, read-only, via the shared dashboard feed
# ---------------------------------------------------------------------------

def _is_eligible(entry: dict) -> bool:
    tags = entry.get("tags") or {}
    score = str(tags.get("Score", "")).strip().lower()
    layer = str(entry.get("layer", "")).strip().lower()
    if any(band in score for band in ELIGIBLE_SCORE_BANDS):
        return True
    if layer.startswith(ELIGIBLE_LAYER_PREFIXES):
        return True
    return False


def _pull_new_signals(s: dict, limit: int = 100) -> list:
    """Read the real, live dashboard_alert_feed (read-only) and return
    eligible, not-yet-acted-on entries, newest first. Never writes to the
    feed; never touches any other real-system key."""
    feed = state.get_alert_feed(limit=limit)
    seen = set(s.get("seen_tokens") or [])
    out = []
    for entry in feed:
        addr = entry.get("token_address")
        if not addr or addr in seen:
            continue
        if addr in s.get("open_positions", {}):
            continue
        if not _is_eligible(entry):
            continue
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Lane trading logic -- 100% paper, own ledger only
# ---------------------------------------------------------------------------

def _stake_for_entry(s: dict) -> float:
    base = s["equity_usd"] * BASE_STAKE_PCT_OF_EQUITY
    return base * EXPOSURE_MULTIPLIER


def _open_position(s: dict, entry: dict) -> None:
    addr = entry["token_address"]
    chain = entry.get("chain", "solana")
    snap = fetch_dexscreener_snapshot(chain, addr)
    entry_mcap = (snap or {}).get("mcap_usd")
    if not entry_mcap or entry_mcap <= 0:
        return  # no real price available right now -- skip, don't fabricate one
    stake = _stake_for_entry(s)
    if stake <= 0 or stake > s["equity_usd"]:
        return
    if len(s["open_positions"]) >= MAX_CONCURRENT_POSITIONS:
        return
    s["equity_usd"] -= stake
    s["open_positions"][addr] = {
        "chain": chain,
        "token_symbol": entry.get("token_symbol", "?"),
        "entry_ts": time.time(),
        "entry_mcap_usd": entry_mcap,
        "stake_usd": stake,
        "remaining_fraction": 1.0,
        "ladder_fired": [],
        "signal_layer": entry.get("layer"),
        "signal_score": (entry.get("tags") or {}).get("Score"),
    }
    seen = s.get("seen_tokens") or []
    seen.append(addr)
    s["seen_tokens"] = seen[-5000:]


def _close_fraction(s: dict, addr: str, pos: dict, frac_of_original: float, mult: float, why: str) -> None:
    realized = pos["stake_usd"] * frac_of_original * mult
    s["equity_usd"] += realized
    s["closed_trades"] = (s.get("closed_trades") or [])[-2000:] + [{
        "token_address": addr, "token_symbol": pos["token_symbol"], "chain": pos["chain"],
        "entry_mcap_usd": pos["entry_mcap_usd"], "exit_mult": mult, "frac_of_original": frac_of_original,
        "stake_usd": pos["stake_usd"], "realized_usd": realized, "why": why,
        "held_seconds": round(time.time() - pos["entry_ts"], 1), "ts": time.time(),
    }]


def _manage_position(s: dict, addr: str) -> bool:
    """Returns True if the position is now fully closed and should be removed."""
    pos = s["open_positions"][addr]
    snap = fetch_dexscreener_snapshot(pos["chain"], addr)
    if not snap or not snap.get("mcap_usd"):
        return False  # no fresh price this cycle -- leave it open, try again next cycle
    cur_mcap = snap["mcap_usd"]
    mult = cur_mcap / pos["entry_mcap_usd"]
    held_s = time.time() - pos["entry_ts"]

    # Hard stop
    if mult <= (1.0 - LANE_STOP_LOSS_PCT):
        _close_fraction(s, addr, pos, pos["remaining_fraction"], mult, "stop_loss")
        return True

    # Time stop: never got going
    if held_s >= LANE_TIME_STOP_SECONDS and abs(mult - 1.0) <= LANE_TIME_STOP_MOVE_PCT:
        _close_fraction(s, addr, pos, pos["remaining_fraction"], mult, "time_stop_flat")
        return True

    # Ladder legs
    for leg_mult, leg_frac in LANE_LADDER:
        leg_key = f"{leg_mult}"
        if mult >= leg_mult and leg_key not in pos["ladder_fired"]:
            sell_frac_of_original = pos["remaining_fraction"] * leg_frac
            _close_fraction(s, addr, pos, sell_frac_of_original, mult, f"ladder_{leg_mult}x")
            pos["remaining_fraction"] -= sell_frac_of_original
            pos["ladder_fired"].append(leg_key)
            if pos["remaining_fraction"] <= 0.0005:
                return True
    return False


def _check_target_and_window(s: dict) -> None:
    combined = s["equity_usd"] + s["banked_usd"]
    if s["status"] == "running" and combined >= TARGET_USD:
        s["status"] = "target_hit"
        return
    elapsed_h = (time.time() - s["started_ts"]) / 3600.0
    if s["status"] == "running" and elapsed_h >= WINDOW_HOURS:
        s["status"] = "window_expired"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_cycle() -> dict:
    s = _load()
    if s["status"] != "running":
        _save(s)
        return get_status()

    # Manage existing positions first
    for addr in list(s["open_positions"].keys()):
        try:
            closed = _manage_position(s, addr)
        except Exception as exc:  # noqa: BLE001 -- never let one bad snapshot kill the lane
            closed = False
            print(f"[aggressive_lane] manage error on {addr}: {exc}", flush=True)
        if closed:
            del s["open_positions"][addr]

    # Intake new signals
    if len(s["open_positions"]) < MAX_CONCURRENT_POSITIONS:
        for entry in _pull_new_signals(s):
            if len(s["open_positions"]) >= MAX_CONCURRENT_POSITIONS:
                break
            try:
                _open_position(s, entry)
            except Exception as exc:  # noqa: BLE001
                print(f"[aggressive_lane] open error on {entry.get('token_address')}: {exc}", flush=True)

    _check_target_and_window(s)
    s["last_cycle_ts"] = time.time()
    _save(s)
    return get_status()


def main():
    print(f"[aggressive_lane] starting. seed=${SEED_USD} exposure={EXPOSURE_MULTIPLIER}x "
          f"target=${TARGET_USD} window={WINDOW_HOURS}h state_key={STATE_KEY}", flush=True)
    while True:
        try:
            st = run_cycle()
            state.record_runner_heartbeat(
                "aggressive-lane", "local-pc",
                f"status={st['status']} combined=${st['combined_usd']} "
                f"open={st['open_positions']} closed={st['closed_trades']} "
                f"elapsed={st['elapsed_hours']}h/{st['window_hours']}h"
            )
            if st["status"] != "running":
                print(f"[aggressive_lane] lane ended: {st}", flush=True)
                break
        except Exception as exc:  # noqa: BLE001 -- this process must never crash the host shell silently
            print(f"[aggressive_lane] cycle error: {exc}", flush=True)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()

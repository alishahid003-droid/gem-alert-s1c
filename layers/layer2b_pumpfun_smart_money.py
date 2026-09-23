"""
Layer 2b -- Self-built pump.fun "smart money" convergence.

Ali, Sept 23 2026: "can we not have a convergence layer based on top
memecoin traders on pump.fun for pump.fun launches... the current
convergence layer would work for fomo app once a coin graduates there."
Correct, and confirmed in code that same conversation: Layer 2
(layers/layer2_convergence.py) only watches layers.roster.CONVERGENCE_
ROSTER -- Ali's own manually-tracked Fomo traders. Real signal once a coin
has graduated onto Fomo, but structurally unable to catch a coin in its
first minutes on raw pump.fun, since it needs those specific named people
to have already bought first.

REAL RESEARCH, NOT ASSUMED (same night): no free, ready-made "top pump.fun
trader" leaderboard API exists to just plug in.
  - GMGN.ai has exactly this (smart-money/KOL leaderboards -- see
    github.com/GMGNAI/gmgn-skills) but real use requires an applied-for
    personal API key; the public demo key is read-only-testing only, and
    no free ongoing tier is published.
  - Bitquery's pump.fun API (real-time trade streams, smart-money data) is
    a 7-DAY FREE TRIAL, then $39+/month minimum -- not ongoing-free.

So this is built ourselves, for free, off infrastructure this project
already has: the Solana RPC pool (already confirmed live-healthy) watching
pump.fun's own program directly (layers/pumpfun_trades.py decodes real
buy/sell trades), computing each wallet's OWN realized SOL PnL over time.
Our own opinion of who's "smart," from real trades this system observes --
not a third party's.

COLD START, STATED PLAINLY: a wallet must be OBSERVED closing real trades
(a buy AND a matching sell, both correctly decoded) before it can ever be
promoted. No historical backfill -- starts from zero the day it's turned
on. This is NOT a fast fix for catching an RBD-shaped coin today -- it's a
real capability that only gets useful the longer it runs.

PROMOTION THRESHOLD -- first-pass, deliberately conservative, TUNE WITH
REAL DATA (same honesty standard as StonkFun's deployer-tier heuristic
elsewhere in this repo): a wallet needs MIN_CLOSED_TRADES_FOR_PROMOTION+
closed trades, a win rate >= MIN_WIN_RATE_FOR_PROMOTION, AND positive total
realized SOL PnL -- all three, not just one, so a single lucky trade can't
promote a wallet.

REUSES layer2_convergence.detect_convergence() UNCHANGED -- it already
takes `roster` as a parameter, so this doesn't fork the convergence-window
logic, only supplies a different, self-computed roster (wallet addresses
instead of Fomo trader names). ONE COSMETIC SIDE EFFECT, flagged not
hidden: that function also calls layers.roster.tier_of() per wallet for
its "tiers" field -- tier_of() doesn't know about self-computed pump.fun
wallets, so every entry there will read "untracked". Harmless (no crash,
the convergence detection itself is unaffected), just not a meaningful
label for this roster.
"""
from datetime import datetime, timezone
from typing import Optional

import state
from layers.layer2_convergence import detect_convergence, CONVERGENCE_WINDOW, MIN_WALLETS_FOR_CONVERGENCE

MIN_CLOSED_TRADES_FOR_PROMOTION = 5
MIN_WIN_RATE_FOR_PROMOTION = 0.6

SMART_MONEY_ROSTER_KEY = "pumpfun_smart_money_roster"


def wallet_qualifies(stats: Optional[dict]) -> bool:
    if not stats or stats.get("closed_trades", 0) < MIN_CLOSED_TRADES_FOR_PROMOTION:
        return False
    win_rate = stats["wins"] / stats["closed_trades"]
    return win_rate >= MIN_WIN_RATE_FOR_PROMOTION and stats.get("total_realized_sol", 0) > 0


def get_smart_money_roster() -> set:
    return set(state.get_value(SMART_MONEY_ROSTER_KEY) or [])


def _add_to_roster(wallet: str):
    roster = get_smart_money_roster()
    if wallet not in roster:
        roster.add(wallet)
        state.set_value(SMART_MONEY_ROSTER_KEY, sorted(roster))


def process_trade(wallet: str, mint: str, direction: str,
                   sol_delta: Optional[float], ts: Optional[float] = None) -> bool:
    """Records one decoded pump.fun trade and re-checks promotion
    eligibility if it closed a position. Returns True iff this call
    promoted the wallet into the smart-money roster for the first time."""
    stats = state.record_pumpfun_trade(wallet, mint, direction, sol_delta, ts)
    if stats and wallet_qualifies(stats) and wallet not in get_smart_money_roster():
        _add_to_roster(wallet)
        return True
    return False


def detect_pumpfun_convergence(buy_trades: list) -> list:
    """buy_trades: decoded pump.fun buys, each {"wallet", "mint",
    "block_time"} (see layers/pumpfun_trades.decode_trade). Reuses Layer 2's
    own convergence detector against the self-built smart-money roster
    instead of Ali's manual Fomo roster -- same 2+ wallets / 1hr window
    definition, different population. Returns [] with no network/roster
    cost if the roster is still empty (cold start)."""
    roster = get_smart_money_roster()
    if not roster:
        return []
    adapted = []
    for t in buy_trades:
        wallet = t.get("wallet")
        block_time = t.get("block_time")
        if wallet not in roster or not block_time:
            continue
        adapted.append({
            "kol_name": wallet,
            "token_mint": t.get("mint"),
            "traded_at": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat(),
            "action": "buy",
        })
    return detect_convergence(adapted, roster=roster,
                               window=CONVERGENCE_WINDOW, min_wallets=MIN_WALLETS_FOR_CONVERGENCE)

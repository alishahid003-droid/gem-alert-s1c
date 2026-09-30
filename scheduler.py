"""
Orchestrator for Stage 1 (Layers 0/0b, 1, 4) AND Stage 2 (Layers 2, 6, 8, 9).
Meant to be invoked on a schedule (GitHub Actions cron / Render cron job) --
see .github/workflows/poll-fast.yml and poll-slow.yml.

Run modes:
    python scheduler.py --self-test
        Runs the fixture-based test suite and prints a readiness report
        (which layers are configured vs blocked on missing keys, Stage 1
        AND Stage 2). Makes NO network calls. Safe to run anywhere, anytime.

    python scheduler.py --poll-fast
        Discovery only: Layer 1 (deployer alerts), Layer 0b (Mobula Pulse
        scoring), Layer 4 (news), Layer 6 (exit-risk snapshot). Cheap, no
        per-token MadeOnSol scoring -- meant to run FAST (poll-fast.yml,
        every 10 min, unchanged), because entry-timing speed on brand-new
        tokens is the single most valuable thing in this whole system.

    python scheduler.py --poll-slow
        The expensive pieces: Layer 8's per-token MadeOnSol deep scoring
        (risk/holders/bundle, 3 calls/token) and Layers 2+9's wallet-activity
        checks (convergence + sell mirror). Meant to run SLOWER
        (poll-slow.yml, every 15-20 min) -- see README's call-budget section
        for why these specifically are the ones that need it.

    python scheduler.py --poll
        Convenience for local/manual runs: --poll-fast then --poll-slow in
        one process. The two GitHub Actions workflows call the split flags
        separately on their own crons; this is not what runs in production.

Design notes (read before assuming something's missing on purpose):
  - Layer 8's momentum check needs a market-cap time series per token.
    Neither MadeOnSol's nor Mobula's public docs show one clean "MC
    history" endpoint, so this scheduler records one itself into state.py
    (Upstash Redis when configured, else a local JSON file) using the
    market_cap_usd_at_trade field already present on every Layer 2 KOL-feed
    trade -- no extra API call. Accurate once the poller has run a few
    cycles; empty on a cold start. Mobula Pulse items may also carry a
    market-cap field, but the exact field name isn't confirmed in public
    docs -- MC recording for BSC tokens is best-effort until a
    live Pulse response confirms it (see layer0_scoring / README).
  - Layer 1's own call-budget fix (Ali's question, answered): checked whether
    the deployer-reputation tier it needs could be read off a discovery feed
    Layer 0/0b already polls, so Layer 1's own call could be dropped entirely.
    It can't -- there is no separate MadeOnSol "discovery feed" for Solana/RHC
    apart from Layer 1's own /deployer-hunter/alerts call, and that response
    already IS the one Layer 1 reads deployer_tier off directly (see
    layers/layer1_deployer.py's parse_deployer_alerts) -- there was never a
    second, separate reputation lookup riding along to eliminate. The 288/day
    figure comes purely from needing one call per chain (Solana + RHC are
    confirmed separate/parallel MadeOnSol endpoint families, not one combined
    multi-chain response) at the 10-min cadence -- structurally minimal
    already. So the fallback applies: alternate which chain gets checked each
    fast cycle (layers.layer1_deployer.chain_for_cycle, picked deterministically
    from wall-clock time so it self-corrects across missed/late runs rather
    than needing a stored counter) -- halves Layer 1 to 144/day while each
    chain is still checked roughly every 20 minutes. Each chain's
    last-successfully-checked timestamp is tracked in state.py and passed as
    MadeOnSol's `since` param on its next check, so an alert firing during a
    chain's skipped cycle is picked up late, not lost.
  - Layer 8's discovery loop reuses Layer 1's deployer alerts (Solana/RHC)
    and Mobula's Pulse feed (BSC only, scope cut Sept 22 -- was Base/BSC/TON/ETH) -- no new data source. Mobula
    Pulse is fetched ONCE per chain per fast cycle and every item in that
    one response is scored (layer0_scoring.score_mobula_pulse_items) -- no
    per-token re-fetch, and no MadeOnSol cost either way, so it stays on the
    fast cadence. MadeOnSol's per-mint scoring (3 calls/mint) is real money
    against the 200/day cap, so it only runs in the SLOW cycle, off a
    pending-rescore queue (state.py) capped at
    LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE per chain per cycle -- see
    README's call-budget section for the arithmetic. Band-D tokens only
    alert if Layer 8's momentum override actually triggers; otherwise
    they're suppressed same as the original spec intends.
  - Event-triggered re-scoring (replaces a timer-based re-check, per Ali's
    direction): a Solana/RHC token that scored band D is NOT re-deep-scored
    on a fixed schedule. Instead, every time a fresh market-cap point comes
    in for it (from Layer 2's KOL-feed trades, the same cheap metric Layer 8
    already tracks for the momentum check regardless of score), if that MC
    has moved by >= layer8_momentum_override.RESCORE_TRIGGER_FRACTION since
    the last full score, it's queued for the next slow cycle's deep-score
    pass -- see state.queue_rescan / pop_pending_rescans and
    layer8_momentum_override.mc_moved_enough. A quiet coin that failed early
    and never moves costs nothing further. A coin that's actually turning
    around (liquidity locked, holder concentration improves, mint authority
    revoked late) shows movement in its MC first and gets caught. A coin
    that's just pumping toward a dump is still caught by the momentum
    override itself, unchanged, regardless of whether it's ever re-scored.
  - Layer 9's "% of position sold" fix: Layer 2's shared KOL-feed buy trades
    (layers.kol_feed) are used to snapshot/refresh a tracked wallet's
    balance in whatever token it just bought, via Mobula's wallet-portfolio
    endpoint (same one Layer 6 already uses for Ali's own wallets) --
    layers.wallet_balance batches EVERY wallet needing a refresh this cycle
    into ONE Mobula call per chain, not one call per wallet. On a sell event
    the % is computed against whatever was last stored, then the stored
    balance is updated ARITHMETICALLY (prior - amount sold) -- no extra
    Mobula call at sell time at all. Still genuinely "unknown" the first
    time a wallet is ever seen selling something it was never seen buying
    (no prior to compare against) -- not retroactive, exactly as discussed
    and accepted.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# Local-only auto-load of a .env file (Ali, Sept 24 2026 -- python-dotenv was
# already in requirements.txt but nothing ever called it). Must happen BEFORE
# `from config import CONFIG` below -- config.py builds CONFIG from
# os.environ at import time, so loading .env any later leaves CONFIG holding
# stale/missing values even though the file itself is fine (caught live: a
# same-day test run showed every key as "not set" despite .env having all
# six). No-op on GitHub Actions, which has no .env file and gets its secrets
# from repo Secrets instead -- this only matters for --poll-madeonsol on
# Ali's own PC.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import CONFIG
from layers.kol_feed import fetch_kol_feed_both
from layers.layer0_scoring import fetch_mobula_pulse, score_mobula_pulse_items, score_solana_mint, flatten_mobula_pulse_response, fetch_solana_token_deployer, fetch_solana_dev_holding_pct, classify_dev_holding_pct, fetch_solana_wallet_first_seen_ts, classify_deployer_wallet_age, free_recheck_solana_signals, fetch_dexscreener_token_price_usd, fetch_geckoterminal_new_pools, flatten_geckoterminal_pools, score_geckoterminal_pools, GECKOTERMINAL_MAX_GOPLUS_PER_CYCLE
from layers.layer1_deployer import poll_layer1, chain_for_cycle
from layers.layer0c_stonkfun_scoring import poll_layer0c, poll_layer0c_momentum, \
    MOMENTUM_GEM_MIN_MULTIPLE, MOMENTUM_LOOKBACK_HOURS
from layers.layer2_convergence import poll_layer2
from executor.entrypoint import handle_stage1_candidate, handle_stage2_candidate, handle_compound_scalper_candidate
import executor.compound_scalper as compound_scalper
import executor.position_state as position_state
import executor.moonbag as moonbag
import executor.defensive_sell as defensive_sell
import executor.campaign_milestones as campaign_milestones
from layers.layer0_scoring import RawSignals, fetch_dexscreener_snapshot
from layers.layer3_backing_check import check_backing_spike
from layers.layer11_social_buzz import fetch_boost_board, check_buzz
from layers.layer12_caller_channels import fetch_caller_channel_posts, extract_token_addresses
from layers.layer13_fomo_copytrade import (
    fetch_fomo_alerts, fetch_leaderboard, detect_roster_buys_and_theses,
    find_new_trader_candidates,
)
from layers.pumpfun_trades import fetch_recent_signatures, fetch_transaction, decode_trade
from layers.layer2b_pumpfun_smart_money import process_trade as pumpfun_process_trade, \
    detect_pumpfun_convergence, get_smart_money_roster, single_wallet_buy_events
from layers.layer7_correlation import AlertEvent, detect_mega_alerts
import layers.layer10_insider_cluster as layer10
from layers.layer4_news import fetch_cryptopanic_posts, parse_cryptopanic_posts, \
    fetch_coindesk_rss, parse_coindesk_rss, \
    fetch_binance_new_listings, parse_binance_new_listings
from layers.layer6_exit_realizable import fetch_wallet_portfolio
from layers.layer8_momentum_override import detect_momentum_override, mc_moved_enough
from layers.layer0d_point_in_time import fetch_birdeye_ohlcv, summarize_launch_window
from layers.layer9_sell_mirror import poll_layer9, update_balance_after_sell
from layers.roster import SELL_WATCH_ROSTER
from layers.wallet_balance import fetch_wallets_portfolio, extract_balances_for_pairs
from telegram_alert import Alert, send_alert
from utils.http import ApiUnreachable, describe_fetch_failure
import state

# True only inside a GitHub Actions runner (GitHub sets this automatically on
# every job -- see https://docs.github.com/actions/learn-github-actions/variables).
# Used to skip every MadeOnSol-calling layer there (Layer 1, Layer 8, Layer 2+9)
# now that a free MadeOnSol key is confirmed rate-limited by GitHub's rotating
# runner IPs -- those layers instead run from run_poll_madeonsol(), meant to be
# scheduled on Ali's own PC (a stable home IP) via Windows Task Scheduler. This
# flag is what keeps the two from double-alerting on the same event.
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def _runner_where() -> str:
    """Where this cycle ran -- shown next to each runner heartbeat so the
    dashboard/diag can tell a GitHub Actions run from Ali's own PC."""
    return "github-actions" if IS_GITHUB_ACTIONS else "local-pc"


# --- Cycle summary (Ali, Sept 28 2026) -----------------------------------
# Ali's ask: the verbose per-line [layerX] prints below are real and stay
# (still needed to actually debug a broken layer), but he shouldn't have to
# scroll/paste the whole thread to find out what happened this cycle. This
# collects the handful of things he actually asked for -- band counts,
# moonshots, rugs, executions, copy-trade hits, deployer launches, news,
# and which modules were up/down -- as the cycle runs, then
# _print_cycle_summary() prints ONE compact block at the very end that's
# meant to be the only thing he reads on a normal check-in. _STATS is a
# single module-level instance (not thread-safe, but this scheduler is
# always single-process/single-threaded) reset at the top of each of the
# three real entrypoints (run_poll_fast/run_poll_slow/run_poll_madeonsol)
# so a --poll (fast+slow) run prints two summaries, one per real cycle,
# rather than merging two different cadences into one confusing block.
class _CycleStats:
    def __init__(self, name: str):
        self.name = name
        self.bands = {"A": 0, "B": 0, "C": 0, "D": 0}
        self.moonshots = []       # band A/B fires + Layer 0c momentum gems (any chain)
        self.rugs = []            # post-alert-monitor CRATERED downgrades
        self.executions = []      # real Stage1/Stage2 fires (handle_stage1/2_candidate)
        self.copytrades = []      # Fomo (layer2) + pump.fun smart-money (layer2b) + sell-mirror (layer9)
        self.deployer_alerts = [] # layer1 elite/good-tier deployer launches
        self.news = []            # layer4 CoinDesk/CryptoPanic actionable posts
        self.modules = {}         # name -> (ok: bool, detail: str) -- last status seen this cycle
        self.alerts_sent = 0
        self.alerts_failed = 0

    def note_module(self, name: str, ok: bool, detail: str = ""):
        self.modules[name] = (ok, detail)

    def note_band(self, chain: str, label: str, band: str, score, extra: str = ""):
        if band not in self.bands:
            self.bands[band] = 0
        self.bands[band] += 1
        if band in ("A", "B"):
            self.moonshots.append(f"{label} [{chain}] band {band} ({score}/100){extra}")

    def note_rug(self, token: str, chain: str, dd: float):
        self.rugs.append(f"{(token or '?')[:8]} [{chain}] {dd:.0f}% from peak")

    def note_execution(self, stage: int, chain: str, token: str, position_usd: float, conviction):
        self.executions.append(f"STAGE{stage} {(token or '?')[:8]} [{chain}] "
                                f"${position_usd:.2f}, conviction {conviction}")

    def note_copytrade(self, detail: str):
        self.copytrades.append(detail)

    def note_deployer(self, detail: str):
        self.deployer_alerts.append(detail)

    def note_news(self, detail: str):
        self.news.append(detail)

    def note_alert_result(self, sent: bool):
        if sent:
            self.alerts_sent += 1
        else:
            self.alerts_failed += 1


_STATS: "_CycleStats | None" = None  # set by _start_cycle_stats(), read via _stats()


def _start_cycle_stats(name: str) -> "_CycleStats":
    global _STATS
    _STATS = _CycleStats(name)
    return _STATS


def _stats() -> "_CycleStats | None":
    return _STATS


def _short_reason(detail: str, max_len: int = 70) -> str:
    """Collapses a verbose network-failure string (full ProxyError/
    ConnectionError chains, real in this codebase since utils/http.py
    deliberately surfaces the real underlying error rather than swallowing
    it) down to something that fits on one summary line. Ali's ask:
    'clear lines', not a wall of stack trace -- the full detail is still in
    the per-layer [layerX] print above this block for anyone who needs to
    actually debug it."""
    if not detail:
        return ""
    detail = str(detail)
    # Cut at the first parenthetical "(Caused by ...)" -- that's where the
    # real underlying urllib3/requests traceback text starts, and it's
    # exactly the part that's useless for a quick glance.
    for marker in (" (Caused by", " (see fetch_cryptopanic_posts", "\n"):
        idx = detail.find(marker)
        if idx != -1:
            detail = detail[:idx]
    if len(detail) > max_len:
        detail = detail[:max_len].rstrip() + "..."
    return detail


def _print_cycle_summary(stats: "_CycleStats"):
    def _section(title, items, cap=10):
        out = [f"{title}: {len(items)}"]
        for it in items[:cap]:
            out.append(f"  - {it}")
        if len(items) > cap:
            out.append(f"  ... and {len(items) - cap} more")
        return out

    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = ["", "=" * 64, f"SUMMARY -- {stats.name} -- {now_str}", "=" * 64]

    band_line = ", ".join(f"{b}:{stats.bands.get(b, 0)}" for b in ("A", "B", "C", "D"))
    lines.append(f"Coins scored this cycle by band -> {band_line}")
    lines.extend(_section("Moonshots (band A/B or momentum gem)", stats.moonshots))
    lines.extend(_section("Rugs/craters flagged", stats.rugs))
    lines.extend(_section("Copy-trade hits (Fomo + pump.fun tracked wallets)", stats.copytrades))
    lines.extend(_section("Deployer launch alerts (elite/good tier)", stats.deployer_alerts))
    lines.extend(_section("News hits (CoinDesk/CryptoPanic)", stats.news))
    lines.extend(_section("Real executions (Stage1/Stage2 fired)", stats.executions))
    lines.append(f"Alerts delivered: {stats.alerts_sent}  |  Alerts attempted but failed to send: {stats.alerts_failed}")

    working = sorted(name for name, (ok, _d) in stats.modules.items() if ok)
    broken = sorted(stats.modules.items(), key=lambda kv: kv[0])
    lines.append(f"Modules OK this cycle ({len(working)}): {', '.join(working) if working else 'none'}")
    down = [(name, detail) for name, (ok, detail) in broken if not ok]
    lines.append(f"Modules DOWN/failed this cycle ({len(down)}):")
    if not down:
        lines.append("  - none")
    for name, detail in down:
        short = _short_reason(detail)
        lines.append(f"  - {name}: {short}" if short else f"  - {name}")
    lines.append("=" * 64)
    print("\n".join(lines))


def _safe(fn, *args, **kwargs):
    """Runs a network-touching call and converts a genuine network-level
    failure (ApiUnreachable -- DNS, connection refused, blocked outbound
    traffic) into a normal {"ok": False, "reason": ...} result instead of
    letting it crash the whole poll cycle. Every layer already fails closed
    on an ORDINARY api error (bad status code, missing key) by returning
    such a dict itself; utils/http.py deliberately RAISES on a hard
    network-level failure instead of swallowing it (so it's diagnosable),
    but nothing above it was actually catching that until this wrapper --
    found while testing this cycle end-to-end against this sandbox's
    blocked network, which is exactly the failure mode this exists for."""
    try:
        return fn(*args, **kwargs)
    except ApiUnreachable as e:
        return {"ok": False, "reason": f"network unreachable: {e}"}


# Cost-control cap for Layer 8's Solana/RHC path: each mint deep-scored costs
# 3 MadeOnSol calls (risk/holders/bundle). This bounds the SLOW cycle's
# worst case -- see README's call-budget section.
LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE = 3

MOBULA_PULSE_CHAINS = [("bsc", "evm:56"), ("base", "evm:8453")]  # Base re-enabled Sept 24 2026 (Ali: Fomo trades Base too) -- TON/ETH still dropped, scope cut Sept 22, 2026. evm:<numeric chainId> is Mobula's real chain-id format (bug #4, fixed Sept 24 2026) -- "bnb:bnb"/"base:base" were never valid and caused a raw 500.


def readiness_report() -> dict:
    return {
        "state_backend": state.backend(),  # "upstash" (persists across runs) or "local_json" (does not)
        "stage1": {
            "layer0_structural_scoring": CONFIG.layer0_ready(),
            "layer1_deployer_alerts": CONFIG.layer1_ready(),
            "layer0c_stonkfun_discovery": {
                "ready": True,
                "note": "keyless public API, no config/secret needed -- wired Sept 22, 2026, "
                        "runs every FAST cycle, SOL-quoted launches only, bands A/B alert, "
                        "C/D suppressed as noise (see layers/layer0c_stonkfun_scoring.py and README).",
            },
            "layer0c_stonkfun_momentum": {
                "ready": True,
                "note": "keyless, wired Sept 23, 2026 -- catches ANY quote type (including xSOL/stock-paired, which the discovery path above filters out), flags launch-to-peak mcap moves >= "
                        f"{MOMENTUM_GEM_MIN_MULTIPLE}x within {MOMENTUM_LOOKBACK_HOURS}h of launch. "
                        "Threshold picked from ONE real data point (LEVERCAT, ~2744x) -- expect false "
                        "positives on purpose. Each gem alert is tagged executable=True/False so you "
                        "know whether swap_executor can actually route a buy for it.",
            },
            "layer4_news_exchange": CONFIG.layer4_ready(),
            "layer3_backing_check": {
                "ready": CONFIG.layer3_ready(),
                "note": "wired Sept 23, 2026 -- only attempted on a Stage 1 fire (not every scan) "
                        "AND only on the Mobula/BSC path (the only one with a symbol available -- "
                        "see layer0_scoring.py). Adanos free tier is 250 calls/month TOTAL, deliberately "
                        "conserved. Result rides as a [Backing: ...] tag on the SAME alert, not a "
                        "separate one.",
            },
        },
        "layer7_correlation": {
            "ready": True,
            "note": "wired Sept 23, 2026 -- keyless, pure logic. Every real per-token alert (all "
                    "layers except Layer 4's exchange-listing feed) is logged via state.log_alert_event; "
                    "2+ distinct layers firing on the same token within the window sends one extra "
                    "MEGA-ALERT, deduplicated per layer-set so it won't re-fire every cycle.",
        },
        "layer10_insider_cluster": {
            "ready": bool(CONFIG.mobula_api_key),
            "note": "wired Sept 23, 2026 into Layer 9 only -- when a seller isn't on the static "
                    "roster and isn't the deployer, traces its funding chain instead of silently "
                    "ignoring it. Cached once resolved, so this is a one-time cost per wallet, not "
                    "per cycle.",
        },
        "layer2b_pumpfun_smart_money": {
            "ready": True,
            "note": "wired Sept 23, 2026 -- Ali's ask: a convergence layer for real pump.fun "
                    "traders, not just the manually-tracked Fomo roster (which only works "
                    "post-graduation). No free ready-made top-trader API exists (GMGN's real API "
                    "needs an applied-for paid key; Bitquery's pump.fun stream is a 7-day trial "
                    "then $39+/mo) -- built free instead off the Solana RPC pool, decoding real "
                    "pump.fun buy/sell trades ourselves and promoting wallets into a self-computed "
                    "smart-money roster once they show a real track record (5+ closed trades, "
                    "60%+ win rate, positive realized PnL). COLD START: roster starts empty, needs "
                    "real time running before it can promote anyone -- not a fix for catching a "
                    "coin today. Decodes CLASSIC buy/sell instructions only (see "
                    "layers/pumpfun_trades.py -- newer instruction variants are a real, "
                    "acknowledged gap, not silently guessed at).",
        },
        "layer11_social_buzz": {
            "ready": True,
            "note": "wired Sept 23, 2026 -- NOT Twitter/X (no free ongoing tier exists for that, "
                    "see layer11_social_buzz.py's docstring for the real research). Uses "
                    "DexScreener's free, keyless boost-board endpoints as a disclosed proxy for "
                    "real community buzz (real money spent to promote a token right now). Covers "
                    "all three chains (bsc, solana, robinhood_chain -- RHC's real DexScreener slug "
                    "'robinhood' confirmed Sept 23, 2026 while checking Ali's RBD/RobinDog "
                    "question). 2 calls total per poll cycle, not per token -- rides as a "
                    "[Buzz: boosted/surging] tag on the same alert, not a separate one.",
        },
        "layer12_caller_channels": {
            "ready": CONFIG.layer12_ready(),
            "note": "built Sept 28, 2026 -- free, keyless beyond a Telegram bot token (Twitter/X "
                    "confirmed paid-only, see layer11's own note above). Requires the caller bot "
                    "invited as ADMIN of each target channel (Telegram's Bot API has no other way "
                    "to read a channel's posts) plus TELEGRAM_CALLER_CHANNEL_IDS -- both left unset "
                    "deliberately: the channel list has to come from Ali's own real, verified "
                    "channels, not scraped from an SEO 'best telegram groups' listicle with no "
                    "actual track record (see layers/layer12_caller_channels.py's docstring). "
                    "Rides as a [Caller: channel (Nm ago)] tag on a real alert, same convention as "
                    "Backing/Buzz -- never folds into the structural score itself.",
        },
        "layer13_fomo_copytrade": {
            "ready": CONFIG.fomoapi_ready(),
            "note": "built Sept 30, 2026 -- fomoapi.io (independent, unofficial third-party API "
                    "over the Fomo dataset), keyed on Ali's 38-person roster (roster.py). "
                    "Dashboard-only, no Telegram alert (Ali's explicit call). Every roster buy/"
                    "thesis still runs the coin through this system's own Layer 0 structural "
                    "score before showing anything -- corroborating signal, never a blind "
                    "trigger, same rule as Layer 12's Caller tag. Also watches for new traders "
                    "(not on roster) with >= $5k balance, surfaced for Ali's approval, never "
                    "auto-added. Read-only: real Fomo auto-execution needs a SEPARATE $1,000 "
                    "paid trading-account product this codebase deliberately does not wire in "
                    "-- see layers/layer13_fomo_copytrade.py's module docstring.",
        },
        "stage2": {
            "layer2_convergence": CONFIG.layer1_ready(),  # same MadeOnSol key powers the KOL feed
            "layer6_exit_realizable": bool(CONFIG.mobula_api_key) and bool(CONFIG.wallet_addresses()),
            "layer8_momentum_override": {
                "solana_rhc_deep_scoring": CONFIG.layer1_ready(),
                "base_bsc_eth_pulse_scoring": bool(CONFIG.mobula_api_key),
                "note": f"wired, split cadence: Mobula Pulse scoring runs every FAST cycle (no "
                        f"MadeOnSol cost); Solana/RHC deep-scoring (initial elite-tier discovery + "
                        f"event-triggered re-scores) runs every SLOW cycle, capped at "
                        f"{LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE}/chain/cycle. Band-D tokens alert "
                        f"only if the momentum override actually triggers.",
            },
            "layer9_sell_mirror": CONFIG.layer1_ready(),
            "layer9_pct_of_position": {
                "ready": bool(CONFIG.mobula_api_key),
                "note": "wired: balance snapshotted/refreshed on tracked-wallet buys (batched, 1 "
                        "Mobula call/chain/cycle), updated arithmetically on sells -- no extra Mobula "
                        "call at sell time. Unknown only for a wallet's first-ever observed sell with "
                        "no prior buy sighting -- not retroactive.",
            },
        },
        "telegram_output": CONFIG.telegram_ready(),
        "layer8_pending_rescans": state.pending_rescan_count(),
    }


def run_self_test():
    print("=== Fixture test suite (no network) ===")
    rc = subprocess.call([sys.executable, "-m", "pytest", "tests/", "-v"])
    print("\n=== Layer readiness ===")
    import json
    print(json.dumps(readiness_report(), indent=2, default=str))
    return rc


def _alert(alert: Alert, layer: str) -> dict:
    """Sends an alert and logs it for Layer 7's cross-layer correlation
    (Ali, Sept 23 2026: built Sept 22 but never called until tonight).
    Every caller that has a real per-token alert should route through this
    instead of calling send_alert directly -- Layer 4's exchange-listing
    alerts are the one exception (no specific token/mint involved, nothing
    for Layer 7 to correlate). If 2+ DISTINCT layers fire on the same token
    within the correlation window, sends one extra MEGA-ALERT, deduplicated
    per distinct layer-set so it doesn't re-fire every cycle for the same
    convergence."""
    send_res = send_alert(alert)
    state.log_full_alert(layer, alert.chain, alert.token_symbol, alert.token_address,
                          alert.headline, dict(alert.tags))
    token = alert.token_address
    if token and token != "n/a":
        state.log_alert_event(token, layer)
        events = [AlertEvent(token, lyr, datetime.fromtimestamp(ts, tz=timezone.utc))
                  for ts, lyr in state.get_recent_alert_events(token)]
        for mega in detect_mega_alerts(events):
            layer_key = ",".join(mega.layers)
            if state.get_value(f"mega_alert_sent:{token}") == layer_key:
                continue  # already sent this exact layer-set for this token
            mega_alert = Alert(alert.token_symbol, token, alert.chain,
                                f"MEGA-ALERT: {len(mega.layers)} independent layers converged")
            mega_alert.set_tag("Chain", alert.chain)
            mega_alert.set_tag("MEGA-ALERT", f"{len(mega.layers)} layers ({', '.join(mega.layers)}) "
                                              f"within {mega.window_minutes:.0f}min")
            mega_send = send_alert(mega_alert)
            print(f"[layer7] MEGA-ALERT {token[:8]} ({layer_key}) -> {mega_send}")
            state.set_value(f"mega_alert_sent:{token}", layer_key)
            if _stats():
                _stats().moonshots.append(f"MEGA-ALERT {token[:8]} [{alert.chain}] {len(mega.layers)} layers converged")
    if _stats():
        sent = send_res.get("sent") if isinstance(send_res, dict) else bool(send_res)
        _stats().note_alert_result(bool(sent))
    return send_res


def _handle_scored(scored: dict, chain: str, source: str, mc: float = None, board: dict = None,
                    is_pregraduation: bool = None) -> bool:
    """Sends an alert for one scored token if appropriate, and records the
    score for future event-triggered re-scoring (state.set_last_score).
    Returns True iff an alert was actually delivered.

    Also the shared execution wiring point for ALL structural scoring on
    ALL THREE chains (Ali, Sept 23 2026: "check it for Solana Robinhood and
    BSC all three" -- this single function is called from both the Mobula
    Pulse path (BSC today, see MOBULA_PULSE_CHAINS) and the MadeOnSol
    deep-score path (Solana + Robinhood Chain, via Layer 8's queue), so
    wiring it here covers every chain this system structurally scores, not
    just the one Ali happened to notice. Mirrors tonight's earlier Layer 2
    fix: a structural score of A/B is one of executor.triggers.evaluate_
    stage1's three OR'd fire conditions (the other two -- deployer tier,
    wallet convergence count -- aren't known at this point in the code, so
    they're passed as None/0, meaning this path can only ever fire on the
    score-band condition, which is correct: it's the only signal this
    function actually has). STALE NOTE CORRECTED Sept 25, 2026:
    handle_stage1_candidate now DOES call the real execute_buy_* path on a
    fire (see executor/entrypoint.py's _attempt_buy_and_record_fill) -- the
    "does not move real money" claim that used to be here is no longer
    true of this function, only of the fact that EXECUTION_ENABLED stays
    "false" until the real (non-mocked) network tests in TASKS_LEFT pass.
    That flag -- not this function's own logic -- is the only thing
    standing between a Stage 1 fire here and a real on-chain transaction."""
    if "error" in scored or scored.get("score") is None:
        return False
    mint = scored["address"]
    sr = scored["score"]
    if _stats():
        symbol = (scored.get("raw") or {}).get("symbol")
        _stats().note_band(chain, symbol or (mint or "?")[:8], sr.band, sr.score)
    # -- Deployer wallet resolution, moved up ahead of the Stage 1 buy
    # decision (it used to live further down, purely for the dev-holding/
    # wallet-age tags -- see that block below, which now reuses this same
    # lookup instead of fetching it twice). Solana only, same RPC
    # restriction as always.
    #
    # Sept 30 2026, Ali: "build that and also do that vice versa on if any
    # of my trade made loss or the developer rugged to add that in that
    # list to blacklist this developer and avoid coins launched from him."
    # Our own deployer track record (state.py's record_deployer_outcome,
    # written every time executor/position_state.py's close_position
    # resolves a real win/loss/rug on a closed position) is the ONLY thing
    # that can mark a wallet blacklisted or trusted here -- never an
    # external/unverified list, only wallets THIS system has actually
    # traded against before and watched the outcome of. A blacklisted
    # deployer hard-blocks Stage 1 on this token outright, before the
    # score-band/convergence checks even run. A trusted deployer (2+ real
    # wins, zero rugs, net positive P&L) is passed through as MadeOnSol's
    # "good" tier for the existing OR condition in
    # triggers.evaluate_stage1 -- that fire path was already built (Sept
    # 28) but was permanently dead code here because deployer_tier was
    # always passed as None; this is real data for it now.
    deployer_wallet = None
    deployer_blacklisted = False
    if mint and chain == "solana":
        try:
            deployer_wallet = fetch_solana_token_deployer(mint)
        except ApiUnreachable as e:
            print(f"[layer0] {mint[:8]} deployer lookup skipped: network unreachable ({e})")
            deployer_wallet = None
        if deployer_wallet and state.is_deployer_blacklisted(deployer_wallet):
            deployer_blacklisted = True
            print(f"[layer0] {mint[:8]} deployer {deployer_wallet[:8]} is BLACKLISTED "
                  f"(rugged/lost us money before) -- Stage 1 skipped regardless of "
                  f"score/convergence")

    if mint:
        state.set_last_score(mint, sr.score, sr.band, mc)
        if deployer_blacklisted:
            stage1 = {"fired": False, "stage": "stage1",
                      "reason": f"deployer {deployer_wallet[:8]} blacklisted (past rug/loss)"}
        else:
            effective_deployer_tier = (
                "good" if (deployer_wallet and state.is_deployer_trusted(deployer_wallet)) else None
            )
            stage1 = handle_stage1_candidate(
                chain, mint, score_band=sr.band, deployer_tier=effective_deployer_tier,
                convergence_count=0, entry_mcap=mc,
                signal_coverage=getattr(sr, "signal_coverage", None),
            )
        # Per-token auto-buy verdict for the dashboard's Alerts tab (Sept 30
        # 2026, Ali: "would these trades have been executed?") -- the real
        # trigger decision, recorded whether it fired or not.
        if sr.band in ("A", "B") or stage1.get("fired"):
            state.record_autobuy_verdict(mint, chain, stage1)
        if stage1["fired"]:
            print(f"[layer0/8:{chain}] STAGE1 FIRED for {mint[:8]} -- "
                  f"${stage1['position_usd']:.2f}, conviction {stage1['conviction_score']}, "
                  f"ladder={stage1['trim_ladder']} ({stage1['reason']})")
            if _stats():
                _stats().note_execution(1, chain, mint, stage1["position_usd"], stage1["conviction_score"])

        # Compound scalper (Ali, Sept 29 2026) -- independent isolated-pool
        # entry path, draws its own gate off the same score band. No-op
        # unless COMPOUND_SCALPER_ENABLED="true" and the pool was manually
        # started -- see executor/compound_scalper.py's own docstring.
        liq_usd = (scored.get("raw") or {}).get("liquidity_usd")
        scalp = handle_compound_scalper_candidate(
            chain, mint, score_band=sr.band, entry_mcap=mc, liquidity_usd=liq_usd,
        )
        if scalp["fired"]:
            print(f"[compound-scalper:{chain}] SCALP OPENED {mint[:8]} -- "
                  f"${scalp['position_usd']:.2f} ({scalp['reason']})")

    # -- Layer 3: Reddit-backing check (Ali, Sept 23 2026 -- built Sept 7,
    # never called). Only attempted on an actual Stage 1 fire, not every
    # scored token -- Adanos's free tier is 250 CALLS/MONTH TOTAL, so this
    # is deliberately reserved for real candidates, not spent on every scan.
    # Only the Mobula/BSC path carries a symbol at all (score_solana_mint's
    # MadeOnSol path returns no symbol -- see layer0_scoring.py) -- querying
    # Reddit by a truncated mint address on the Solana/RHC path would just
    # burn quota on near-guaranteed misses, so this stays BSC-only until a
    # real symbol source exists for the other chains. --
    backing_tag = None
    if mint and source == "mobula" and CONFIG.layer3_ready():
        symbol = (scored.get("raw") or {}).get("symbol")
        if symbol:
            backing = check_backing_spike(symbol)
            if backing.get("ok") and backing.get("result") and backing["result"].tag != "none":
                backing_tag = backing["result"].tag
                print(f"[layer3] {mint[:8]} ({symbol}) backing={backing_tag}")

    # -- Layer 11: social/community buzz proxy (Ali, Sept 23 2026 -- real
    # Twitter/X data has no free ongoing tier, see layer11_social_buzz.py's
    # docstring; DexScreener boosts used as an honest, disclosed proxy
    # instead). Keyless and free, so attempted on every chain DexScreener
    # covers, not gated behind CONFIG -- board is fetched once per poll
    # cycle by the caller and passed in here, so this costs zero extra
    # calls per token. --
    buzz_tag = None
    if mint and board:
        buzz = check_buzz(board, chain, mint)
        if buzz.tag != "none":
            buzz_tag = buzz.tag
            print(f"[layer11] {mint[:8]} buzz={buzz_tag} (total_amount={buzz.total_amount})")

    # -- Layer 12: Telegram caller-channel mention (Ali, Sept 28 2026, see
    # layers/layer12_caller_channels.py's docstring). Pure state lookup --
    # poll_layer12_caller_channels already did the real work of recording
    # this earlier in the cycle, so this costs no extra call at all. Never
    # gates or scores anything, corroborating color only, same as
    # Backing/Buzz above -- a token still has to pass real structural
    # scoring on its own merits to reach this point in the first place. --
    caller_tag = None
    if mint:
        caller = state.get_caller_signal(mint)
        if caller:
            mins_ago = max(0, (time.time() - caller.get("ts", time.time())) / 60)
            caller_tag = f"{caller.get('channel', 'unknown channel')} ({mins_ago:.0f}m ago)"
            print(f"[layer12] {mint[:8]} caller_mention={caller_tag}")

    # -- Dev-wallet current-holding-% (Ali, Sept 28 2026 -- checklist item,
    # built same night as fetch_solana_token_deployer/
    # fetch_solana_dev_holding_pct in layer0_scoring.py). Solana only --
    # both RPC helpers are Solana-specific (see their docstrings), and this
    # rides as a tag on the alert same as Backing/Buzz above, NOT folded
    # into the 100-point structural score -- that score's weights were
    # deliberately rebalanced Sept 25 2026 off real backtest evidence, and
    # changing it again without the same kind of evidence isn't a call to
    # make solo overnight. A visible tag gets this in front of Ali on every
    # real alert without touching a score that's already been tuned once
    # for a documented reason. Only costs 2 extra RPC calls, both free,
    # both already used elsewhere tonight -- no MadeOnSol budget spent. --
    # deployer_rep_tag: surfaces our own blacklisted/trusted verdict on the
    # alert itself (Sept 30 2026) -- the blacklist gate above already
    # blocked a real Stage 1 buy, but a blacklisted deployer's token can
    # still reach here via a non-Stage-1 alert path (e.g. a band-D momentum
    # override), so this keeps that risk visible even when nothing was
    # bought. A trusted verdict shows for context too, same as Dev
    # holding/Deployer age below.
    dev_tag = None
    age_tag = None
    deployer_rep_tag = None
    if mint and chain == "solana":
        # deployer_wallet was already resolved above (ahead of the Stage 1
        # decision) -- reused here rather than fetched a second time.
        try:
            dev_pct = fetch_solana_dev_holding_pct(mint, deployer_wallet) if deployer_wallet else None
        except ApiUnreachable as e:
            # Same real gap _safe() exists for elsewhere in this file --
            # this RPC helper can raise on a genuine network-level failure
            # (not just an ordinary API error, which it already handles),
            # and this call site had no guard for that until now. Caught
            # here rather than letting one network blip crash the whole
            # poll cycle over an optional tag.
            print(f"[layer0] {mint[:8]} dev-holding check skipped: network unreachable ({e})")
            dev_pct = None
        if deployer_wallet:
            rep = state.get_deployer_reputation(deployer_wallet)
            if rep["tier"] in ("trusted", "blacklisted"):
                deployer_rep_tag = (f"{rep['tier']} ({rep['wins']}W/{rep['losses']}L/"
                                     f"{rep['rugs']}rug, net {rep['net_pnl_usd']:+.2f} USD)")
                print(f"[layer0] {mint[:8]} deployer_reputation={deployer_rep_tag}")
        dev_tier = classify_dev_holding_pct(dev_pct)
        if dev_tier in ("notable", "risk"):
            dev_tag = f"{dev_tier} ({dev_pct*100:.1f}%)"
            print(f"[layer0] {mint[:8]} dev_holding={dev_tag}")

        # -- Deployer wallet age/freshness (Ali, Sept 28 2026 -- the safe
        # adjacent signal to literal deployer rug-history, see
        # fetch_solana_wallet_first_seen_ts's docstring for why the literal
        # version (past-launch outcomes) isn't built tonight: it needs
        # decoding pump.fun's own "create" instruction, and this codebase
        # has a deliberate existing rule against guessing a discriminator
        # without confirmed real traffic. Reuses the already-found
        # deployer_wallet from the dev-holding check above -- no extra
        # deployer lookup needed. --
        if deployer_wallet:
            try:
                first_seen = fetch_solana_wallet_first_seen_ts(deployer_wallet)
            except ApiUnreachable as e:
                print(f"[layer0] {mint[:8]} deployer-age check skipped: network unreachable ({e})")
                first_seen = None
            age_tier = classify_deployer_wallet_age(first_seen)
            if age_tier in ("fresh", "new"):
                age_hours = (time.time() - first_seen) / 3600.0 if first_seen else None
                age_tag = f"{age_tier} wallet ({age_hours:.1f}h old)" if age_hours is not None else age_tier
                print(f"[layer0] {mint[:8]} deployer_age={age_tag}")

    if sr.band == "D":
        mc_hist = state.get_mc_history(mint) if mint else []
        mom = detect_momentum_override(sr.band, mc_hist)
        if not mom.triggered:
            # Soft-fail watch list (Ali, Sept 28 2026 -- see state.py's
            # watch_add docstring for the real gap this closes): a token no
            # tracked KOL wallet ever trades never gets a fresh MC point, so
            # _maybe_queue_rescan's MC-triggered path never re-checks it --
            # this gives it a second, MC-independent path back into
            # consideration via cheap free-RPC signals. Solana only (the
            # free signals are SPL-specific -- see
            # free_recheck_solana_signals -- RHC uses a different token
            # standard and isn't covered by them).
            if mint and chain == "solana":
                state.watch_add(mint, chain, bool(is_pregraduation), sr.score, sr.reasons)
            return False  # fails safety score, no momentum yet -- suppressed, per spec
        alert = Alert((mint or "?")[:8], mint, chain, "HIGH-RISK MOMENTUM override")
        alert.set_tag("Chain", chain).set_tag("Score", f"{sr.score}/100 (band D)")
        alert.set_tag("HIGH-RISK MOMENTUM", mom.reason)
    else:
        # Cleared band D on a real re-score (soft-fail watch or MC-triggered
        # -- either path lands here) -- drop it from the watch list so it
        # doesn't sit there stale; this IS the "coin quietly became solid"
        # case Ali described, now actually alerting instead of being stuck
        # silently rejected forever.
        if mint:
            state.watch_remove(mint)
        alert = Alert((mint or "?")[:8], mint, chain, f"Layer 0{'b' if source == 'mobula' else ''} structural score")
        alert.set_tag("Chain", chain).set_tag("Score", f"{sr.score}/100 (band {sr.band})")
    if backing_tag:
        alert.set_tag("Backing", backing_tag)
    if buzz_tag:
        alert.set_tag("Buzz", buzz_tag)
    if caller_tag:
        alert.set_tag("Caller", caller_tag)
    if dev_tag:
        alert.set_tag("Dev holding", dev_tag)
    if age_tag:
        alert.set_tag("Deployer age", age_tag)
    if deployer_rep_tag:
        alert.set_tag("Deployer track record", deployer_rep_tag)
    layer_name = "layer0b" if source in ("mobula", "geckoterminal") else "layer0"
    send_res = _alert(alert, layer_name)
    print(f"[layer0/8:{chain}] {mint} band {sr.band} -> {send_res}")
    # Post-alert monitoring pass (Ali, Sept 28 2026 -- see state.py's
    # post_alert_monitor_add docstring): every real alert this function
    # actually delivers gets a one-time follow-up entry, checked once real
    # price history exists for the 15-60 min window. Birdeye-supported
    # chains (solana/base/bsc/ethereum) need nothing extra here --
    # _run_post_alert_monitor_cycle re-derives the whole window from real
    # Birdeye OHLCV at check time. Robinhood Chain (no Birdeye mapping) gets
    # a real DexScreener price snapshot taken NOW instead, so its check can
    # compare against a second snapshot later -- see
    # fetch_dexscreener_token_price_usd's docstring for why this closes
    # that gap without needing Birdeye at all. A snapshot fetch that fails
    # (no DexScreener pair yet, network error) just isn't queued -- nothing
    # to compare against later, an honest skip, not a silent guess.
    if mint and send_res.get("sent") and chain in POST_ALERT_MONITOR_SUPPORTED_CHAINS:
        price_at_alert = None
        if chain == "robinhood_chain":
            price_at_alert = _safe(fetch_dexscreener_token_price_usd, chain, mint)
            if not isinstance(price_at_alert, (int, float)):
                price_at_alert = None
        if chain != "robinhood_chain" or price_at_alert is not None:
            state.post_alert_monitor_add(mint, chain, alert.headline, sr.band, sr.score,
                                          price_at_alert=price_at_alert)
    return bool(send_res.get("sent"))


def _maybe_queue_rescan(token: str, chain: str, new_mc: float):
    """Called whenever a fresh MC point comes in for a token that may have
    been deep-scored before. Only re-queues if the last full score was a
    failing 'D' AND the MC has moved enough since then to be worth another
    look -- see layer8_momentum_override.RESCORE_TRIGGER_FRACTION."""
    if not token or new_mc is None:
        return
    last = state.get_last_score(token)
    if not last or last.get("band") != "D":
        return
    if mc_moved_enough(last.get("mc"), new_mc):
        is_pregrad = chain == "solana"
        state.queue_rescan(token, chain, is_pregrad)
        print(f"[layer8] {token[:8]} MC moved {last.get('mc')} -> {new_mc} since its failing score -- "
              f"queued for re-score")


# Soft-fail watch cycle (Ali, Sept 28 2026) -- see state.py's watch_add
# docstring for the real gap this closes and free_recheck_solana_signals'
# docstring for why this costs ZERO MadeOnSol budget. Capped per cycle same
# philosophy as LAYER2B_MAX_SIGNATURES_PER_CYCLE below -- the free Solana RPC
# pool (executor/rpc_pool.py) has no real SLA, so this is additive load on
# it, not a MadeOnSol budget concern (this path spends none).
SOFT_FAIL_RECHECK_MAX_PER_CYCLE = 8

# Thresholds a free re-check needs to clear before this queues a real
# 3-call MadeOnSol re-score on a token that already failed once -- kept
# deliberately stricter than "any improvement at all" (burning real,
# budget-capped calls on a token that's barely better than before isn't
# worth it). Both mirror the same structural bar score_token itself already
# uses: top10 concentration's credit curve zeroes out at >=60%, so <=35%
# is comfortably in "real, not marginal" territory; holder growth's credit
# curve maxes out at 30/hr, so >=10/hr is a genuine signal, not noise.
SOFT_FAIL_WATCH_TOP10_OK = 0.35
SOFT_FAIL_WATCH_HOLDER_GROWTH_OK = 10.0


def _run_soft_fail_watch_cycle() -> int:
    """Sweeps state.get_soft_fail_watch() -- D-band Solana tokens
    _handle_scored couldn't clear and that never got MC-triggered back into
    _maybe_queue_rescan's path (no tracked KOL wallet ever traded them, so
    no fresh MC point ever arrived -- see state.py's watch_add docstring).
    Spends ZERO MadeOnSol budget -- only the two free RPC signals already
    wired for real scoring (layer0_scoring.free_recheck_solana_signals). A
    token that clears either bar above gets queued into the SAME
    state.queue_rescan/pop_pending_rescans path _maybe_queue_rescan already
    uses -- the next slow cycle spends one real 3-call MadeOnSol re-score on
    it, and _handle_scored's own band-D-cleared branch fires a normal alert
    and drops it from the watch list if it actually clears. No separate
    alerting path needed -- this only decides WHETHER a real re-score is
    worth spending, not what happens after. A token that doesn't clear
    either bar just gets last_checked_ts bumped and ages out naturally past
    state.SOFT_FAIL_WATCH_MAX_AGE_SECONDS (pruned on state.get_soft_fail_watch's
    own read). Returns the number newly queued for a real re-score."""
    watch = state.get_soft_fail_watch()
    if not watch:
        return 0
    # Oldest-checked-first so the whole list gets a turn across successive
    # cycles instead of the same few tokens (whatever sorts first) hogging
    # every cycle's cap -- same round-robin intent as Layer 8's overflow
    # re-queue.
    watch.sort(key=lambda w: w.get("last_checked_ts", 0))
    to_check = [w for w in watch if w.get("chain") == "solana"][:SOFT_FAIL_RECHECK_MAX_PER_CYCLE]
    queued = 0
    for w in to_check:
        token = w["token"]
        sig = _safe(free_recheck_solana_signals, token)
        state.watch_touch(token)
        if not isinstance(sig, dict):
            continue
        top10 = sig.get("top10_holder_pct")
        growth = sig.get("holder_growth_rate_per_hr")
        improved = (top10 is not None and top10 <= SOFT_FAIL_WATCH_TOP10_OK) or \
                   (growth is not None and growth >= SOFT_FAIL_WATCH_HOLDER_GROWTH_OK)
        if improved:
            reasons = []
            if top10 is not None and top10 <= SOFT_FAIL_WATCH_TOP10_OK:
                reasons.append(f"top10 now {top10*100:.1f}%")
            if growth is not None and growth >= SOFT_FAIL_WATCH_HOLDER_GROWTH_OK:
                reasons.append(f"+{growth:.0f} holders/hr")
            print(f"[soft-fail-watch] {token[:8]} shows real improvement ({', '.join(reasons)}) -- "
                  f"queued for a real re-score")
            state.queue_rescan(token, w["chain"], w.get("is_pregraduation", True))
            # The next real re-score (via the queue above) is the
            # authoritative verdict either way -- leaving this entry in the
            # watch list too would just double-check the same token via two
            # separate paths for no benefit.
            state.watch_remove(token)
            queued += 1
    if to_check:
        print(f"[soft-fail-watch] checked {len(to_check)}/{len(watch)} watched token(s), "
              f"{queued} queued for a real re-score (0 MadeOnSol calls spent)")
    return queued


# Post-alert monitoring pass (Ali, Sept 28 2026 -- see state.py's
# post_alert_monitor_add docstring for the gap this closes: "a flagged
# token that rugs 10 minutes later still shows as a live alert with no
# correction"). Two chain groups, two real data sources -- Birdeye has no
# Robinhood Chain mapping (see layers.layer0d_point_in_time.fetch_birdeye_
# ohlcv), so RHC alerts got no follow-up at all when this pass first
# shipped; closed same night via a DexScreener price-snapshot compare
# instead (see fetch_dexscreener_token_price_usd's docstring) -- RHC is the
# one chain this system actually trades that Birdeye can't cover, and it's
# also the one Track B's real-money go-live plan includes, so leaving it
# unmonitored wasn't acceptable once noticed.
BIRDEYE_SUPPORTED_CHAINS = {"solana", "base", "bsc", "ethereum"}
DEXSCREENER_SNAPSHOT_CHAINS = {"robinhood_chain"}
POST_ALERT_MONITOR_SUPPORTED_CHAINS = BIRDEYE_SUPPORTED_CHAINS | DEXSCREENER_SNAPSHOT_CHAINS
POST_ALERT_MONITOR_MAX_PER_CYCLE = 8
# Same threshold as layer0_scoring's launch-window collapse override
# (real backtest evidence, Sept 28 2026) -- a token whose price is down
# 60%+ from its post-alert peak (Birdeye path) or from its alert-time price
# (DexScreener snapshot path) by the time this checks it is the same
# "pumped then got dumped on" shape that override already proved on, not a
# separately-guessed number. Applied identically on both paths so a coin
# isn't judged by a stricter or looser bar just because of which chain it's
# on.
POST_ALERT_CRATER_DRAWDOWN_PCT = -60.0


def _send_post_alert_downgrade(token: str, chain: str, q: dict, dd: float, now: float) -> dict:
    """Shared DOWNGRADE-alert builder for both check paths below -- same
    alert shape regardless of whether the drawdown came from real Birdeye
    OHLCV or a DexScreener snapshot compare, so Ali sees one consistent
    format either way."""
    alert_ts = q.get("alert_ts", now)
    downgrade = Alert(token[:8], token, chain, f"ALERT DOWNGRADE -- {q.get('headline', 'previous alert')}")
    downgrade.set_tag("Chain", chain)
    downgrade.set_tag("Score", f"{q.get('score')}/100 (band {q.get('band')}, at time of original alert)")
    downgrade.set_tag("Exit-risk", f"price down {dd:.1f}% from its post-alert peak within "
                                    f"{(now - alert_ts) / 60:.0f} min -- likely a rug/dump in progress")
    send_res = send_alert(downgrade)
    # Real gap found and fixed Sept 28 2026: this was the one alert path in
    # the whole codebase that never wrote to state.log_full_alert -- every
    # other alert does, which is what the dashboard actually reads (see
    # dashboard.py). It only ever reached Ali via Telegram or a live
    # terminal print, both of which he can be away from -- a fully
    # unattended run (GitHub Actions, or Telegram unreachable/disabled)
    # would silently lose the one alert that matters most: "this thing you
    # bought is cratering." Now logged like everything else.
    state.log_full_alert("post_alert_downgrade", chain, downgrade.token_symbol, downgrade.token_address,
                          downgrade.headline, dict(downgrade.tags))
    print(f"[post-alert-monitor] {token[:8]} CRATERED ({dd:.1f}% from peak) -- downgrade sent -> {send_res}")
    if _stats():
        _stats().note_rug(token, chain, dd)
        sent = send_res.get("sent") if isinstance(send_res, dict) else bool(send_res)
        _stats().note_alert_result(bool(sent))
    return send_res


def _run_position_management_cycle() -> dict:
    """Closes a real, live gap found and fixed Sept 28 2026 (Ali: "you fix
    it live let the test run"). moonbag.check_and_trim and
    defensive_sell.check_and_defend were both fully built and tested, but
    until now NOTHING in any of Ali's three real scheduled tasks (fast/
    slow/MadeOnSol crons) ever called them -- the only caller was
    worker_stonkfun_snipe.py's own manage_open_stonkfun_positions(), which
    isn't wired into any scheduled task either. With EXECUTION_ENABLED=true
    and a real Solana signing key live, that meant real buys could sit
    open with zero automated profit-taking or rug-defense follow-up. This
    runs every fast cycle (see run_poll_fast, called right after the
    post-alert-monitor pass) against EVERY open position this executor
    holds, on ANY chain/source -- not just StonkFun-sourced ones the way
    worker_stonkfun_snipe.py's version is scoped.

    Re-prices each open position via fetch_dexscreener_snapshot (one call
    per position, covers solana/bsc/base/robinhood_chain -- same chain
    coverage already proven live for fetch_dexscreener_token_price_usd).
    A position DexScreener can't currently price (delisted pair, network
    hiccup) is skipped for this cycle rather than guessed at -- fail-closed,
    same posture as the rest of this module.

    Per position, in order: moonbag trim check (fires at most one rung),
    then the account-wide sequential campaign-milestone check ($25k -> $150k
    auto-close, $500k-$1M alert-only -- Ali, Sept 28 2026 message; see
    executor/campaign_milestones.py's own docstring), then the defensive
    rug-exit check (needs a previous liquidity reading to detect a DROP,
    not just a low absolute number -- state stores the last-seen liquidity
    per chain:token, same pattern worker_stonkfun_snipe.py already uses,
    so this is correct across cycles/restarts too)."""
    managed, trims_fired, milestones_fired, defends_fired = [], [], [], []
    for pos in position_state.list_open_positions():
        chain, token = pos.get("chain"), pos.get("token")
        if not chain or not token:
            continue

        snap = _safe(fetch_dexscreener_snapshot, chain, token)
        if not (isinstance(snap, dict) and snap.get("mcap_usd") is not None):
            continue
        current_mcap = snap["mcap_usd"]
        current_liq = snap.get("liquidity_usd")
        managed.append(f"{chain}:{token[:8]}")

        trim_result = moonbag.check_and_trim(chain, token, current_mcap)
        if trim_result is not None:
            trims_fired.append(trim_result)
            print(f"[position-mgmt:{chain}] MOONBAG TRIM {token[:8]}: {trim_result['trim_decision'].reason}")

        milestone_result = _safe(campaign_milestones.check_and_apply, chain, token, current_mcap)
        if isinstance(milestone_result, dict) and milestone_result.get("action"):
            milestones_fired.append(milestone_result)
            print(f"[position-mgmt:{chain}] CAMPAIGN MILESTONE {token[:8]}: "
                  f"{milestone_result['action']} (~${milestone_result.get('value_usd', 0):,.0f})")

        prev_liq_key = f"exec_position_prev_liq:{chain}:{token}"
        prev_liq = state.get_value(prev_liq_key)
        if prev_liq is not None and current_liq is not None:
            prev_signals = RawSignals(liquidity_usd=prev_liq)
            curr_signals = RawSignals(liquidity_usd=current_liq)
            defend_result = defensive_sell.check_and_defend(chain, token, prev_signals, curr_signals)
            if defend_result is not None:
                defends_fired.append(defend_result)
                print(f"[position-mgmt:{chain}] DEFENSIVE EXIT {token[:8]}: {defend_result['rug_reasons']}")
        if current_liq is not None:
            state.set_value(prev_liq_key, current_liq)

    if managed:
        print(f"[position-mgmt] re-priced {len(managed)} open position(s); "
              f"{len(trims_fired)} trim(s), {len(milestones_fired)} milestone action(s), "
              f"{len(defends_fired)} defensive exit(s) fired this cycle.")
    return {"managed": managed, "trims_fired": trims_fired,
            "milestones_fired": milestones_fired, "defends_fired": defends_fired}


def _run_compound_scalper_cycle() -> dict:
    """Manages the compound-scalper pool's one open position, if any --
    that pool's state is deliberately separate from position_state.py's
    Stage 1/2 positions (see executor/compound_scalper.py's module
    docstring for why: isolated capital, sequential-only, its own circuit
    breaker), so it gets its own small cycle here rather than folding into
    _run_position_management_cycle above. A true no-op whenever the pool
    has no open position -- including whenever the mode is disabled or was
    never manually started."""
    pool_status = compound_scalper.status()
    pos = pool_status.get("open_position")
    if not pos:
        return {"managed": False}

    chain, token = pos.get("chain"), pos.get("token")
    if not chain or not token:
        return {"managed": False}

    snap = _safe(fetch_dexscreener_snapshot, chain, token)
    if not (isinstance(snap, dict) and snap.get("mcap_usd") is not None):
        return {"managed": False, "reason": "could not re-price open scalp position this cycle"}

    result = compound_scalper.check_and_manage(snap["mcap_usd"])
    if result and result.get("sell_attempted"):
        d = result["exit_decision"]
        print(f"[compound-scalper:{chain}] {d.exit_type.upper()} {token[:8]}: {d.reason}")
    return {"managed": True, "result": result}


def _run_post_alert_monitor_cycle() -> int:
    """Sweeps state.get_post_alert_monitor() -- tokens a real alert was
    just sent for -- and, for any entry that's now at least 15 minutes old
    (state.POST_ALERT_MONITOR_MIN_AGE_SECONDS), checks whether the price
    has since collapsed 60%+ (POST_ALERT_CRATER_DRAWDOWN_PCT) since the
    alert. Birdeye-supported chains use real historical OHLCV covering the
    alert-to-now window; Robinhood Chain (no Birdeye mapping) instead
    compares the DexScreener price snapshot taken at alert time
    (state.post_alert_monitor_add's price_at_alert) against a fresh
    snapshot taken now. This is a SINGLE one-time pass per alert, not a
    repeating watch (per Ali's own framing, "15-60 min after a coin is
    flagged") -- every entry checked this cycle is removed from the queue
    regardless of outcome, since the verdict this pass exists to give has
    now been delivered one way or the other. An entry that ages past 60 min
    without ever being checked (the per-cycle cap was full every cycle in
    that window) just ages out on state.get_post_alert_monitor's own
    self-cleaning read -- an honest, disclosed gap, same convention as the
    soft-fail watch list's own max-age prune, not a silent failure. Returns
    the number of real DOWNGRADE follow-ups sent."""
    queue = state.get_post_alert_monitor()
    if not queue:
        return 0
    now = time.time()
    eligible = [q for q in queue if q.get("chain") in POST_ALERT_MONITOR_SUPPORTED_CHAINS
                and now - q.get("alert_ts", now) >= state.POST_ALERT_MONITOR_MIN_AGE_SECONDS]
    # Oldest-flagged-first, same round-robin intent as the soft-fail watch
    # cycle -- a busy night shouldn't let the newest handful of alerts hog
    # every cycle's cap while older ones silently age past the 60-min
    # window without ever being checked.
    eligible.sort(key=lambda q: q.get("alert_ts", 0))
    to_check = eligible[:POST_ALERT_MONITOR_MAX_PER_CYCLE]
    downgraded = 0
    for q in to_check:
        token, chain, alert_ts = q["token"], q["chain"], q.get("alert_ts", now)
        # One-time pass -- remove now, checked either way, before deciding
        # the outcome, so a crash in the summarize/compare step below can't
        # leave the same entry stuck being re-checked forever.
        state.post_alert_monitor_remove(token)

        if chain in DEXSCREENER_SNAPSHOT_CHAINS:
            price_then = q.get("price_at_alert")
            if not isinstance(price_then, (int, float)) or price_then <= 0:
                print(f"[post-alert-monitor] {token[:8]} follow-up check skipped: "
                      f"no valid price_at_alert snapshot recorded")
                continue
            price_now = _safe(fetch_dexscreener_token_price_usd, chain, token)
            if not isinstance(price_now, (int, float)):
                print(f"[post-alert-monitor] {token[:8]} follow-up check skipped: "
                      f"couldn't fetch a current DexScreener price")
                continue
            dd = round((price_now - price_then) / price_then * 100, 2)
        else:
            ohlcv = _safe(fetch_birdeye_ohlcv, chain, token, int(alert_ts), int(now), interval="15m")
            if not (isinstance(ohlcv, dict) and ohlcv.get("ok")):
                reason = ohlcv.get("reason") if isinstance(ohlcv, dict) else str(ohlcv)
                print(f"[post-alert-monitor] {token[:8]} follow-up check skipped: {reason}")
                continue
            summary = summarize_launch_window(ohlcv.get("candles") or [])
            if not summary.get("ok"):
                print(f"[post-alert-monitor] {token[:8]} follow-up check skipped: {summary.get('reason')}")
                continue
            dd = summary.get("drawdown_from_peak_pct")

        if dd is not None and dd <= POST_ALERT_CRATER_DRAWDOWN_PCT:
            _send_post_alert_downgrade(token, chain, q, dd, now)
            downgraded += 1
        else:
            print(f"[post-alert-monitor] {token[:8]} held up (drawdown {dd}% from peak) -- no downgrade")
    if to_check:
        print(f"[post-alert-monitor] checked {len(to_check)}/{len(queue)} pending follow-up(s), "
              f"{downgraded} downgraded")
    return downgraded


def poll_layer12_caller_channels() -> dict:
    """One getUpdates call against the caller bot (free, keyless beyond the
    bot token itself -- no MadeOnSol/Birdeye/DexScreener budget touched),
    parses every configured channel's posts for candidate token addresses,
    and records each one via state.record_caller_signal for
    scheduler._handle_scored to ride as a "Caller" tag later. No-ops
    cleanly (never raises) if CONFIG.layer12_ready() is False -- see
    layers/layer12_caller_channels.py's docstring for why the channel list
    starts empty and stays that way until Ali supplies real ones."""
    if not CONFIG.layer12_ready():
        return {"ok": False, "reason": "Layer 12 not configured (TELEGRAM_CALLER_BOT_TOKEN / "
                                        "TELEGRAM_CALLER_CHANNEL_IDS)"}
    offset = state.get_caller_update_offset()
    result = _safe(fetch_caller_channel_posts, offset=offset)
    if not (isinstance(result, dict) and result.get("ok")):
        reason = result.get("reason") if isinstance(result, dict) else str(result)
        print(f"[layer12] caller-channel fetch failed this cycle: {reason}")
        return {"ok": False, "reason": reason}
    posts = result.get("posts") or []
    recorded = 0
    for post in posts:
        addresses = extract_token_addresses(post.text)
        for addr in addresses["solana"] + addresses["evm"]:
            state.record_caller_signal(addr, post.channel_name, ts=post.date)
            recorded += 1
    next_offset = result.get("next_offset")
    if next_offset is not None:
        state.set_caller_update_offset(next_offset)
    if posts:
        print(f"[layer12] {len(posts)} caller-channel post(s) this cycle, {recorded} address mention(s) recorded")
    return {"ok": True, "posts_checked": len(posts), "addresses_recorded": recorded}


def poll_layer13_fomo_copytrade() -> dict:
    """Fomo copy-trading + thesis detection (Ali, Sept 30 2026 -- see
    layers/layer13_fomo_copytrade.py's module docstring for the full
    design). No-ops cleanly if CONFIG.fomoapi_ready() is False. Two parts
    per cycle:
      1. Pull recent /v2/alerts since the last cursor, match against the
         roster, score every hit's coin, record to the dashboard feed.
      2. Every FOMO_CANDIDATE_CHECK_EVERY_N_CYCLES-th call, also pull the
         24h leaderboard and check for new >=$5k-balance candidates NOT on
         the roster -- gated to not every cycle since it spends
         fomoapi.io balance-check credits per candidate (see
         find_new_trader_candidates's docstring) and Ali only needs to see
         new candidates periodically, not every 15 minutes."""
    if not CONFIG.fomoapi_ready():
        return {"ok": False, "reason": "Layer 13 not configured (FOMOAPI_API_KEY)"}

    since = state.get_fomo_alerts_since()
    alerts_result = _safe(fetch_fomo_alerts, since_iso=since, limit=100)
    if not (isinstance(alerts_result, dict) and alerts_result.get("ok")):
        reason = alerts_result.get("reason") if isinstance(alerts_result, dict) else str(alerts_result)
        print(f"[layer13] fomoapi.io /v2/alerts fetch failed this cycle: {reason}")
        return {"ok": False, "reason": reason}
    alerts = alerts_result.get("alerts") or []
    detected = detect_roster_buys_and_theses(alerts)
    if alerts:
        newest_ts = alerts[0].get("ts")
        if isinstance(newest_ts, (int, float)):
            import datetime
            iso = datetime.datetime.fromtimestamp(newest_ts / 1000, tz=datetime.timezone.utc).isoformat()
            state.set_fomo_alerts_since(iso)
    if detected["buys_recorded"] or detected["theses_recorded"]:
        print(f"[layer13] {detected['buys_recorded']} roster buy(s), "
              f"{detected['theses_recorded']} thesis/theses recorded this cycle")

    candidates_result = {"checked": 0, "candidates_found": 0}
    lb = _safe(fetch_leaderboard, window="24h", limit=100)
    if isinstance(lb, dict) and lb.get("ok"):
        candidates_result = find_new_trader_candidates(lb.get("traders") or [], min_balance_usd=5000.0)
        if candidates_result["candidates_found"]:
            print(f"[layer13] {candidates_result['candidates_found']} new-trader candidate(s) "
                  f">= $5k balance found this cycle (checked {candidates_result['checked']})")
    else:
        reason = lb.get("reason") if isinstance(lb, dict) else str(lb)
        print(f"[layer13] leaderboard fetch failed this cycle (candidate discovery skipped): {reason}")

    return {"ok": True, **detected, **{f"candidate_{k}": v for k, v in candidates_result.items()}}


LAYER2B_MAX_SIGNATURES_PER_CYCLE = 20  # honest call-budget cap -- see poll_layer2b docstring


def poll_layer2b_pumpfun_smart_money() -> dict:
    """One getSignaturesForAddress call against pump.fun's program, then up
    to LAYER2B_MAX_SIGNATURES_PER_CYCLE getTransaction calls to decode them
    -- real per-transaction network cost, unlike this scheduler's other
    keyless layers. Capped deliberately: the free Solana RPC pool has no
    real SLA (see executor/rpc_pool.py), and this is additive load on top
    of everything else already using it. Dedups via a stored cursor
    (state.get_value("pumpfun_last_signature")) so a cycle only processes
    signatures newer than the last one seen, not the same 20 every time.
    Every decoded buy feeds both the wallet-PnL tracker (layer2b's
    promotion logic) and, if the smart-money roster isn't empty yet, this
    cycle's convergence check."""
    cursor = state.get_value("pumpfun_last_signature")
    sigs_result = _safe(fetch_recent_signatures, before=None, limit=LAYER2B_MAX_SIGNATURES_PER_CYCLE)
    if not (isinstance(sigs_result, dict) and sigs_result.get("ok")):
        return {"ok": False, "reason": sigs_result.get("reason") if isinstance(sigs_result, dict) else str(sigs_result)}

    signatures = sigs_result["signatures"]
    if cursor and cursor in signatures:
        signatures = signatures[:signatures.index(cursor)]  # only newer than last-seen
    if signatures:
        state.set_value("pumpfun_last_signature", signatures[0])

    decoded_buys = []
    promotions = []
    decode_failures = 0
    for sig in signatures:
        tx_result = _safe(fetch_transaction, sig)
        if not (isinstance(tx_result, dict) and tx_result.get("ok")):
            decode_failures += 1
            continue
        trade = decode_trade(tx_result.get("tx"))
        if not trade:
            decode_failures += 1  # not necessarily an error -- could be an unrecognized variant, see pumpfun_trades.py docstring
            continue
        promoted = pumpfun_process_trade(trade["wallet"], trade["mint"], trade["direction"],
                                          trade["sol_delta"], trade.get("block_time"))
        if promoted:
            promotions.append(trade["wallet"])
        if trade["direction"] == "buy":
            decoded_buys.append(trade)

    convergence_events = detect_pumpfun_convergence(decoded_buys) if decoded_buys else []
    single_events = single_wallet_buy_events(decoded_buys) if decoded_buys else []
    return {"ok": True, "checked": len(signatures), "decoded": len(decoded_buys),
            "decode_failures": decode_failures, "promotions": promotions,
            "convergence_events": convergence_events, "single_wallet_events": single_events,
            "roster_size": len(get_smart_money_roster())}


def _run_layer1_cycle(_summary_path=None):
    """The exact Layer 1 logic that used to be inline in run_poll_fast, now
    shared between there (currently a no-op on GitHub Actions) and
    run_poll_madeonsol() (the local-PC entrypoint) -- extracted so both call
    the SAME code instead of two copies that could quietly drift apart.
    Returns (alerts_sent, madeonsol_calls)."""
    alerts_sent = 0
    madeonsol_calls = 0
    if not CONFIG.layer1_ready():
        print("[layer1] BLOCKED: MADEONSOL_API_KEY not set")
        if _stats():
            _stats().note_module("layer1 deployer", False, "MADEONSOL_API_KEY not set")
        return alerts_sent, madeonsol_calls

    chain = chain_for_cycle(time.time())
    since = state.get_layer1_last_checked(chain)
    result = _safe(poll_layer1, chain, since)
    madeonsol_calls += 1
    if not result["ok"]:
        print(f"[layer1:{chain}] skipped: {result.get('reason')}")
        if _summary_path:
            with open(_summary_path, "a") as _f:
                _f.write(f"- [layer1:{chain}] FAILED: {result.get('reason')}\n")
        if _stats():
            _stats().note_module(f"layer1 deployer ({chain})", False, result.get("reason"))
    else:
        state.set_layer1_last_checked(chain, datetime.now(timezone.utc).isoformat())
        if _stats():
            _stats().note_module(f"layer1 deployer ({chain})", True)
        for a in result["alerts"]:
            alert = Alert(a["token_address"][:8], a["token_address"], chain,
                           "Elite/good-tier deployer just launched a token")
            alert.set_tag("Chain", chain).set_tag("Deployer", a["deployer_tier"])
            send_res = _alert(alert, "layer1")
            print(f"[layer1:{chain}] alert -> {send_res}")
            if send_res.get("sent"):
                alerts_sent += 1
            if _stats():
                _stats().note_deployer(f"{a['token_address'][:8]} [{chain}] {a['deployer_tier']} tier")
            if a["deployer_tier"] == "elite" and a["token_address"]:
                state.queue_rescan(a["token_address"], chain, is_pregraduation=(chain == "solana"))
    print(f"[layer1] checked {chain} this cycle")
    if _summary_path:
        with open(_summary_path, "a") as _f:
            _f.write(f"- [layer1:{chain}] result: {result.get('ok')}, "
                     f"{len(result.get('alerts', []))} alert(s)\n")
    return alerts_sent, madeonsol_calls


def _run_geckoterminal_fallback(chain: str, board) -> int:
    """Fallback discovery source for BSC/Base Layer 0b, only reached when
    Mobula's own fetch failed or MOBULA_API_KEY isn't set (Ali, Sept 28
    2026 -- Mobula's free plan is confirmed permanently dead, HTTP 403 on
    /api/2/pulse, not a transient outage). GeckoTerminal's own /new_pools
    call costs zero MadeOnSol/Mobula budget; the per-token GoPlus security
    enrichment inside score_geckoterminal_pools is capped (see
    layer0_scoring.GECKOTERMINAL_MAX_GOPLUS_PER_CYCLE) so this can't quietly
    balloon into an unbounded number of calls on a busy new-pools page.
    Returns the number of alerts actually delivered, same convention as
    every other _run_* helper in this file."""
    alerts_sent = 0
    gt_network = chain  # GeckoTerminal's own slug already matches this codebase's chain name for bsc/base
    raw = _safe(fetch_geckoterminal_new_pools, gt_network)
    if not (isinstance(raw, dict) and raw.get("ok")):
        detail = raw.get("reason") if isinstance(raw, dict) else describe_fetch_failure({"raw": raw})
        print(f"[layer0b/8:{chain}] GeckoTerminal fallback fetch failed: {detail}")
        if _stats():
            _stats().note_module(f"layer0b pulse ({chain}) [GeckoTerminal fallback]", False, detail)
        return alerts_sent
    if _stats():
        _stats().note_module(f"layer0b pulse ({chain}) [GeckoTerminal fallback]", True)
    items = flatten_geckoterminal_pools(raw.get("json"))
    for scored in score_geckoterminal_pools(chain, items):
        mint = scored["address"]
        mc = scored["raw"].get("market_cap_usd") or scored["raw"].get("fdv_usd")
        if mint and mc is not None:
            state.record_mc_point(mint, mc)
        if _handle_scored(scored, chain, source="geckoterminal", mc=mc, board=board):
            alerts_sent += 1
    print(f"[layer0b/8:{chain}] GeckoTerminal fallback: {len(items)} new pool(s) fetched, "
          f"{min(len(items), GECKOTERMINAL_MAX_GOPLUS_PER_CYCLE)} scored via GoPlus enrichment")
    return alerts_sent


def run_poll_fast_loop():
    """Wraps run_poll_fast() in a real wall-clock loop so discovery actually
    runs on a real cadence, instead of relying on GitHub's `schedule:`
    trigger to re-invoke this process -- confirmed live (Sept 27 2026) that
    GitHub only actually fires poll-fast.yml's declared */10 cron every
    2.5-5.5 hours in practice (a platform-side scheduling throttle, not a
    bug here). One job now keeps polling on its own for up to ~5h45m
    (comfortably under GitHub's 6h per-job ceiling on this repo), which in
    practice overlaps or nearly touches the next real schedule fire, giving
    close to continuous coverage without needing GitHub to cooperate.

    Guarded by an Upstash lock (state.acquire_lock) so that if GitHub's
    schedule DOES fire a second time while a loop from an earlier run is
    still going, the second process exits immediately instead of running a
    duplicate loop and silently doubling the MadeOnSol call rate.

    CADENCE (Ali's call, Sept 27 2026): deliberately set to 20 min, not the
    originally-declared 10 min in the cron comment. Layer 1's 2 MadeOnSol
    calls/cycle at a genuine 10-min cadence is ~288 calls/day, over the
    200/day BASIC cap; at 20 min it's ~144/day, comfortably under it with
    real headroom left for diagnostics/backtests run the same day.
    madeonsol_budget_remaining() still gates calls downstream regardless
    (see layer1_deployer.py) -- this cadence choice is about staying under
    the cap on a normal day, not a hard dependency on that gate."""
    INTERVAL_SECONDS = 20 * 60  # 20 min -- see CADENCE note above
    LOOP_CEILING_SECONDS = 5 * 3600 + 45 * 60  # 5h45m
    LOCK_KEY = "poll_fast_loop_lock"
    LOCK_TTL_SECONDS = INTERVAL_SECONDS + 5 * 60  # covers one full interval + a slow cycle, so the lock never expires between refreshes

    if not state.acquire_lock(LOCK_KEY, ttl_seconds=LOCK_TTL_SECONDS):
        print("poll-fast loop lock already held by another run -- exiting immediately, no duplicate loop.")
        return

    start = time.time()
    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"\n=== poll-fast loop: cycle {cycle}, elapsed {int(time.time() - start)}s ===")
            try:
                run_poll_fast()
            except Exception as e:
                # A single bad cycle must never kill the whole loop -- log and keep going.
                print(f"poll-fast loop: cycle {cycle} raised {e!r} -- continuing")

            if not state.refresh_lock(LOCK_KEY, ttl_seconds=LOCK_TTL_SECONDS):
                print("poll-fast loop: lost the lock refresh -- exiting rather than assuming we still hold it.")
                break

            elapsed = time.time() - start
            if elapsed >= LOOP_CEILING_SECONDS:
                print(f"poll-fast loop: ceiling reached ({int(elapsed)}s) -- exiting cleanly for the next scheduled run to take over.")
                break

            time.sleep(INTERVAL_SECONDS)
    finally:
        state.release_lock(LOCK_KEY)


def run_poll_fast():
    """Discovery: Layer 1 (deployer alerts) + Layer 0b (Mobula Pulse
    scoring) + Layer 4 (news) + Layer 6 (exit-risk snapshot). No per-token
    MadeOnSol scoring here -- meant to run every 10 min, unchanged, because
    speed on brand-new token discovery is the single most valuable thing in
    this system. See module docstring."""
    # Pump.fun manual wallet seeding (Ali, Sept 24 2026), run from HERE
    # rather than its own workflow file or an edit to an existing one:
    # GitHub blocks Ali's saved token from pushing ANY change to a workflow
    # YAML (create OR modify) without the `workflow` scope -- confirmed live
    # Sept 24, twice. A plain Python change has no such restriction. Runs
    # every poll-fast cycle now (fixed Sept 28 2026 -- see
    # run_seed_pumpfun_wallets' docstring): it tracks completion per BATCH
    # KEY internally, so this costs one cheap Upstash read/cycle (not a
    # MadeOnSol call, doesn't touch the 200/day budget) and only does real
    # work the first cycle after Ali hands over a genuinely new batch.
    run_seed_pumpfun_wallets()
    _start_cycle_stats("FAST cycle")

    report = readiness_report()
    print("Readiness:", report)
    alerts_sent = 0
    madeonsol_calls = 0

    # Diagnostic (Ali, Sept 24 2026): dump readiness straight into the GitHub Actions
    # run's Summary tab, which renders without needing 'admin rights' the way the raw
    # job log API/UI does. No workflow YAML touched -- GITHUB_STEP_SUMMARY is already
    # set by the runner for every step regardless of what the .yml says.
    _summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if _summary_path:
        with open(_summary_path, "a") as _f:
            _f.write("### Fast-cycle readiness\n```\n" + str(report) + "\n```\n")

    # --- Layer 11: fetch the DexScreener boost board once for this whole
    # cycle (2 keyless calls total) -- matched in-memory per token below,
    # see layer11_social_buzz.py. ---
    board = _safe(fetch_boost_board)
    if not (isinstance(board, dict) and board.get("ok")):
        print(f"[layer11] boost board fetch failed this cycle: {board}")
        board = None

    # --- Layer 12: Telegram caller-channel monitoring (Ali, Sept 28 2026)
    # -- free, keyless beyond the bot token, no-ops cleanly if not
    # configured. See poll_layer12_caller_channels' docstring. ---
    _safe(poll_layer12_caller_channels)

    # --- Layer 1: deployer alerts. MOVED off GitHub Actions (Ali, Sept 24
    # 2026): confirmed live that MadeOnSol's free key rate-limits on IP
    # diversity ("Too many IP addresses for one free key... 8 IP addresses
    # today"), and GitHub Actions runners get a different IP nearly every
    # run -- structurally incompatible with a free key, not a code bug.
    # Runs instead from run_poll_madeonsol(), meant to be scheduled on
    # Ali's own PC (stable home IP) via Windows Task Scheduler -- see
    # README. This block is now a no-op on GitHub Actions specifically so
    # the two never double-alert on the same deployer launch. ---
    if IS_GITHUB_ACTIONS:
        print("[layer1] SKIPPED on GitHub Actions -- MadeOnSol free-key IP-rate-limit "
              "(see run_poll_madeonsol / README). Run 'python scheduler.py --poll-madeonsol' "
              "on your own PC instead.")
        if _summary_path:
            with open(_summary_path, "a") as _f:
                _f.write("- [layer1] SKIPPED on GitHub Actions -- moved to --poll-madeonsol "
                         "(MadeOnSol free-key rate limit)\n")
    else:
        a1, c1 = _run_layer1_cycle(_summary_path)
        alerts_sent += a1
        madeonsol_calls += c1

    # --- Layer 0c: StonkFun discovery. Keyless, so always attempted --
    # no CONFIG gate needed (see layers/layer0c_stonkfun_scoring.py). Only
    # bands A/B alert; C/D are suppressed as noise given StonkFun's high
    # launch volume (100k+ tokens on the platform) and this layer's thinner,
    # deliberately-looser-banded signal set (see that module's docstring --
    # its bands are NOT equivalent in rigor to Layer 0/0b's). Dedup is a
    # capped seen-mint set (state.py) since this endpoint has no documented
    # 'since' cursor the way Layer 1's does. ---
    stonkfun_result = _safe(poll_layer0c)
    if isinstance(stonkfun_result, dict) and stonkfun_result.get("ok"):
        if _stats():
            _stats().note_module("layer0c stonkfun discovery", True)
        seen = state.layer0c_seen_mints()
        newly_alerted = []
        for tok in stonkfun_result["tokens"]:
            mint = tok.get("mint")
            if not mint or mint in seen:
                continue
            if tok["band"] not in ("A", "B"):
                continue
            alert = Alert(tok.get("symbol") or mint[:8], mint, "solana", "Layer 0c StonkFun discovery")
            alert.set_tag("Chain", "solana (StonkFun)").set_tag("Score", f"{tok['score']}/100 (band {tok['band']})")
            send_res = _alert(alert, "layer0c")
            print(f"[layer0c] {mint} band {tok['band']} -> {send_res}")
            if send_res.get("sent"):
                alerts_sent += 1
            if _stats():
                _stats().note_band("solana (StonkFun)", tok.get("symbol") or mint[:8], tok["band"], tok["score"])
            newly_alerted.append(mint)
        state.mark_layer0c_seen(newly_alerted)
        print(f"[layer0c] {len(stonkfun_result['tokens'])} SOL-quoted launch(es) fetched, "
              f"{len(newly_alerted)} new A/B-band alert(s)")
    else:
        reason = stonkfun_result.get("reason") if isinstance(stonkfun_result, dict) else str(stonkfun_result)
        print(f"[layer0c] fetch failed: {reason}")
        if _stats():
            _stats().note_module("layer0c stonkfun discovery", False, reason)

    # --- Layer 0c momentum: cross-quote-type gem scan (Sept 23, 2026). ---
    # Independent of the SOL-only filter above -- catches a LEVERCAT-shaped
    # mover (xSOL-quoted, ~2744x launch-to-peak) that the discovery block
    # above would otherwise drop entirely. Alert-only: tags executable=False
    # for any quote type swap_executor can't route yet, rather than staying
    # silent about it. Capped deep-lookups/cycle (MOMENTUM_MAX_DEEP_LOOKUPS_
    # PER_CYCLE in the layer module) -- see readiness_report's note above for
    # the honest timing caveat (10-min cycles can miss a move that completes
    # inside one interval). ---
    mom_checked = state.layer0c_momentum_checked_mints()
    mom_result = _safe(poll_layer0c_momentum, 25, 15, mom_checked)
    if isinstance(mom_result, dict) and mom_result.get("ok"):
        if _stats():
            _stats().note_module("layer0c momentum", True)
        for gem in mom_result["gems"]:
            mint = gem.get("mint")
            exec_tag = "EXECUTABLE" if gem.get("executable") else "ALERT-ONLY (no buy route for this quote token)"
            alert = Alert(gem.get("symbol") or (mint or "?")[:8], mint, "solana", "Layer 0c MOMENTUM GEM (cross-quote)")
            alert.set_tag("Chain", "solana (StonkFun)").set_tag("Quote", gem.get("quote_symbol") or "?")
            alert.set_tag("Multiple", f"{gem['peak_multiple']:.0f}x peak vs launch mcap").set_tag("Route", exec_tag)
            send_res = _alert(alert, "layer0c_momentum")
            print(f"[layer0c-momentum] {mint} {gem['peak_multiple']:.0f}x ({exec_tag}) -> {send_res}")
            if send_res.get("sent"):
                alerts_sent += 1
            if _stats():
                _stats().moonshots.append(
                    f"{gem.get('symbol') or (mint or '?')[:8]} [solana (StonkFun)] "
                    f"{gem['peak_multiple']:.0f}x momentum ({exec_tag})")
        state.mark_layer0c_momentum_checked(mom_result["checked"])
        print(f"[layer0c-momentum] {len(mom_result['checked'])} recent launch(es) deep-checked, "
              f"{len(mom_result['gems'])} momentum gem(s) found")
    else:
        reason = mom_result.get("reason") if isinstance(mom_result, dict) else str(mom_result)
        print(f"[layer0c-momentum] fetch failed: {reason}")
        if _stats():
            _stats().note_module("layer0c momentum", False, reason)

    # --- Layer 4: news/exchange (no MadeOnSol cost either way) ---
    #
    # CryptoPanic (Ali, Sept 24 2026): this was fetching and parsing posts every
    # cycle but never turning any of them into an alert -- the exact reason
    # nothing was showing up for "news or utility based memecoin launches". Fixed:
    # only posts that actually name a currency alert (pure macro/opinion posts with
    # no `currencies` tag are skipped -- they're not actionable per-token signal),
    # deduped the same way Binance's feed is below.
    # CryptoPanic's free plan was discontinued (Sept 28 2026, see
    # layers/layer4_news.py's fetch_cryptopanic_posts docstring) -- replaced
    # with CoinDesk's free, keyless RSS feed. The old CryptoPanic call/print
    # is left in place, still gated behind the readiness flag, so it starts
    # working again on its own the moment Ali upgrades to a paid CryptoPanic
    # plan and updates CONFIG.cryptopanic_base_url -- no code change needed
    # then, just delete/replace this CoinDesk block.
    if report["stage1"]["layer4_news_exchange"]["cryptopanic"]:
        cp = _safe(fetch_cryptopanic_posts, "rising")
        if cp["ok"]:
            if _stats():
                _stats().note_module("layer4 cryptopanic", True)
            posts = parse_cryptopanic_posts(cp["raw"].get("json") or {})
            seen = state.cryptopanic_seen_posts()
            actionable = [p for p in posts if p.get("currencies") and p.get("id") not in seen]
            print(f"[layer4:cryptopanic] {len(posts)} rising post(s) fetched, "
                  f"{len(actionable)} new coin-tagged post(s)")
            just_alerted = []
            for post in actionable[:5]:
                coins = ", ".join(post["currencies"])
                alert = Alert(post["currencies"][0], "n/a", "n/a", post.get("title") or "CryptoPanic rising post")
                alert.set_tag("News", f"CryptoPanic rising ({coins})")
                send_res = send_alert(alert)
                state.log_full_alert("layer4_news", alert.chain, alert.token_symbol, alert.token_address,
                                      alert.headline, dict(alert.tags))
                print(f"[layer4:cryptopanic] {coins} -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                if _stats():
                    _stats().note_news(f"CryptoPanic: {coins}")
                    _stats().note_alert_result(bool(send_res.get("sent")))
                just_alerted.append(post.get("id"))
            state.mark_cryptopanic_seen(just_alerted)
        else:
            print(f"[layer4:cryptopanic] fetch failed (expected -- see docstring): {describe_fetch_failure(cp)}")
            if _stats():
                _stats().note_module("layer4 cryptopanic", False, describe_fetch_failure(cp))
    else:
        print("[layer4:cryptopanic] BLOCKED: CRYPTOPANIC_AUTH_TOKEN not set (see README -- separate free signup)")
        if _stats():
            _stats().note_module("layer4 cryptopanic", False, "CRYPTOPANIC_AUTH_TOKEN not set")

    # CoinDesk RSS -- keyless, so it runs regardless of whether
    # CRYPTOPANIC_AUTH_TOKEN is configured (unlike the CryptoPanic block
    # above, which is gated on that token).
    cd = _safe(fetch_coindesk_rss)
    if cd["ok"]:
        if _stats():
            _stats().note_module("layer4 coindesk", True)
        posts = parse_coindesk_rss(cd["raw"].get("text") or "")  # fetch_coindesk_rss returns raw text directly now, no "json" key
        seen = state.coindesk_seen_posts()
        actionable = [p for p in posts if p.get("currencies") and p.get("id") not in seen]
        print(f"[layer4:coindesk] {len(posts)} post(s) fetched, "
              f"{len(actionable)} new coin-tagged post(s)")
        just_alerted = []
        for post in actionable[:5]:
            coins = ", ".join(post["currencies"])
            alert = Alert(post["currencies"][0], "n/a", "n/a", post.get("title") or "CoinDesk post")
            alert.set_tag("News", f"CoinDesk ({coins})")
            send_res = send_alert(alert)
            state.log_full_alert("layer4_news", alert.chain, alert.token_symbol, alert.token_address,
                                  alert.headline, dict(alert.tags))
            print(f"[layer4:coindesk] {coins} -> {send_res}")
            if send_res.get("sent"):
                alerts_sent += 1
            if _stats():
                _stats().note_news(f"CoinDesk: {coins}")
                _stats().note_alert_result(bool(send_res.get("sent")))
            just_alerted.append(post.get("id"))
        state.mark_coindesk_seen(just_alerted)
    else:
        print(f"[layer4:coindesk] fetch failed: {describe_fetch_failure(cd)}")
        if _stats():
            _stats().note_module("layer4 coindesk", False, describe_fetch_failure(cd))

    # Binance new-listing feed REMOVED (Ali, Sept 24 2026: "do we really need it
    # for memecoins"). Binance only lists coins after its own formal review, which
    # in practice means established/vetted projects, essentially never a brand-new
    # pump.fun/Fomo/StonkFun-style memecoin -- it was flooding Telegram with the
    # only alerts actually arriving while the real memecoin layers stayed quiet,
    # i.e. pure noise for this system's actual purpose. Code left in place
    # (layers/layer4_news.py's fetch_binance_new_listings/parse_binance_new_listings,
    # state.py's binance_seen_listings/mark_binance_seen) in case it's ever wanted
    # back -- just not called from here anymore.

    # --- Layer 0b: Mobula Pulse, one call per chain, scores every item in
    # that response -- free (no MadeOnSol cost), so this runs every fast
    # cycle rather than waiting for the slow one. ---
    for chain, chain_id in MOBULA_PULSE_CHAINS:
        mobula_ok = False
        if CONFIG.mobula_api_key:
            raw = _safe(fetch_mobula_pulse, chain_id)  # ONE call per chain, covers every token in it
            if raw.get("ok"):
                mobula_ok = True
                if _stats():
                    _stats().note_module(f"layer0b pulse ({chain}) [Mobula]", True)
                items = flatten_mobula_pulse_response(raw.get("json"))
                for scored in score_mobula_pulse_items(chain, items):
                    mint = scored["address"]
                    mc = scored["raw"].get("marketCap") or scored["raw"].get("market_cap")
                    if mint and mc is not None:
                        state.record_mc_point(mint, mc)
                    if _handle_scored(scored, chain, source="mobula", mc=mc, board=board):
                        alerts_sent += 1
                print(f"[layer0b/8:{chain}] scored {len(items)} Pulse item(s) (1 Mobula call)")
            else:
                detail = raw["reason"] if "reason" in raw else describe_fetch_failure({"raw": raw})
                print(f"[layer0b/8:{chain}] Mobula pulse fetch failed: {detail}")
                if _stats():
                    _stats().note_module(f"layer0b pulse ({chain}) [Mobula]", False, detail)
        else:
            print(f"[layer0b/8:{chain}] Mobula BLOCKED: MOBULA_API_KEY not set")
            if _stats():
                _stats().note_module(f"layer0b pulse ({chain}) [Mobula]", False, "MOBULA_API_KEY not set")

        # GeckoTerminal fallback (Ali, Sept 28 2026) -- only reached when
        # Mobula didn't come through this cycle, either because the key's
        # missing or (the real, current situation) its free plan 403s. See
        # _run_geckoterminal_fallback's docstring.
        if not mobula_ok:
            alerts_sent += _run_geckoterminal_fallback(chain, board)

    # --- Layer 2b: self-built pump.fun smart-money convergence (Ali, Sept
    # 23 2026). Keyless (free Solana RPC only), so always attempted, no
    # CONFIG gate -- see poll_layer2b_pumpfun_smart_money's docstring for
    # the real per-cycle call cost this incurs. ---
    l2b_result = _safe(poll_layer2b_pumpfun_smart_money)
    if isinstance(l2b_result, dict) and l2b_result.get("ok"):
        if _stats():
            _stats().note_module("layer2b pump.fun smart-money", True)
        # Single-wallet alerts (Ali, Sept 24 2026 -- see single_wallet_buy_events'
        # docstring): fires on ANY tracked wallet's buy, not just 2+ converging.
        # Sent BEFORE the convergence check below so if both fire for the same
        # token this cycle, you see the individual signal first, escalation
        # second -- matches the actual order of what happened on-chain.
        for event in l2b_result.get("single_wallet_events", []):
            token = event["mint"]
            wallet = event["wallet"]
            alert = Alert(token[:8], token, "solana", "Pump.fun tracked trader buy")
            alert.set_tag("Chain", "solana (pump.fun)")
            alert.set_tag("Trader", f"{wallet[:8]}...{wallet[-4:]} ({event['source']})")
            if event.get("note"):
                alert.set_tag("Note", event["note"])
            send_res = _alert(alert, "layer2b_single")
            print(f"[layer2b] {token[:8]} single tracked-trader buy by {wallet[:8]}... -> {send_res}")
            if send_res.get("sent"):
                alerts_sent += 1
            if _stats():
                _stats().note_copytrade(f"pump.fun: {wallet[:8]}... bought {token[:8]}")
        for event in l2b_result["convergence_events"]:
            token = event["token"]
            alert = Alert(token[:8], token, "solana", "Pump.fun SMART-MONEY convergence")
            alert.set_tag("Chain", "solana (pump.fun)")
            alert.set_tag("Convergence", f"{event['count']} self-tracked smart-money wallet(s) within "
                                          f"{int(CONVERGENCE_WINDOW.total_seconds() / 60)}min")
            send_res = _alert(alert, "layer2b")
            print(f"[layer2b] {token[:8]} smart-money convergence -> {send_res}")
            if send_res.get("sent"):
                alerts_sent += 1
            if _stats():
                _stats().note_copytrade(f"pump.fun CONVERGENCE: {event['count']} wallet(s) on {token[:8]}")
                _stats().moonshots.append(f"{token[:8]} [solana (pump.fun)] {event['count']}-wallet smart-money convergence")
        print(f"[layer2b] checked {l2b_result['checked']} pump.fun signature(s), "
              f"{l2b_result['decoded']} decoded buy(s), {len(l2b_result['promotions'])} new smart-money "
              f"promotion(s), roster size now {l2b_result['roster_size']}")
    else:
        reason = l2b_result.get("reason") if isinstance(l2b_result, dict) else str(l2b_result)
        print(f"[layer2b] fetch failed: {reason}")
        if _stats():
            _stats().note_module("layer2b pump.fun smart-money", False, reason)

    # --- Layer 6: exit-risk / realizable-gain snapshot (Mobula only, no
    # MadeOnSol cost) -- kept fast for quicker rug detection. ---
    if report["stage2"]["layer6_exit_realizable"]:
        portfolio = _safe(fetch_wallet_portfolio)
        if portfolio["ok"]:
            held = (portfolio["raw"].get("json") or {}).get("data", {}).get("assets", [])
            print(f"[layer6] {len(held)} held assets fetched; exit-risk diffing needs a prior "
                  f"snapshot (state.py) -- first cycle establishes the baseline only")
            state.save_snapshot(held)
            if _stats():
                _stats().note_module("layer6 exit-risk", True)
        else:
            raw = portfolio.get("raw") if isinstance(portfolio, dict) else None
            if isinstance(raw, dict):
                detail = raw.get("json") or raw.get("text") or f"HTTP {raw.get('status_code')}"
            else:
                detail = portfolio.get("reason") if isinstance(portfolio, dict) else str(portfolio)
            print(f"[layer6] portfolio fetch failed: {detail}")
            if _stats():
                _stats().note_module("layer6 exit-risk", False, str(detail)[:120])
    else:
        print("[layer6] BLOCKED: MOBULA_API_KEY and/or WALLET_ADDRESSES not set")
        if _stats():
            _stats().note_module("layer6 exit-risk", False, "MOBULA_API_KEY / WALLET_ADDRESSES not set")

    # --- Soft-fail watch sweep (Ali, Sept 28 2026) -- free RPC only (0
    # MadeOnSol calls), so this runs every fast cycle same as Layer 2b/6
    # above, not gated behind the slow cycle's budget concerns. See
    # _run_soft_fail_watch_cycle's docstring for what this closes. ---
    _safe(_run_soft_fail_watch_cycle)

    # --- Post-alert monitoring pass (Ali, Sept 28 2026) -- Birdeye calls
    # cost no MadeOnSol budget (separate free-tier account, same as the
    # launch-window collapse override's own Birdeye call), so this runs
    # every fast cycle too, same reasoning as the soft-fail sweep above.
    # See _run_post_alert_monitor_cycle's docstring for what this closes. ---
    _safe(_run_post_alert_monitor_cycle)

    # --- Live position management (Ali, Sept 28 2026 -- "fix it live
    # let the test run") -- moonbag trims, campaign milestones, and
    # defensive rug-exits for every OPEN EXECUTOR POSITION, every fast
    # cycle. See _run_position_management_cycle's docstring for the real
    # gap this closes. ---
    _safe(_run_position_management_cycle)

    # --- Compound scalper pool management (Ali, Sept 29 2026) -- separate
    # from the position management pass above on purpose. See
    # _run_compound_scalper_cycle's docstring. ---
    _safe(_run_compound_scalper_cycle)

    print(f"\nFast cycle done. {alerts_sent} alert(s) delivered. ~{madeonsol_calls} MadeOnSol call(s) "
          f"used ({state.pending_rescan_count()} token(s) now queued for the next slow cycle's deep-score "
          f"pass). See README's call-budget section for how this compares to the 200/day cap.")

    if _summary_path:
        with open(_summary_path, "a") as _f:
            _f.write(f"\n### Fast-cycle result\n- alerts_sent: {alerts_sent}\n"
                     f"- madeonsol_calls: {madeonsol_calls}\n"
                     f"- layer2b roster_size: {l2b_result.get('roster_size') if isinstance(l2b_result, dict) else 'n/a'}\n")

    _safe(state.record_runner_heartbeat, "poll-fast", _runner_where(), f"alerts={alerts_sent}")
    if _stats():
        _print_cycle_summary(_stats())


def _run_layer8_cycle(board):
    # The exact Layer 8 deep-scoring logic that used to be inline in
    # run_poll_slow, extracted for the same reason as _run_layer1_cycle --
    # shared between there (a no-op on GitHub Actions now) and
    # run_poll_madeonsol() (Ali's own PC). Returns (alerts_sent, madeonsol_calls).
    alerts_sent = 0
    madeonsol_calls = 0
    # --- Layer 8: deep-score whatever's queued (initial elite-tier
    # discoveries from Layer 1, plus event-triggered re-scores), capped per
    # chain per cycle. Overflow beyond the cap is re-queued, not dropped. ---
    if CONFIG.layer1_ready():
        pending = state.pop_pending_rescans(limit=LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE * 2)
        by_chain = {}
        for p in pending:
            by_chain.setdefault(p["chain"], []).append(p)
        for chain, items in by_chain.items():
            to_process = items[:LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE]
            overflow = items[LAYER8_MAX_DEEP_SCORES_PER_SLOW_CYCLE:]
            for p in overflow:
                state.queue_rescan(p["token"], p["chain"], p["is_pregraduation"])
            for p in to_process:
                # Real daily-budget check added Sept 25 2026 -- see
                # state.py's madeonsol_budget_remaining() docstring. If the
                # day's real 200/day BASIC-tier quota is nearly spent,
                # re-queue this token for a later cycle instead of burning
                # a call attempt that fetch_madeonsol_token_risk would just
                # refuse anyway -- same re-queue mechanism already used for
                # per-cycle overflow above, so nothing here is lost, just
                # deferred to whenever budget frees up (next UTC day, or a
                # quieter cycle).
                if state.madeonsol_budget_remaining() < 3:
                    state.queue_rescan(p["token"], p["chain"], p["is_pregraduation"])
                    continue
                scored = _safe(score_solana_mint, p["token"], chain, is_pregraduation=p["is_pregraduation"])
                madeonsol_calls += 3  # risk + holders + bundle
                if _handle_scored(scored, chain, source="madeonsol", board=board,
                                   is_pregraduation=p["is_pregraduation"]):
                    alerts_sent += 1
            if to_process:
                print(f"[layer0/8:{chain}] deep-scored {len(to_process)} token(s) "
                      f"({len(to_process) * 3} MadeOnSol calls){', ' + str(len(overflow)) + ' re-queued' if overflow else ''}")
    else:
        print("[layer0/8:solana/rhc] BLOCKED: MADEONSOL_API_KEY not set")
        if _stats():
            _stats().note_module("layer8 deep-score (solana/rhc)", False, "MADEONSOL_API_KEY not set")
    return alerts_sent, madeonsol_calls


def _run_fomo_cycle(report, _summary_path=None):
    # The exact Layer 2 (buy convergence) + Layer 9 (sell mirror) logic
    # that used to be inline in run_poll_slow, extracted for the same reason
    # as _run_layer1_cycle -- shared between there (a no-op on GitHub Actions
    # now) and run_poll_madeonsol() (Ali's own PC). Returns
    # (alerts_sent, madeonsol_calls).
    alerts_sent = 0
    madeonsol_calls = 0
    active_tokens = set()
    # --- Layer 2 (buy-side convergence) + Layer 9 (sell-side mirror), sharing
    # one KOL-feed fetch per chain instead of two -- see layers/kol_feed.py.
    # Also the source of Layer 9's balance-snapshot refresh and of Layer 8's
    # event-triggered re-score signal (fresh MC points). ---
    if report["stage2"]["layer2_convergence"]:
        for chain in ("solana", "robinhood_chain"):
            fetched = _safe(fetch_kol_feed_both, chain)
            madeonsol_calls += fetched.get("calls_made", 0)
            if not fetched["ok"]:
                print(f"[layer2+9:{chain}] skipped: {fetched.get('reason')}")
                if _summary_path:
                    with open(_summary_path, "a") as _f:
                        _f.write(f"- [layer2+9:{chain}] FAILED: {fetched.get('reason')}\n")
                if _stats():
                    _stats().note_module(f"layer2+9 kol-feed ({chain})", False, fetched.get("reason"))
                continue
            if _stats():
                _stats().note_module(f"layer2+9 kol-feed ({chain})", True)
            print(f"[layer2+9:{chain}] kol-feed fetch mode={fetched['mode']} "
                  f"({fetched['calls_made']} MadeOnSol call(s))")
            buy_trades, sell_trades = fetched["buy_trades"], fetched["sell_trades"]
            if _summary_path:
                with open(_summary_path, "a") as _f:
                    _f.write(f"- [layer2+9:{chain}] OK mode={fetched['mode']} "
                             f"buy_trades={len(buy_trades)} sell_trades={len(sell_trades)}\n")

            # -- Layer 2: convergence alerts + MC-point recording, and the
            # event-triggered re-score check for previously-failing tokens --
            result = poll_layer2(chain, trades=buy_trades)

            # Latest known mcap per token this cycle, from the same KOL-feed
            # batch -- feeds handle_stage2_candidate's mcap-floor gate below
            # without any extra fetch.
            latest_mc_by_token = {}
            for token, mc, ts in result.get("mc_points", []):
                prior = latest_mc_by_token.get(token)
                if prior is None or ts >= prior[1]:
                    latest_mc_by_token[token] = (mc, ts)

            # Single-trader buy alerts (Ali, Sept 24 2026 -- see
            # single_trader_buy_events' docstring): fires the moment ANY one
            # of the 38 tracked people buys, not just when 2+ converge.
            # Sent before the convergence block below so if both fire for
            # the same token this cycle, the individual signal shows first.
            for ev in result.get("single_events", []):
                token = ev["token"]
                alert = Alert(token[:8], token, chain, f"{ev['name']} bought this token")
                alert.set_tag("Chain", chain).set_tag("Trader", f"{ev['name']} ({ev['tier']})")
                send_res = _alert(alert, "layer2_single")
                print(f"[layer2:{chain}] {token[:8]} single tracked-trader buy by {ev['name']} -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                if _stats():
                    _stats().note_copytrade(f"Fomo: {ev['name']} bought {token[:8]} [{chain}]")

            for ev in result["events"]:
                active_tokens.add(ev["token"])
                alert = Alert(ev["token"][:8], ev["token"], chain,
                               f"{ev['count']} tracked wallets converged on this token")
                alert.set_tag("Chain", chain).set_tag("Convergence", f"{ev['count']} wallets")
                send_res = _alert(alert, "layer2")
                print(f"[layer2:{chain}] convergence alert -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                if _stats():
                    _stats().note_copytrade(f"Fomo CONVERGENCE: {ev['count']} wallet(s) on {ev['token'][:8]} [{chain}]")
                    _stats().moonshots.append(f"{ev['token'][:8]} [{chain}] {ev['count']}-wallet Fomo convergence")
                # FIXED Sept 30 2026: this Stage 2 block used to sit inside the
                # large-untracked-buy loop below instead of this convergence
                # loop -- so a real 2+-trader convergence NEVER reached Stage
                # 2, and any large untracked buy crashed the whole cycle
                # (KeyError on ev["count"], which untracked events don't
                # carry), taking Layer 9's sell mirror down with it.
                # -- Shared execution core (Ali, Sept 23 2026: "point 5 ...
                # should cover all 3 platforms" -- decided: Pump.fun/Fomo
                # convergence feeds the SAME executor.entrypoint used by
                # StonkFun's worker, not a separate worker file. Stage 1 =
                # launchpad-level (worker_stonkfun_snipe.py), Stage 2 =
                # Fomo-roster convergence (here). graduated=True is an
                # assumption, not a confirmed field: MadeOnSol's /kol/feed
                # only surfaces real on-chain swaps, which by construction
                # trade against live pool liquidity -- a bonding-curve-only
                # token has no swap for a wallet-copy feed to see. If that
                # assumption is ever wrong for some chain/venue, Stage 2
                # would fire early; flagging it here rather than deciding
                # it silently. Calling this does NOT move real money --
                # handle_stage2_candidate only records position/conviction
                # state; swap_executor's execute_buy_* paths are still
                # untested placeholders, same safety floor as worker_
                # stonkfun_snipe.py's own --dry-run default. --
                # handle_stage2_candidate makes no network calls of its own
                # (see executor/entrypoint.py's docstring) -- called directly,
                # not through _safe, which exists for network-touching calls.
                mc_entry = latest_mc_by_token.get(ev["token"])
                stage2 = handle_stage2_candidate(
                    chain, ev["token"],
                    current_mcap_usd=mc_entry[0] if mc_entry else None,
                    fomo_convergence_count=ev["count"], graduated=True,
                )
                if stage2["fired"]:
                    print(f"[layer2:{chain}] STAGE2 FIRED for {ev['token'][:8]} -- "
                          f"${stage2['position_usd']:.2f}, conviction {stage2['conviction_score']}, "
                          f"ladder={stage2['trim_ladder']} ({stage2['reason']})")
                    if _stats():
                        _stats().note_execution(2, chain, ev["token"], stage2["position_usd"], stage2["conviction_score"])
                else:
                    print(f"[layer2:{chain}] stage2 not fired for {ev['token'][:8]}: {stage2['reason']}")
                state.record_autobuy_verdict(ev["token"], chain, stage2)


            # Large buy from an UNTRACKED name (Ali, Sept 24 2026 -- "a new
            # person...good cash balance...maybe he can be an insider
            # entering"). Not a track-record promotion, just a surfaced
            # signal -- see large_untracked_buys' docstring for the
            # first-pass threshold.
            for ev in result.get("large_untracked_events", []):
                token = ev["token"]
                alert = Alert(token[:8], token, chain,
                               f"Large buy ({ev['sol_amount']:.1f} SOL) from untracked wallet")
                alert.set_tag("Chain", chain)
                alert.set_tag("Possible insider", f"{ev['name']} ({ev['sol_amount']:.1f} SOL, not on your tracked list)")
                send_res = _alert(alert, "layer2_untracked_large")
                print(f"[layer2:{chain}] {token[:8]} large untracked buy by {ev['name']} "
                      f"({ev['sol_amount']:.1f} SOL) -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                if _stats():
                    _stats().note_copytrade(f"Fomo large untracked buy: {ev['name']} "
                                             f"{ev['sol_amount']:.1f} SOL on {token[:8]} [{chain}]")

            for token, mc, ts in result.get("mc_points", []):
                state.record_mc_point(token, mc, ts)
                _maybe_queue_rescan(token, chain, mc)
            if result.get("mc_points"):
                print(f"[layer2:{chain}] recorded {len(result['mc_points'])} MC point(s) into "
                      f"state ({state.backend()}) for Layer 8")

            # -- Layer 9 balance-snapshot refresh: any SELL_WATCH_ROSTER member's
            # buy this cycle gets their balance in that token snapshotted/refreshed,
            # batched into ONE Mobula call for the whole chain (not one per wallet). --
            buy_pairs = []
            for t in buy_trades:
                name = t.get("kol_name")
                wallet = t.get("wallet_address")
                token = t.get("token_mint") or t.get("mint") or t.get("token_address")
                if name in SELL_WATCH_ROSTER and wallet and token:
                    buy_pairs.append((wallet, token))
            if buy_pairs and CONFIG.mobula_api_key:
                wallets_needing_refresh = list({w for w, _ in buy_pairs})
                portfolio = _safe(fetch_wallets_portfolio, wallets_needing_refresh, blockchain=chain)
                if portfolio.get("ok"):
                    balances = extract_balances_for_pairs(
                        portfolio["raw"].get("json") or {}, buy_pairs)
                    for (wallet, token), bal in balances.items():
                        state.record_balance(wallet, token, bal)
                    print(f"[layer9:{chain}] refreshed {len(balances)}/{len(buy_pairs)} tracked-wallet "
                          f"balance(s) via 1 batched Mobula call")
                else:
                    print(f"[layer9:{chain}] balance refresh skipped: {portfolio.get('reason')}")

            # -- Layer 9: sell alerts, using whatever balances were just refreshed
            # (or recorded on a prior cycle) as the "prior balance" for % sold --
            held_tokens = state.held_token_addresses()
            relevant = active_tokens | held_tokens
            if not relevant:
                print(f"[layer9:{chain}] no active/held tokens this cycle -- nothing to watch yet")
                continue
            sell_pairs = [(t.get("wallet_address"), t.get("token_mint") or t.get("mint") or t.get("token_address"))
                          for t in sell_trades if t.get("wallet_address")]
            prior_balances = state.prior_balances_map(sell_pairs)
            # Layer 10 (Ali, Sept 23 2026: built Sept 22, never called) -- lets
            # Layer 9 catch a sell from a wallet that isn't on the static
            # roster and isn't the deployer, by tracing who funded it. Gated
            # on mobula_api_key since the funding-chain trace needs it (a
            # cached/kol_name hit is free either way, resolve_identity fails
            # closed to "untracked" without a key -- gating here just skips
            # the wasted attempt, not a behavior change).
            resolve_unknown = (
                (lambda addr: layer10.resolve_identity(chain, addr).get("tier"))
                if CONFIG.mobula_api_key else None
            )
            result9 = _safe(poll_layer9, chain, relevant, prior_balances=prior_balances,
                             trades=sell_trades, resolve_unknown=resolve_unknown)
            if not result9["ok"]:
                print(f"[layer9:{chain}] skipped: {result9.get('reason')}")
                if _stats():
                    _stats().note_module(f"layer9 sell-mirror ({chain})", False, result9.get("reason"))
                continue
            if _stats():
                _stats().note_module(f"layer9 sell-mirror ({chain})", True)
            for ev in result9["events"]:
                alert = Alert(ev["token"][:8], ev["token"], chain,
                              f"Tracked entity SOLD: {ev['who']}")
                pct_str = f"{ev['pct_of_position']:.0f}%" if ev["pct_of_position"] is not None else "unknown %"
                alert.set_tag("Chain", chain).set_tag("SELL", f"{ev['who']} ({ev['role']}) sold {pct_str}")
                send_res = _alert(alert, "layer9")
                print(f"[layer9:{chain}] sell alert -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                if _stats():
                    _stats().note_copytrade(f"SELL: {ev['who']} ({ev['role']}) sold {pct_str} of "
                                             f"{ev['token'][:8]} [{chain}]")
                    if ev["pct_of_position"] is not None and ev["pct_of_position"] >= 50:
                        _stats().rugs.append(f"{ev['token'][:8]} [{chain}] {ev['who']} dumped {pct_str} "
                                              f"of position (tracked-wallet sell, not price-based)")
                # Arithmetic-only update -- no extra Mobula call at sell time.
                new_bal = update_balance_after_sell(ev["wallet"], ev["token"], ev.get("amount_for_pct"),
                                                     prior_balances)
                if new_bal is not None:
                    state.record_balance(ev["wallet"], ev["token"], new_bal)
    else:
        print("[layer2+9] BLOCKED: MADEONSOL_API_KEY not set")
        if _stats():
            _stats().note_module("layer2+9 kol-feed", False, "MADEONSOL_API_KEY not set")
    return alerts_sent, madeonsol_calls


def run_poll_slow():
    """The expensive pieces: Layer 8's per-token MadeOnSol deep scoring (off
    the pending-rescore queue Layer 1/run_poll_fast feeds) and Layers 2+9's
    wallet-activity checks. Meant to run every 15-20 min, not 10 -- see
    README's call-budget section."""
    _start_cycle_stats("SLOW cycle")
    report = readiness_report()
    print("Readiness:", report)
    alerts_sent = 0
    madeonsol_calls = 0

    _summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if _summary_path:
        with open(_summary_path, "a") as _f:
            _f.write("### Slow-cycle readiness\n```\n" + str(report) + "\n```\n")

    # --- Layer 11: fetch the DexScreener boost board once for this cycle
    # too -- run_poll_fast and run_poll_slow are separate processes (GitHub
    # Actions cron), so each needs its own fetch; still just 2 keyless
    # calls per cycle. See layer11_social_buzz.py. ---
    board = _safe(fetch_boost_board)
    if not (isinstance(board, dict) and board.get("ok")):
        print(f"[layer11] boost board fetch failed this cycle: {board}")
        board = None

    if IS_GITHUB_ACTIONS:
        print("[layer0/8] SKIPPED on GitHub Actions -- MadeOnSol free-key IP-rate-limit "
              "(see run_poll_madeonsol / README). Run 'python scheduler.py --poll-madeonsol' "
              "on your own PC instead.")
    else:
        a8, c8 = _run_layer8_cycle(board)
        alerts_sent += a8
        madeonsol_calls += c8

    if IS_GITHUB_ACTIONS:
        print("[layer2+9] SKIPPED on GitHub Actions -- MadeOnSol free-key IP-rate-limit "
              "(see run_poll_madeonsol / README). Run 'python scheduler.py --poll-madeonsol' "
              "on your own PC instead.")
        if _summary_path:
            with open(_summary_path, "a") as _f:
                _f.write("- [layer2+9] SKIPPED on GitHub Actions -- moved to --poll-madeonsol "
                         "(MadeOnSol free-key rate limit)\n")
    else:
        a29, c29 = _run_fomo_cycle(report, _summary_path)
        alerts_sent += a29
        madeonsol_calls += c29

    print(f"\nSlow cycle done. {alerts_sent} alert(s) delivered. ~{madeonsol_calls} MadeOnSol call(s) "
          f"used this cycle (see README's call-budget section for how that compares to the 200/day cap "
          f"at whatever cadence this is running on).")

    if _summary_path:
        with open(_summary_path, "a") as _f:
            _f.write(f"\n### Slow-cycle result\n- alerts_sent: {alerts_sent}\n"
                     f"- madeonsol_calls: {madeonsol_calls}\n")

    _safe(state.record_runner_heartbeat, "poll-slow", _runner_where(), f"alerts={alerts_sent}")
    if _stats():
        _print_cycle_summary(_stats())


# One-off manual seeding runs (Ali, Sept 24 2026) -- each entry here is a
# real wallet address Ali pulled off pump.fun's own signed-in leaderboard
# himself (see layers/layer2b_pumpfun_smart_money.py's seed_manual_wallets
# docstring for why this exists instead of a scraped/researched list).
# This dict can just grow over time as Ali hands over more addresses --
# already-present wallets are silently skipped (see seed_manual_wallets),
# so re-running this is always safe.
PUMPFUN_MANUAL_SEED_BATCHES = {
    "pumpfun_leaderboard_1D_Sept24": [
        "4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo",
        "4UrFSCrGxgoCtCUBAEZq7ZmPK3Pczkxx7PwYnkBMi1KR",
        "Bmi9zf27MNN5pjCtyv2Y15TDQoYgcbcmPTxvEoQ6UwWs",
        "49MHZz9c1mE1ePTcLxBWPjf7KKzt3Ntd25JnspKvV8vL",
        "3jWTgYPG5s7WfaRvppPBXio4hHQxg18fLUkV2z5covSQ",
        "J23qr98GjGJJqKq9CBEnyRhHbmkaVxtTJNNxKu597wsA",
        "24L6uekgvsQRgT41DZRhahEAAVnkseTT7x4qyigC3HvH",
        "2jgmHtkCkJXm3Xq4dp9DgippkQjXLK3rhaREAz7oG7s7",
        "6HJetMbdHBuk3mLUainxAPpBpWzDgYbHGTS2TqDAUSX2",
        "9djgawmgpGrzt7DQoJ6tA2YW4gQyt3yH19uVZ3e2T3JJ",
        "G29kbPokFzmVeYuZB1ihA7AmGzLjyDaECEyRMKhHiR4J",
        "2M2vLX34LXMg24dMEnjWHvRXS1tshpEDRWzmXgV8ENNZ",
        "uDYvqwgSxNDMKGPaMJ7JqyadEg1EhxkuCx9hbLSanHA",
    ],
}


PUMPFUN_MANUAL_SEED_BATCHES_DONE_KEY = "pumpfun_manual_seed_batches_done"


def run_seed_pumpfun_wallets():
    """Writes PUMPFUN_MANUAL_SEED_BATCHES into the live smart-money roster.
    Must run somewhere that can actually reach Upstash -- GitHub Actions,
    not Ali's own machine (confirmed Sept 24 2026: his local network blocks
    the outbound connection to Upstash at the proxy level, unrelated to
    this code).

    Tracks completion PER BATCH KEY (not one global flag) -- fixed Sept 28
    2026 after the original single `pumpfun_manual_seed_done` bool silently
    stopped a newly-added batch from ever being applied once it had already
    fired once live. Now: a batch name already recorded as done is skipped
    (no wasted Upstash round-trip re-checking wallets that are already in
    the roster), but any NEW batch key Ali hands over gets picked up
    automatically on the next real poll-fast cycle with zero manual Upstash
    access needed."""
    from layers.layer2b_pumpfun_smart_money import seed_manual_wallets, get_smart_money_roster
    print(f"[seed] state backend: {state.backend()}")
    done = set(state.get_value(PUMPFUN_MANUAL_SEED_BATCHES_DONE_KEY) or [])
    for source, wallets in PUMPFUN_MANUAL_SEED_BATCHES.items():
        if source in done:
            continue
        result = seed_manual_wallets(wallets, source=source, note=f"batch={source}")
        print(f"[seed] {source}: added={len(result['added'])} "
              f"already_present={len(result['already_present'])} rejected={result['rejected']}")
        done.add(source)
    state.set_value(PUMPFUN_MANUAL_SEED_BATCHES_DONE_KEY, sorted(done))
    roster = get_smart_money_roster()
    print(f"[seed] roster size now: {len(roster)}")
    print(f"[seed] roster: {sorted(roster)}")


def run_poll_madeonsol():
    """Meant to run on Ali's own PC, NOT GitHub Actions (Ali, Sept 24 2026).
    Covers every layer that actually calls MadeOnSol -- Layer 1 (pump.fun
    deployer alerts), Layer 8 (deep-scoring the rescan queue), and Layer 2+9
    (Fomo buy convergence + sell mirror) -- confirmed live that a free
    MadeOnSol key gets rate-limited ("Too many IP addresses for one free
    key... 8 IP addresses today") by GitHub Actions' rotating runner IPs.
    A home/office IP is stable, so running this from Task Scheduler on
    Ali's own machine sidesteps the limit for free, no paid tier needed.

    poll-fast.yml/poll-slow.yml skip these exact three layers now (guarded
    by IS_GITHUB_ACTIONS), so running this alongside them will not produce
    duplicate alerts for the same event -- each layer runs in exactly one
    place. Schedule via Windows Task Scheduler, e.g. every 15 minutes while
    the PC is on:

        schtasks /create /tn "GemAlert MadeOnSol" /sc minute /mo 15 ^
            /tr "cmd /c cd /d C:\\path\\to\\gem-alert-s1c_6 && python scheduler.py --poll-madeonsol" ^
            /st 00:00

    Requires a .env file in the project folder with the same keys as
    GitHub Actions Secrets (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN, MADEONSOL_API_KEY,
    MOBULA_API_KEY) -- auto-loaded via python-dotenv, no manual env-var
    setup needed. Not something this environment could test live end to
    end (its own local network is confirmed blocked from reaching Upstash
    at the proxy level, per run_seed_pumpfun_wallets' docstring) -- Ali's
    real machine's network is untested for this specifically, so the first
    real run is the real answer; if it can't reach Upstash or Telegram
    either, that's a separate, new finding to report back."""
    _start_cycle_stats("MADEONSOL cycle")
    report = readiness_report()
    print("Readiness:", report)
    board = _safe(fetch_boost_board)
    if not (isinstance(board, dict) and board.get("ok")):
        print(f"[layer11] boost board fetch failed this cycle: {board}")
        board = None

    total_alerts = 0
    total_calls = 0

    a1, c1 = _run_layer1_cycle()
    total_alerts += a1
    total_calls += c1

    a8, c8 = _run_layer8_cycle(board)
    total_alerts += a8
    total_calls += c8

    a29, c29 = _run_fomo_cycle(report)
    total_alerts += a29
    total_calls += c29

    # Layer 13 (Fomo copy-trading/thesis, Sept 30 2026) -- dashboard-only,
    # never sends a Telegram alert, so it does not add to total_alerts.
    # Runs here (not GitHub Actions) because a real hit spends MadeOnSol
    # budget via score_solana_mint, same reason Layer 1/8/2+9 live here.
    l13 = _safe(poll_layer13_fomo_copytrade)
    if isinstance(l13, dict) and l13.get("ok"):
        print(f"[layer13] cycle done: {l13}")
    elif isinstance(l13, dict):
        print(f"[layer13] skipped: {l13.get('reason')}")

    print(f"\nMadeOnSol-only cycle done. {total_alerts} alert(s) delivered. "
          f"~{total_calls} MadeOnSol call(s) used.")

    _safe(state.record_runner_heartbeat, "poll-madeonsol", _runner_where(),
          f"alerts={total_alerts} madeonsol_calls={total_calls}")
    if _stats():
        _print_cycle_summary(_stats())


def run_poll():
    """Convenience for local/manual runs -- fast then slow in one process.
    Production runs these on separate crons; see poll-fast.yml/poll-slow.yml."""
    run_poll_fast()
    print()
    run_poll_slow()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--poll", action="store_true", help="fast + slow in one process (local/manual only)")
    parser.add_argument("--seed-pumpfun-wallets", action="store_true", help="one-off: writes PUMPFUN_MANUAL_SEED_BATCHES into the live roster")
    parser.add_argument("--poll-fast", action="store_true", help="LEGACY/manual-only: discovery looped every ~20min internally (run_poll_fast_loop), from before cron-job.org existed. Do NOT use this in poll-fast.yml -- it fights cron-job.org's own 10-min re-dispatch for the concurrency slot (real bug, Sept 30 2026). Use --poll-fast-once there instead.")
    parser.add_argument("--poll-fast-once", action="store_true", help="discovery only, single cycle, no loop -- for manual/local testing")
    parser.add_argument("--poll-slow", action="store_true", help="expensive layers only -- runs on the slow cron")
    parser.add_argument("--poll-madeonsol", action="store_true", help="Layer 1 + Layer 8 + Layer 2+9 only -- run on your OWN PC via Task Scheduler, never on GitHub Actions (MadeOnSol free-key rate limit)")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(run_self_test())
    elif args.poll_fast:
        run_poll_fast_loop()
    elif args.poll_fast_once:
        run_poll_fast()
    elif args.poll_slow:
        run_poll_slow()
    elif args.poll_madeonsol:
        run_poll_madeonsol()
    elif args.poll:
        run_poll()
    elif args.seed_pumpfun_wallets:
        run_seed_pumpfun_wallets()
    else:
        parser.print_help()

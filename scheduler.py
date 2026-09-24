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
import subprocess
import sys
import time
from datetime import datetime, timezone

from config import CONFIG
from layers.kol_feed import fetch_kol_feed_both
from layers.layer0_scoring import fetch_mobula_pulse, score_mobula_pulse_items, score_solana_mint
from layers.layer1_deployer import poll_layer1, chain_for_cycle
from layers.layer0c_stonkfun_scoring import poll_layer0c, poll_layer0c_momentum, \
    MOMENTUM_GEM_MIN_MULTIPLE, MOMENTUM_LOOKBACK_HOURS
from layers.layer2_convergence import poll_layer2
from executor.entrypoint import handle_stage1_candidate, handle_stage2_candidate
from layers.layer3_backing_check import check_backing_spike
from layers.layer11_social_buzz import fetch_boost_board, check_buzz
from layers.pumpfun_trades import fetch_recent_signatures, fetch_transaction, decode_trade
from layers.layer2b_pumpfun_smart_money import process_trade as pumpfun_process_trade, \
    detect_pumpfun_convergence, get_smart_money_roster, single_wallet_buy_events
from layers.layer7_correlation import AlertEvent, detect_mega_alerts
import layers.layer10_insider_cluster as layer10
from layers.layer4_news import fetch_cryptopanic_posts, parse_cryptopanic_posts, \
    fetch_binance_new_listings, parse_binance_new_listings
from layers.layer6_exit_realizable import fetch_wallet_portfolio
from layers.layer8_momentum_override import detect_momentum_override, mc_moved_enough
from layers.layer9_sell_mirror import poll_layer9, update_balance_after_sell
from layers.roster import SELL_WATCH_ROSTER
from layers.wallet_balance import fetch_wallets_portfolio, extract_balances_for_pairs
from telegram_alert import Alert, send_alert
from utils.http import ApiUnreachable
import state


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

MOBULA_PULSE_CHAINS = [("bsc", "bnb:bnb"), ("base", "base:base")]  # Base re-enabled Sept 24 2026 (Ali: Fomo trades Base too) -- TON/ETH still dropped, scope cut Sept 22, 2026


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
    return send_res


def _handle_scored(scored: dict, chain: str, source: str, mc: float = None, board: dict = None) -> bool:
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
    function actually has). Calling this does NOT move real money --
    handle_stage1_candidate only records position/conviction state;
    swap_executor's execute_buy_* paths are still untested placeholders,
    same safety floor as every other wiring done tonight."""
    if "error" in scored or scored.get("score") is None:
        return False
    mint = scored["address"]
    sr = scored["score"]
    if mint:
        state.set_last_score(mint, sr.score, sr.band, mc)
        stage1 = handle_stage1_candidate(
            chain, mint, score_band=sr.band, deployer_tier=None,
            convergence_count=0, entry_mcap=mc,
        )
        if stage1["fired"]:
            print(f"[layer0/8:{chain}] STAGE1 FIRED for {mint[:8]} -- "
                  f"${stage1['position_usd']:.2f}, conviction {stage1['conviction_score']}, "
                  f"ladder={stage1['trim_ladder']} ({stage1['reason']})")

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

    if sr.band == "D":
        mc_hist = state.get_mc_history(mint) if mint else []
        mom = detect_momentum_override(sr.band, mc_hist)
        if not mom.triggered:
            return False  # fails safety score, no momentum yet -- suppressed, per spec
        alert = Alert((mint or "?")[:8], mint, chain, "HIGH-RISK MOMENTUM override")
        alert.set_tag("Chain", chain).set_tag("Score", f"{sr.score}/100 (band D)")
        alert.set_tag("HIGH-RISK MOMENTUM", mom.reason)
    else:
        alert = Alert((mint or "?")[:8], mint, chain, f"Layer 0{'b' if source == 'mobula' else ''} structural score")
        alert.set_tag("Chain", chain).set_tag("Score", f"{sr.score}/100 (band {sr.band})")
    if backing_tag:
        alert.set_tag("Backing", backing_tag)
    if buzz_tag:
        alert.set_tag("Buzz", buzz_tag)
    layer_name = "layer0b" if source == "mobula" else "layer0"
    send_res = _alert(alert, layer_name)
    print(f"[layer0/8:{chain}] {mint} band {sr.band} -> {send_res}")
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


def run_poll_fast():
    """Discovery: Layer 1 (deployer alerts) + Layer 0b (Mobula Pulse
    scoring) + Layer 4 (news) + Layer 6 (exit-risk snapshot). No per-token
    MadeOnSol scoring here -- meant to run every 10 min, unchanged, because
    speed on brand-new token discovery is the single most valuable thing in
    this system. See module docstring."""
    # One-off pump.fun manual wallet seeding (Ali, Sept 24 2026), run from
    # HERE rather than its own workflow file or an edit to an existing one:
    # GitHub blocks Ali's saved token from pushing ANY change to a workflow
    # YAML (create OR modify) without the `workflow` scope -- confirmed live
    # Sept 24, twice. A plain Python change has no such restriction. Guarded
    # by a state flag so it only actually runs once (seed_manual_wallets is
    # idempotent regardless, but no reason to spend an Upstash round-trip on
    # it every 10 minutes forever).
    if not state.get_value("pumpfun_manual_seed_done"):
        run_seed_pumpfun_wallets()
        state.set_value("pumpfun_manual_seed_done", True)

    report = readiness_report()
    print("Readiness:", report)
    alerts_sent = 0
    madeonsol_calls = 0

    # --- Layer 11: fetch the DexScreener boost board once for this whole
    # cycle (2 keyless calls total) -- matched in-memory per token below,
    # see layer11_social_buzz.py. ---
    board = _safe(fetch_boost_board)
    if not (isinstance(board, dict) and board.get("ok")):
        print(f"[layer11] boost board fetch failed this cycle: {board}")
        board = None

    # --- Layer 1: deployer alerts. Elite-tier finds go straight onto the
    # Layer 8 deep-score queue for the NEXT slow cycle to pick up -- not
    # scored here, to keep this cycle cheap and fast.
    #
    # Only ONE chain is checked per fast cycle, alternating Solana/RHC by
    # wall-clock time (chain_for_cycle) -- see the module docstring above for
    # why (no combined multi-chain endpoint exists, and the reputation-tier
    # field is already read off this same single call, so there's no
    # redundant call to dedup away; alternating is the only real lever).
    # This halves Layer 1 to 144 MadeOnSol calls/day; each chain still gets
    # checked roughly every 20 minutes. `since` (the other chain's or this
    # chain's own last-checked timestamp) is passed so an alert firing during
    # a skipped cycle is caught late instead of dropped. ---
    if CONFIG.layer1_ready():
        chain = chain_for_cycle(time.time())
        since = state.get_layer1_last_checked(chain)
        result = _safe(poll_layer1, chain, since)
        madeonsol_calls += 1
        if not result["ok"]:
            print(f"[layer1:{chain}] skipped: {result.get('reason')}")
        else:
            state.set_layer1_last_checked(chain, datetime.now(timezone.utc).isoformat())
            for a in result["alerts"]:
                alert = Alert(a["token_address"][:8], a["token_address"], chain,
                               "Elite/good-tier deployer just launched a token")
                alert.set_tag("Chain", chain).set_tag("Deployer", a["deployer_tier"])
                send_res = _alert(alert, "layer1")
                print(f"[layer1:{chain}] alert -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                if a["deployer_tier"] == "elite" and a["token_address"]:
                    state.queue_rescan(a["token_address"], chain, is_pregraduation=(chain == "solana"))
        print(f"[layer1] checked {chain} this cycle (alternates each fast cycle, ~144 MadeOnSol calls/day "
              f"total instead of 288 -- see README's call-budget section)")
    else:
        print("[layer1] BLOCKED: MADEONSOL_API_KEY not set")

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
            newly_alerted.append(mint)
        state.mark_layer0c_seen(newly_alerted)
        print(f"[layer0c] {len(stonkfun_result['tokens'])} SOL-quoted launch(es) fetched, "
              f"{len(newly_alerted)} new A/B-band alert(s)")
    else:
        reason = stonkfun_result.get("reason") if isinstance(stonkfun_result, dict) else str(stonkfun_result)
        print(f"[layer0c] fetch failed: {reason}")

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
        state.mark_layer0c_momentum_checked(mom_result["checked"])
        print(f"[layer0c-momentum] {len(mom_result['checked'])} recent launch(es) deep-checked, "
              f"{len(mom_result['gems'])} momentum gem(s) found")
    else:
        reason = mom_result.get("reason") if isinstance(mom_result, dict) else str(mom_result)
        print(f"[layer0c-momentum] fetch failed: {reason}")

    # --- Layer 4: news/exchange (no MadeOnSol cost either way) ---
    if report["stage1"]["layer4_news_exchange"]["cryptopanic"]:
        cp = _safe(fetch_cryptopanic_posts, "rising")
        if cp["ok"]:
            posts = parse_cryptopanic_posts(cp["raw"].get("json") or {})
            print(f"[layer4:cryptopanic] {len(posts)} rising posts fetched")
        else:
            print(f"[layer4:cryptopanic] fetch failed: {cp.get('reason')}")
    else:
        print("[layer4:cryptopanic] BLOCKED: CRYPTOPANIC_AUTH_TOKEN not set")

    bn = _safe(fetch_binance_new_listings)
    if bn["ok"]:
        listings = parse_binance_new_listings(bn["raw"].get("json") or {})
        seen = state.binance_seen_listings()
        new_listings = [item for item in listings if (item.get("code") or item.get("title")) not in seen]
        print(f"[layer4:binance] {len(listings)} recent listing article(s) fetched, "
              f"{len(new_listings)} not-yet-alerted")
        just_alerted = []
        for item in new_listings[:3]:
            alert = Alert(item.get("title", "?")[:20], "n/a", "n/a", item["title"])
            alert.set_tag("News", "exchange-listing (Binance)")
            send_res = send_alert(alert)
            state.log_full_alert("layer4_news", alert.chain, alert.token_symbol, alert.token_address,
                                  alert.headline, dict(alert.tags))
            if send_res.get("sent"):
                alerts_sent += 1
            just_alerted.append(item.get("code") or item.get("title"))
        state.mark_binance_seen(just_alerted)
    else:
        print("[layer4:binance] fetch failed (network-level or API-level -- see raw response)")

    # --- Layer 0b: Mobula Pulse, one call per chain, scores every item in
    # that response -- free (no MadeOnSol cost), so this runs every fast
    # cycle rather than waiting for the slow one. ---
    if CONFIG.mobula_api_key:
        for chain, chain_id in MOBULA_PULSE_CHAINS:
            raw = _safe(fetch_mobula_pulse, chain_id)  # ONE call per chain, covers every token in it
            if not raw.get("ok"):
                print(f"[layer0b/8:{chain}] pulse fetch failed")
                continue
            items = (raw.get("json") or {}).get("data", []) if isinstance(raw.get("json"), dict) else []
            for scored in score_mobula_pulse_items(chain, items):
                mint = scored["address"]
                mc = scored["raw"].get("marketCap") or scored["raw"].get("market_cap")
                if mint and mc is not None:
                    state.record_mc_point(mint, mc)
                if _handle_scored(scored, chain, source="mobula", mc=mc, board=board):
                    alerts_sent += 1
            print(f"[layer0b/8:{chain}] scored {len(items)} Pulse item(s) (1 Mobula call)")
    else:
        print("[layer0b/8] BLOCKED: MOBULA_API_KEY not set")

    # --- Layer 2b: self-built pump.fun smart-money convergence (Ali, Sept
    # 23 2026). Keyless (free Solana RPC only), so always attempted, no
    # CONFIG gate -- see poll_layer2b_pumpfun_smart_money's docstring for
    # the real per-cycle call cost this incurs. ---
    l2b_result = _safe(poll_layer2b_pumpfun_smart_money)
    if isinstance(l2b_result, dict) and l2b_result.get("ok"):
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
        print(f"[layer2b] checked {l2b_result['checked']} pump.fun signature(s), "
              f"{l2b_result['decoded']} decoded buy(s), {len(l2b_result['promotions'])} new smart-money "
              f"promotion(s), roster size now {l2b_result['roster_size']}")
    else:
        reason = l2b_result.get("reason") if isinstance(l2b_result, dict) else str(l2b_result)
        print(f"[layer2b] fetch failed: {reason}")

    # --- Layer 6: exit-risk / realizable-gain snapshot (Mobula only, no
    # MadeOnSol cost) -- kept fast for quicker rug detection. ---
    if report["stage2"]["layer6_exit_realizable"]:
        portfolio = _safe(fetch_wallet_portfolio)
        if portfolio["ok"]:
            held = (portfolio["raw"].get("json") or {}).get("data", {}).get("assets", [])
            print(f"[layer6] {len(held)} held assets fetched; exit-risk diffing needs a prior "
                  f"snapshot (state.py) -- first cycle establishes the baseline only")
            state.save_snapshot(held)
        else:
            raw = portfolio.get("raw") if isinstance(portfolio, dict) else None
            if isinstance(raw, dict):
                detail = raw.get("json") or raw.get("text") or f"HTTP {raw.get('status_code')}"
            else:
                detail = portfolio.get("reason") if isinstance(portfolio, dict) else str(portfolio)
            print(f"[layer6] portfolio fetch failed: {detail}")
    else:
        print("[layer6] BLOCKED: MOBULA_API_KEY and/or WALLET_ADDRESSES not set")

    print(f"\nFast cycle done. {alerts_sent} alert(s) delivered. ~{madeonsol_calls} MadeOnSol call(s) "
          f"used ({state.pending_rescan_count()} token(s) now queued for the next slow cycle's deep-score "
          f"pass). See README's call-budget section for how this compares to the 200/day cap.")


def run_poll_slow():
    """The expensive pieces: Layer 8's per-token MadeOnSol deep scoring (off
    the pending-rescore queue Layer 1/run_poll_fast feeds) and Layers 2+9's
    wallet-activity checks. Meant to run every 15-20 min, not 10 -- see
    README's call-budget section."""
    report = readiness_report()
    print("Readiness:", report)
    alerts_sent = 0
    madeonsol_calls = 0
    active_tokens = set()

    # --- Layer 11: fetch the DexScreener boost board once for this cycle
    # too -- run_poll_fast and run_poll_slow are separate processes (GitHub
    # Actions cron), so each needs its own fetch; still just 2 keyless
    # calls per cycle. See layer11_social_buzz.py. ---
    board = _safe(fetch_boost_board)
    if not (isinstance(board, dict) and board.get("ok")):
        print(f"[layer11] boost board fetch failed this cycle: {board}")
        board = None

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
                scored = _safe(score_solana_mint, p["token"], chain, is_pregraduation=p["is_pregraduation"])
                madeonsol_calls += 3  # risk + holders + bundle
                if _handle_scored(scored, chain, source="madeonsol", board=board):
                    alerts_sent += 1
            if to_process:
                print(f"[layer0/8:{chain}] deep-scored {len(to_process)} token(s) "
                      f"({len(to_process) * 3} MadeOnSol calls){', ' + str(len(overflow)) + ' re-queued' if overflow else ''}")
    else:
        print("[layer0/8:solana/rhc] BLOCKED: MADEONSOL_API_KEY not set")

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
                continue
            print(f"[layer2+9:{chain}] kol-feed fetch mode={fetched['mode']} "
                  f"({fetched['calls_made']} MadeOnSol call(s))")
            buy_trades, sell_trades = fetched["buy_trades"], fetched["sell_trades"]

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

            for ev in result["events"]:
                active_tokens.add(ev["token"])
                alert = Alert(ev["token"][:8], ev["token"], chain,
                               f"{ev['count']} tracked wallets converged on this token")
                alert.set_tag("Chain", chain).set_tag("Convergence", f"{ev['count']} wallets")
                send_res = _alert(alert, "layer2")
                print(f"[layer2:{chain}] convergence alert -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1

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
                else:
                    print(f"[layer2:{chain}] stage2 not fired for {ev['token'][:8]}: {stage2['reason']}")

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
                continue
            for ev in result9["events"]:
                alert = Alert(ev["token"][:8], ev["token"], chain,
                              f"Tracked entity SOLD: {ev['who']}")
                pct_str = f"{ev['pct_of_position']:.0f}%" if ev["pct_of_position"] is not None else "unknown %"
                alert.set_tag("Chain", chain).set_tag("SELL", f"{ev['who']} ({ev['role']}) sold {pct_str}")
                send_res = _alert(alert, "layer9")
                print(f"[layer9:{chain}] sell alert -> {send_res}")
                if send_res.get("sent"):
                    alerts_sent += 1
                # Arithmetic-only update -- no extra Mobula call at sell time.
                new_bal = update_balance_after_sell(ev["wallet"], ev["token"], ev.get("amount_for_pct"),
                                                     prior_balances)
                if new_bal is not None:
                    state.record_balance(ev["wallet"], ev["token"], new_bal)
    else:
        print("[layer2+9] BLOCKED: MADEONSOL_API_KEY not set")

    print(f"\nSlow cycle done. {alerts_sent} alert(s) delivered. ~{madeonsol_calls} MadeOnSol call(s) "
          f"used this cycle (see README's call-budget section for how that compares to the 200/day cap "
          f"at whatever cadence this is running on).")



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


def run_seed_pumpfun_wallets():
    """Writes PUMPFUN_MANUAL_SEED_BATCHES into the live smart-money roster.
    Must run somewhere that can actually reach Upstash -- GitHub Actions,
    not Ali's own machine (confirmed Sept 24 2026: his local network blocks
    the outbound connection to Upstash at the proxy level, unrelated to
    this code)."""
    from layers.layer2b_pumpfun_smart_money import seed_manual_wallets, get_smart_money_roster
    print(f"[seed] state backend: {state.backend()}")
    for source, wallets in PUMPFUN_MANUAL_SEED_BATCHES.items():
        result = seed_manual_wallets(wallets, source=source, note=f"batch={source}")
        print(f"[seed] {source}: added={len(result['added'])} "
              f"already_present={len(result['already_present'])} rejected={result['rejected']}")
    roster = get_smart_money_roster()
    print(f"[seed] roster size now: {len(roster)}")
    print(f"[seed] roster: {sorted(roster)}")


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
    parser.add_argument("--poll-fast", action="store_true", help="discovery only -- runs on the fast cron")
    parser.add_argument("--poll-slow", action="store_true", help="expensive layers only -- runs on the slow cron")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(run_self_test())
    elif args.poll_fast:
        run_poll_fast()
    elif args.poll_slow:
        run_poll_slow()
    elif args.poll:
        run_poll()
    elif args.seed_pumpfun_wallets:
        run_seed_pumpfun_wallets()
    else:
        parser.print_help()

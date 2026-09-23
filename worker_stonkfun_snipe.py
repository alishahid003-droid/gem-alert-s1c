"""
StonkFun proactive snipe worker -- Sept 23, 2026, built in response to
Ali's direct pushback: the alert-only Layer 0c momentum scan (poll_layer0c_
momentum, in scheduler.py) is reactive -- it runs on GitHub Actions' 10-min
FAST cycle and only fires after a coin has already moved 20x+. A move that
fast (LEVERCAT graduated in 13m35s) can already be near its local top by
the time a 10-minute cycle even sees it as "new".

THIS is the honest fix for that, and its limit is architectural, not a bug
GitHub Actions can be tuned around: cron-triggered ephemeral jobs cannot
poll every few seconds. This script is a continuously-running process
instead -- meant to be left running on Ali's own machine (matches his
stated preference for CMD/local over a VPS, from the earlier S1f sniper-bot
scoping), polling StonkFun every few seconds and reacting within one poll
interval of a launch starting to move, not within a 10-minute window.

WHAT THIS DOES:
  1. Every `--interval` seconds (default 8), fetches StonkFun's newest-
     launches listing (one call, free, keyless).
  2. For each launch within the lookback window not already bought/checked
     this session, fetches its per-mint detail (start mcap, current mcap,
     liquidity, creator) and runs it through layers.layer0c_stonkfun_
     scoring.is_snipe_candidate() -- CURRENT-vs-start multiple (never
     peak -- see that function's docstring for why peak would be look-
     ahead bias), a hard deployer-spammer reject, and a liquidity floor.
  3. A pass is handed to executor.entrypoint.handle_stage1_candidate() --
     the EXACT SAME trigger/circuit-breaker/budget/moonbag-ladder pipeline
     Layer 0/1/2 alerts already use. Nothing new was built for sizing,
     the daily loss circuit breaker, or exit management -- all of that is
     reused as-is.
  4. If handle_stage1_candidate fires AND --dry-run is not set, calls
     executor.swap_executor.execute_buy_solana() to actually attempt the
     buy. execute_buy_solana is quote-token-agnostic (routes through
     Jupiter, which already indexes StonkFun pools including xSOL-quoted
     ones -- confirmed live Sept 23, 2026 against LEVERCAT's real pool) --
     no new execution code was needed for xSOL/leverage-quoted tokens.

DEFAULTS TO --dry-run (no real orders, just logs what WOULD have fired) --
same "inert by design" posture as the rest of this codebase. Real buys
additionally require EXECUTION_ENABLED=true and a funded wallet key in the
environment (executor/config.py) -- this script does not bypass either
gate, it just gives them something to fire on faster than a 10-min cron
ever could.

HONEST LIMITS, said plainly, not buried:
  - No structural filter here gives real predictive edge over the other
    bots/traders watching the same public StonkFun data. This buys
    EARLIER when it buys at all -- it does not buy only winners. Expect
    most fired trades to be flat or losing; the moonbag/trim-ladder logic
    this reuses is what's supposed to let a rare real winner run.
  - Requires Ali's machine to be on, connected, and this process actually
    running (`python worker_stonkfun_snipe.py`) -- it does nothing while
    the window is closed or the machine is asleep. Unlike scheduler.py,
    nothing here is triggered by GitHub Actions.
  - Uses the same state.py backend (Upstash if configured, else a local
    JSON file) as scheduler.py -- for circuit-breaker/budget consistency
    with the GitHub Actions side, Upstash credentials should be set in
    BOTH places, not just one, or the two won't see each other's spend.

UPDATE (Sept 23, 2026) -- position management wired in, per Ali's direct
follow-up ("build them and implement in our code"): previously this worker
only ever opened positions -- handle_stage1_candidate would fire and lock in
a moonbag ladder, but nothing ever came back to check on that position
again, so no trim tier and no defensive rug-exit could ever actually fire.
manage_open_stonkfun_positions() below closes that gap: every cycle, it
re-prices every open StonkFun position and calls moonbag.check_and_trim()
and defensive_sell.check_and_defend() on each one -- the exact same exit
logic other layers already have, now actually invoked for these positions.

Also per Ali's direct instruction ("there should be something significant
left for moon bag coins identified... target something big from them"):
convergence_count is no longer hardcoded to 0 on every fire. It's now
_stonkfun_signal_count() -- a real score built only from signals this
worker actually has (sustained multiple past the trigger, real liquidity
depth, a deployer with an actual non-spam track record). A candidate strong
enough on independent StonkFun-native signals now has a genuine shot at
moonbag.py's high_conviction ladder (55% moonbag rides, vs. the 20% default)
instead of defaulting to the smaller moonbag on every single fire -- see
_stonkfun_signal_count()'s own docstring for exactly what it does and does
not claim.

STILL HONESTLY BLOCKED, not silently patched over: swap_executor's buy path
stops before sign+send (see that module's execute_buy_solana -- returns
UNTESTED by design, real money has never moved through it) and never
records amount_tokens on a position. That means even with trims/defends now
wired and firing their *decisions* correctly, execute_sell has no real
token quantity to actually sell yet -- it will correctly refuse (inert by
design) rather than send a bad order, exactly like every other real-money
path in this codebase, but a REAL trim or rug-exit cannot fully execute
until that fill-recording gap is closed. That's a separate, higher-stakes
piece of work (parsing a real on-chain fill) deliberately left for its own
pass with real-network testing, not bundled into this change.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

import state
from config import CONFIG
from executor.config import EXECUTOR_CONFIG
from executor.entrypoint import handle_stage1_candidate
import executor.position_state as position_state
import executor.moonbag as moonbag
import executor.defensive_sell as defensive_sell
from layers.layer0_scoring import RawSignals
from layers.layer0c_stonkfun_scoring import (
    fetch_new_stonkfun_tokens,
    fetch_stonkfun_token_detail,
    parse_stonkfun_token_detail,
    compute_stonkfun_deployer_tier_from_payload,
    fetch_stonkfun_launches_by_creator,
    is_snipe_candidate,
    MOMENTUM_LOOKBACK_HOURS,
)
from utils.http import ApiUnreachable

try:
    from executor.swap_executor import execute_buy_solana
except ImportError:
    execute_buy_solana = None


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ApiUnreachable as e:
        return {"ok": False, "reason": f"network unreachable: {e}"}


def _age_hours(created_at: str) -> float:
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    except (ValueError, TypeError, AttributeError):
        return 9999.0


def _stonkfun_signal_count(detail: dict, deployer_result: dict = None) -> int:
    """How many INDEPENDENT StonkFun-native signals point to this candidate
    being more than a bare 3x trigger flicker. Fed into handle_stage1_
    candidate as convergence_count -- the same field other layers use for
    "how many independent things point at this coin" -- so a strong pass
    here has a real shot at moonbag.assess_conviction's high_conviction
    ladder (55% moonbag rides) instead of the 20% default every fire got
    before this. Deliberately conservative: only counts signals this
    worker can actually verify from StonkFun's own API, nothing guessed.

      +1  current multiple vs. start is already >=5x at detection time --
          sustained past the 3x trigger, not a single-tick flicker at the
          floor.
      +1  liquidity is >=$10,000 -- well past the $2,000 snipe floor, real
          depth rather than a pool that could be drained in one swap.
      +1  deployer has an actual multi-launch track record (>=2 prior
          launches) and was NOT flagged spammer -- a real history that
          didn't trip the spam filter, not "unknown" (a brand-new wallet
          with zero history is NOT rewarded here -- no history is not the
          same as a good history).

    Max score is 3, matching compute_moonshot_score's own convergence_count
    >= 3 -> +2 tier (its highest tier for this field). This can NOT by
    itself push a position to high_conviction (CONVICTION_THRESHOLD=4) --
    it needs deployer_tier or another signal to also contribute, same as
    for every other layer's use of this scoring function. That's
    deliberate: no single StonkFun-only signal set should unlock the wider
    moonbag on its own without at least one signal outside "this worker
    looked at StonkFun's own numbers."""
    count = 0
    start = detail.get("start_mcap_usd")
    current = detail.get("current_mcap_usd")
    if start and start > 0 and current is not None and (current / start) >= 5.0:
        count += 1
    if (detail.get("liquidity_usd") or 0) >= 10_000:
        count += 1
    if deployer_result and deployer_result.get("tier") == "neutral" and deployer_result.get("launch_count", 0) >= 2:
        count += 1
    return count


def manage_open_stonkfun_positions(dry_run: bool = True) -> dict:
    """The other half of what was missing (Ali, Sept 23, 2026 follow-up):
    handle_stage1_candidate can OPEN a position and lock in a moonbag
    ladder, but until this function existed nothing ever came back to
    check on that position again -- no trim tier and no defensive rug-exit
    could ever fire, no matter how the price moved after entry. Call this
    once per cycle (run_one_cycle does, below) to re-price every open
    position this worker is tracking and give both exit paths a real
    chance to fire, using the exact same moonbag.py / defensive_sell.py
    logic other layers already have -- nothing new was invented for the
    exit decision itself, only the loop that actually calls it.

    Only manages positions this worker can re-price via StonkFun's own
    detail endpoint -- if a mint's detail lookup fails (delisted, not a
    StonkFun token, network hiccup) it's skipped for this cycle rather
    than guessed at, same fail-closed posture as the rest of this module.

    Defensive rug-check needs a PREVIOUS liquidity reading to detect a
    *drop*, not just a low absolute number -- state stores the last-seen
    liquidity per mint (layer0c_snipe_prev_liq:<mint>) so this works
    correctly across cycles, including after a restart, rather than
    comparing a cycle's single reading against itself.

    HONEST LIMIT (see module docstring's Sept 23 update): even when a trim
    or rug-exit decision correctly fires here, the actual sell still goes
    through swap_executor.execute_sell, which has no real amount_tokens to
    sell yet (buy fills aren't recorded -- separate, unclosed gap) and
    whose sign+send path is itself still UNTESTED. So today this function
    makes the right DECISION every cycle and logs it; it does not yet
    complete a real on-chain exit. That's the same "decides correctly,
    inert until the execution gap closes" posture as every other exec/
    module in this codebase -- not a shortcut taken here specifically."""
    managed, trims_fired, defends_fired = [], [], []
    for pos in position_state.list_open_positions():
        chain, token = pos.get("chain"), pos.get("token")
        if chain != "solana" or not token:
            continue

        detail_fetch = _safe(fetch_stonkfun_token_detail, token)
        if not detail_fetch.get("ok"):
            continue
        detail_body = detail_fetch["raw"].get("json") if "raw" in detail_fetch else detail_fetch.get("json")
        if detail_body is None:
            continue
        detail = parse_stonkfun_token_detail(detail_body)
        if detail is None:
            continue

        managed.append(token)
        current_mcap = detail.get("current_mcap_usd")
        current_liq = detail.get("liquidity_usd")

        trim_result = moonbag.check_and_trim("solana", token, current_mcap)
        if trim_result is not None:
            trims_fired.append(trim_result)
            print(f"[snipe] MOONBAG TRIM {token}: {trim_result['trim_decision'].reason}")

        prev_liq_key = f"layer0c_snipe_prev_liq:{token}"
        prev_liq = state.get_value(prev_liq_key)
        if prev_liq is not None:
            prev_signals = RawSignals(liquidity_usd=prev_liq)
            curr_signals = RawSignals(liquidity_usd=current_liq)
            defend_result = defensive_sell.check_and_defend("solana", token, prev_signals, curr_signals)
            if defend_result is not None:
                defends_fired.append(defend_result)
                print(f"[snipe] DEFENSIVE EXIT {token}: {defend_result['rug_reasons']}")
        if current_liq is not None:
            state.set_value(prev_liq_key, current_liq)

    return {"managed": managed, "trims_fired": trims_fired, "defends_fired": defends_fired}


def run_one_cycle(limit: int = 25, max_deep_lookups: int = 15, dry_run: bool = True) -> dict:
    """One poll cycle's worth of work, pulled out of the loop below so it
    can be called directly in a test with fetch functions monkeypatched,
    without needing a live network call or a real sleep loop."""
    already_bought = state.layer0c_snipe_bought_mints()
    fired = []

    listing = _safe(fetch_new_stonkfun_tokens, limit)
    if not listing.get("ok"):
        return {"ok": False, "reason": listing.get("reason") or f"status {listing.get('raw', {}).get('status_code')}",
                "fired": []}
    body = listing["raw"].get("json") if "raw" in listing else listing.get("json")
    if body is None:
        return {"ok": False, "reason": "non-JSON response", "fired": []}

    tokens = body.get("data") or body.get("tokens") or []
    candidates = [
        t for t in tokens
        if t.get("mint") and t.get("mint") not in already_bought
        and _age_hours(t.get("createdAt")) <= MOMENTUM_LOOKBACK_HOURS
    ][:max_deep_lookups]

    checked_this_cycle = []
    for t in candidates:
        mint = t["mint"]
        checked_this_cycle.append(mint)

        detail_fetch = _safe(fetch_stonkfun_token_detail, mint)
        if not detail_fetch.get("ok"):
            continue
        detail_body = detail_fetch["raw"].get("json") if "raw" in detail_fetch else detail_fetch.get("json")
        if detail_body is None:
            continue
        detail = parse_stonkfun_token_detail(detail_body)
        if detail is None:
            continue

        deployer_result = None
        creator = detail.get("creator")
        if creator:
            launches_fetch = _safe(fetch_stonkfun_launches_by_creator, creator)
            if launches_fetch.get("ok"):
                launches_body = launches_fetch["raw"].get("json") if "raw" in launches_fetch else launches_fetch.get("json")
                if launches_body is not None:
                    deployer_result = compute_stonkfun_deployer_tier_from_payload(launches_body)

        verdict = is_snipe_candidate(detail, deployer_result)
        if not verdict["candidate"]:
            continue

        decision = handle_stage1_candidate(
            chain="solana", token=mint, score_band="A",  # synthetic: a snipe pass is treated as strong as
                                                           # a structural A-band score for trigger purposes --
                                                           # see is_snipe_candidate's own bar, not re-derived here
            deployer_tier=(deployer_result or {}).get("tier"),
            convergence_count=_stonkfun_signal_count(detail, deployer_result),  # real score, not hardcoded --
                                                           # see _stonkfun_signal_count's docstring; this is what
                                                           # gives a genuinely strong candidate a shot at the
                                                           # wider (55%) moonbag instead of the 20% default
            entry_mcap=detail.get("current_mcap_usd"),
        )
        if not decision["fired"]:
            print(f"[snipe] {mint} passed candidate check but trigger declined: {decision['reason']}")
            continue

        exec_result = None
        if not dry_run and execute_buy_solana is not None:
            exec_result = execute_buy_solana(mint, decision["position_usd"])
            print(f"[snipe] BUY ATTEMPT {mint}: {exec_result}")
        else:
            print(f"[snipe] DRY RUN -- would buy {mint} for ${decision['position_usd']:.2f} "
                  f"({verdict['reasons']})")

        fired.append({"mint": mint, "symbol": detail.get("symbol"), "decision": decision,
                       "exec_result": exec_result, "reasons": verdict["reasons"]})

    state.mark_layer0c_snipe_bought(checked_this_cycle)
    management = manage_open_stonkfun_positions(dry_run=dry_run)
    return {"ok": True, "fired": fired, "checked": checked_this_cycle, "managed": management}


def run_loop(interval_seconds: int, limit: int, max_deep_lookups: int, dry_run: bool):
    print(f"=== StonkFun snipe worker starting ===")
    print(f"interval={interval_seconds}s  dry_run={dry_run}  "
          f"EXECUTION_ENABLED={EXECUTOR_CONFIG.execution_enabled}  "
          f"wallet_configured={bool(EXECUTOR_CONFIG.solana_private_key)}")
    if dry_run:
        print("Running in DRY RUN -- no real orders will be placed. Pass --live to change that "
              "(still requires EXECUTION_ENABLED=true and a wallet key, same as everywhere else).")
    while True:
        cycle_start = time.time()
        try:
            result = run_one_cycle(limit=limit, max_deep_lookups=max_deep_lookups, dry_run=dry_run)
            if result["ok"]:
                mgmt = result.get("managed", {})
                print(f"[snipe] cycle done: {len(result['checked'])} checked, {len(result['fired'])} fired, "
                      f"{len(mgmt.get('managed', []))} open positions re-priced, "
                      f"{len(mgmt.get('trims_fired', []))} trims, {len(mgmt.get('defends_fired', []))} defensive exits")
            else:
                print(f"[snipe] cycle failed: {result.get('reason')}")
        except Exception as e:  # noqa: BLE001 -- a worker that dies on one bad cycle defeats the point
            print(f"[snipe] unexpected error this cycle (continuing): {e}")
        elapsed = time.time() - cycle_start
        sleep_for = max(0.0, interval_seconds - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StonkFun proactive snipe worker (continuous, run on your own machine)")
    parser.add_argument("--interval", type=int, default=8, help="seconds between poll cycles (default 8)")
    parser.add_argument("--limit", type=int, default=25, help="newest-tokens page size per cycle (default 25)")
    parser.add_argument("--max-deep-lookups", type=int, default=15, help="capped per-mint detail calls/cycle (default 15)")
    parser.add_argument("--live", action="store_true", help="place REAL orders (still needs EXECUTION_ENABLED=true "
                                                              "and a funded wallet key -- this flag alone does nothing)")
    args = parser.parse_args()
    run_loop(args.interval, args.limit, args.max_deep_lookups, dry_run=not args.live)

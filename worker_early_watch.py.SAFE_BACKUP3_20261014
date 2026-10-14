"""Early watcher -- the sniper (Ali, Oct 12 2026). Runs on your PC as its own
process (local_runner supervises it). Listens to EVERY new pump.fun launch in
real time (free PumpPortal stream) and decides within seconds, per coin, whether
a book should take a paper entry (layers/layer17_early_entry.py).

Memory-only while it listens (no state writes per event); state is written only
on an entry, a heartbeat (every 30 s) and the stats snapshot the dashboard reads.
PAPER only. Real money stays off.

Usage: python worker_early_watch.py
"""
import asyncio
import json
import os
import time
import traceback

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import state  # noqa: E402
from early_stream import EarlyStream  # noqa: E402
from layers import modules  # noqa: E402
from layers.layer17_early_entry import EarlyTracker  # noqa: E402

WSOL = "So11111111111111111111111111111111111111112"
_cache = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:  # noqa: BLE001
        val = hit[1] if hit else None
    _cache[key] = (time.time(), val)
    return val


def sol_usd() -> float:
    """SOL price in USD, cached 5 min; falls back to SOL_USD env (default 150)."""
    def fetch():
        from utils.http import get_json
        j = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{WSOL}")
        pairs = (j or {}).get("pairs") or []
        best = max(pairs, key=lambda p: float(((p.get("liquidity") or {}).get("usd")) or 0), default=None)
        return float(best["priceUsd"]) if best and best.get("priceUsd") else None
    v = _cached("sol", 300, fetch)
    try:
        return float(v) if v else float(os.environ.get("SOL_USD", "150"))
    except ValueError:
        return 150.0


def load_dev_list() -> set:
    out = set()
    path = os.environ.get("EARLY_DEV_LIST_FILE", "pumpfun_top_devs_oct2026.json")
    try:
        with open(path, encoding="utf-8") as f:
            for w in (json.load(f).get("wallets") or []):
                if w.get("address"):
                    out.add(w["address"])
    except Exception:  # noqa: BLE001
        pass
    return out


def smart_wallets() -> set:
    def fetch():
        from layers.layer2b_pumpfun_smart_money import get_smart_money_roster
        return get_smart_money_roster()
    return _cached("smart", 30, fetch) or set()


def hot_terms() -> set:
    def fetch():
        rec = state.get_value("radar_hot_terms")
        if isinstance(rec, dict) and time.time() - float(rec.get("ts") or 0) < 6 * 3600:
            return {str(t).lower() for t in (rec.get("terms") or [])}
        return set()
    return _cached("hot", 60, fetch) or set()


def make_tracker() -> EarlyTracker:
    return EarlyTracker(dev_list=load_dev_list(), smart_fn=smart_wallets,
                        trusted_fn=state.is_deployer_trusted, blacklist_fn=state.is_deployer_blacklisted,
                        hot_terms_fn=hot_terms)


# every sniper entry uses the early exit engine (paper_ledger._manage_early); the profile is the lane name
_PROFILE = {k: ("early", k) for k in ("core1", "core2", "sprint", "compound", "dev_list", "leaderboard")}
_RECORDER = None


def act(decision: dict, send=None, now=None) -> dict:
    """Paper entries (one per book in decision['lanes']) + one Telegram alert + feed row."""
    from executor import books, paper_ledger
    now = now if now is not None else time.time()
    mint = decision["mint"]
    if state.cache_get(f"early_seen:{mint}", 24 * 3600):
        return {"skipped": "already acted"}
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    d = state.get_value("early_day")
    d = d if isinstance(d, dict) and d.get("day") == day else {"day": day, "n": 0}
    if d["n"] >= int(float(os.environ.get("EARLY_MAX_ENTRIES_PER_DAY", "60"))):
        return {"skipped": "daily cap"}
    state.cache_set(f"early_seen:{mint}", True)
    usd_price = sol_usd()
    mcap_usd = decision["entry_mcap_sol"] * usd_price
    opened, refused = [], []
    if _RECORDER:
        _RECORDER.on_decision(decision, now)
    live_done = []
    # decisions that lean on the free DexScreener confirmations stay PAPER until Ali sets EARLY_FREE_CONFIRM=live
    free_paper_only = bool(decision.get("uses_free")) and os.environ.get("EARLY_FREE_CONFIRM", "paper").strip().lower() != "live"
    for lane in decision.get("lanes") or [decision["lane"]]:
        conf = decision.get("confirmations") or []
        strong = [c for c in conf if c not in ("dev_list", "repeat_grad")]     # dev_list lane: 0/69 graduated (Oct 13)
        tier = "conviction" if len(strong) >= 4 else "standard" if len(strong) >= 2 else "probe"
        ok, why = books.can_open(lane, strong=len(strong) >= 4)
        stake = books.tier_stake_usd(lane, tier, mcap_usd) if ok else 0.0
        if ok and not stake and not free_paper_only:          # NIGHT TUNE 14 Oct: paper book stake can round to 0; a live lane still gets its capped live stake
            try:
                from executor import pumpportal_exec as _px
                if _px.lane_live(lane):
                    stake = float(os.environ.get('LIVE_MAX_STAKE_USD', '0') or 0)
            except Exception:  # noqa: BLE001
                pass
        if not stake:
            refused.append(f"{lane}: {why}")
            continue
        strategy, profile = _PROFILE.get(lane, ("early", lane))
        is_live = False
        try:
            from executor import pumpportal_exec
            l_ok, l_why, l_stake = pumpportal_exec.allowed(lane, stake)
            if free_paper_only:
                pass                      # free-data confirmation not proven yet: paper only
            elif l_ok and l_stake > 0:
                res = pumpportal_exec.buy(mint, l_stake, usd_price)
                if res.get("ok"):
                    is_live, stake = True, l_stake
                    live_done.append(lane)
                else:
                    refused.append(f"{lane}: live buy failed ({str(res.get('resp'))[:80]})")
            elif pumpportal_exec.lane_live(lane):
                refused.append(f"{lane}: live blocked ({l_why})")
        except Exception as e:  # noqa: BLE001
            refused.append(f"{lane}: live error {type(e).__name__}")
        pos = paper_ledger.open_paper(
            "solana", mint, f"early_{lane}", f"early_{lane}", stake, mcap_usd, strategy=strategy, exit_profile=profile,
            live=is_live,
            tags={"origin": "early", "age_s": decision["age_s"], "confirmations": decision["confirmations"],
                  "mcap_sol": decision["entry_mcap_sol"]}, module=lane)
        (opened if pos else refused).append(lane if pos else f"{lane}: not opened")
    d["n"] += 1
    state.set_value("early_day", d)
    primary = decision["lane"]
    label = modules.label(primary)
    text = (f"[{label} · {decision['potential']}] ⚡ *EARLY ENTRY* {decision['symbol'] or mint[:8]}\n`{mint}`\n"
            f"age {decision['age_s']:.0f}s · mcap {decision['entry_mcap_sol']:.1f} SOL (~${mcap_usd:,.0f}) · "
            f"{decision['buyers']} buyers · {decision['buys']} buys/{decision['sells']} sells\n"
            f"Confirmations: {', '.join(decision['confirmations']) or 'none'}\n"
            f"Paper entries: {', '.join(opened) or 'none'}" + (f" | LIVE buys: {', '.join(live_done)}" if live_done else "")
            + (f" (refused: {'; '.join(refused)})" if refused else ""))
    try:
        from links import render_links_line
        ln = render_links_line("solana", mint)
        if ln:
            text += "\n" + ln
    except Exception:  # noqa: BLE001
        pass
    try:
        (send or __import__("telegram_alert").send_telegram_message)(text)
    except Exception:  # noqa: BLE001
        pass
    try:
        state.log_full_alert("early_" + primary, "solana", decision["symbol"] or mint[:8], mint,
                             f"Early entry ({decision['age_s']:.0f}s old, {decision['entry_mcap_sol']:.0f} SOL)",
                             {"Chain": "solana", "Module": primary, "Confirmations": ", ".join(decision["confirmations"]),
                              "Paper": ", ".join(opened) or "none", "Dev holding": ""})
    except Exception:  # noqa: BLE001
        pass
    return {"opened": opened, "refused": refused}


async def main_async():
    tracker = make_tracker()
    loop = asyncio.get_running_loop()
    counters = {"events": 0, "entries": 0}
    global _RECORDER
    from early_recorder import Recorder
    _RECORDER = recorder = Recorder()
    from early_creators import Creators
    creators = Creators()
    tracker.creator_fn = creators.repeat_grad
    tracker.launches_fn = lambda c: creators.snapshot(c).get("creator_launches", 0)

    def on_event(e):
        counters["events"] += 1
        kind = (e.get("txType") or "").lower()
        if kind == "create":
            who = e.get("traderPublicKey")
            if who and e.get("mint"):
                creators.on_create(who, e["mint"])
            tracked = tracker.on_create(e)
            recorder.on_create(e, tracked, (who in tracker.dev_list),
                               extra={**creators.snapshot(who or ""), "repeat_grad": creators.repeat_grad(who or "")})
            if not tracked:
                return None
            tracker.feed_off = stream.trades_blocked
            dec = tracker.evaluate(e["mint"])        # dev-list launches enter at creation, no trade feed needed
            if dec:
                counters["entries"] += 1
                loop.run_in_executor(None, act, dec)
            if stream.trade_url and not (who in tracker.dev_list or creators.repeat_grad(who or "")):
                return None          # metered trade feed: watch only the lanes with a proven signal (repeat graduate / dev list)
            return [e["mint"]]
        if kind == "migrate" or (e.get("pool") and kind not in ("buy", "sell", "create")):
            counters["grads"] = counters.get("grads", 0) + 1
            if e.get("mint"):
                creators.on_graduate(e["mint"])
                recorder.on_grad(e["mint"])
            return None
        if kind in ("buy", "sell"):
            dec = tracker.on_trade(e)
            if dec:
                counters["entries"] += 1
                loop.run_in_executor(None, act, dec)
        return None

    stream = EarlyStream(on_event)

    async def housekeeping():
        while True:
            await asyncio.sleep(30)
            try:
                for r in [r for r in tracker.recs.values() if time.time() - r.created > 1800]:
                    recorder.on_final(r)
                creators.save()
                gone = tracker.sweep()
                if gone:
                    await stream.unsubscribe_trades(gone)
                snap = dict(tracker.stats, tracked_now=len(tracker.recs), events=counters["events"],
                            entries=counters["entries"], reconnects=stream.reconnects, error=stream.last_error, trades_blocked=stream.trades_blocked, trade_msgs=stream.trade_msgs, spend_est_sol=stream.spend_est_sol, budget_exhausted=stream.budget_exhausted, grads=counters.get("grads", 0), creators_known=len(creators.t),
                            last_event_ago=round(time.time() - stream.last_event_ts, 1) if stream.last_event_ts else None,
                            ts=time.time())
                state.set_value("early_stats", snap)
                note = (f"events={snap['events']} tracked={snap['tracked_now']} entries={snap['entries']} "
                        f"reconnects={snap['reconnects']}" + (f" err={snap['error']}" if snap["error"] else "")
                        + (" TRADE-FEED-OFF(need PUMPPORTAL_API_KEY)" if stream.trades_blocked else "")
                        + (f" trade_msgs={stream.trade_msgs} est_spend={stream.spend_est_sol}SOL" if stream.trade_url else "")
                        + (" BUDGET-REACHED" if stream.budget_exhausted else "")
                        + f" free_polls={counters.get('free_polls', 0)} free_entries={counters.get('free_entries', 0)}"
                        + f" insane={tracker.stats.get('insane_price', 0)}")
                state.record_runner_heartbeat("early-watch", "local-pc", note)
                print(f"[early-watch] {note}", flush=True)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    async def manager():
        """Sniper position manager: re-prices open early positions every few seconds (stream price when the
        coin is tracked and recently traded, DexScreener otherwise) and applies the early exit engine."""
        from executor import paper_ledger
        from layers.layer0_scoring import fetch_dexscreener_snapshot
        while True:
            await asyncio.sleep(float(os.environ.get("EARLY_MANAGE_SECONDS", "6")))
            try:
                if not any(str(p.get("source", "")).startswith("early_") for p in paper_ledger._open().values()):
                    continue
                px = sol_usd()

                def snap(chain, token):
                    r = tracker.recs.get(token)
                    if r and r.mcap_sol and time.time() - r.last_ts < 20:
                        return {"mcap_usd": r.mcap_sol * px, "liquidity_usd": None}
                    return fetch_dexscreener_snapshot(chain, token)
                await loop.run_in_executor(None, lambda: paper_ledger.manage(snap, source_prefix="early_"))
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    async def sampler():
        from layers.layer14_revival import fetch_dexscreener_batch
        while True:
            await asyncio.sleep(30)
            try:
                n = await loop.run_in_executor(None, lambda: recorder.sample(fetch_dexscreener_batch))
                counters["outcomes"] = counters.get("outcomes", 0) + n
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    async def free_confirmer():
        """Free confirmations (14 Oct): the paid trade feed only watches dev coins, so every other coin had zero buy data
        and sprint/core could never fire. Every few seconds, ask DexScreener (free, batched) for the 5-minute buy/sell
        counts and volume of young tracked coins and feed them to the tracker. Entries made this way are PAPER only."""
        from layers.layer14_revival import fetch_dexscreener_batch
        if os.environ.get("EARLY_FREE_CONFIRM", "paper").strip().lower() == "off":
            return
        poll = float(os.environ.get("EARLY_FREE_POLL_S", "12"))
        amin = float(os.environ.get("EARLY_FREE_MIN_AGE_S", "30"))
        amax = float(os.environ.get("EARLY_FREE_MAX_AGE_S", "600"))
        cap = int(float(os.environ.get("EARLY_FREE_MAX_PER_CYCLE", "150")))
        while True:
            await asyncio.sleep(poll)
            try:
                now = time.time()
                cands = sorted((r for r in tracker.recs.values() if not r.decided and amin <= now - r.created <= amax),
                               key=lambda r: r.created, reverse=True)[:cap]
                if not cands:
                    continue
                pairs = await loop.run_in_executor(None, lambda: fetch_dexscreener_batch("solana", [r.mint for r in cands]))
                px = sol_usd()
                counters["free_polls"] = counters.get("free_polls", 0) + 1
                for r in cands:
                    pair = pairs.get(r.mint)
                    if not pair:
                        continue
                    m5 = (pair.get("txns") or {}).get("m5") or {}
                    vol = float(((pair.get("volume") or {}).get("m5")) or 0)
                    mc_usd = float(pair.get("marketCap") or pair.get("fdv") or 0)
                    dec = tracker.on_free(r.mint, int(m5.get("buys") or 0), int(m5.get("sells") or 0), vol,
                                          (mc_usd / px) if (mc_usd and px) else None)
                    if dec:
                        counters["entries"] += 1
                        counters["free_entries"] = counters.get("free_entries", 0) + 1
                        loop.run_in_executor(None, act, dec)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    health = {"alerted": 0.0}

    async def watchdog():
        """Telegram warning when the launch feed goes quiet or the trade feed is blocked."""
        while True:
            await asyncio.sleep(60)
            try:
                quiet = stream.last_event_ts and time.time() - stream.last_event_ts > 120
                if (quiet or stream.trades_blocked) and time.time() - health["alerted"] > 1800:
                    health["alerted"] = time.time()
                    msg = ("⚠️ early-watch: launch feed quiet for >2 min" if quiet else
                           "⚠️ early-watch: trade feed OFF (set PUMPPORTAL_API_KEY) -- only dev-list launches can enter")
                    await loop.run_in_executor(None, __import__("telegram_alert").send_telegram_message, msg)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    tasks = [asyncio.create_task(manager()), asyncio.create_task(sampler()), asyncio.create_task(watchdog()),
             asyncio.create_task(free_confirmer())]
    hk = asyncio.create_task(housekeeping())
    try:
        await stream.run_forever()
    finally:
        hk.cancel()
        for t in tasks:
            t.cancel()


def main():
    print("[early-watch] started (PumpPortal stream, paper entries only)", flush=True)
    while True:
        try:
            asyncio.run(main_async())
        except KeyboardInterrupt:
            return
        except Exception:  # the watcher must never die
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()

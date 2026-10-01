"""
Alert replay -- the win rate of OUR OWN live alerts (Sept 30 2026).

Ali: "if you want to do any paper test, do it right now from live APIs --
I want to fix every flaw tonight." Waiting 24 h for the paper ledger is too
slow, and backtest_trades.py's 24 hand-picked coins aren't what the scanner
actually sees. This replays every coin the live system ALERTED on (the
dashboard alert feed in Upstash: up to 24 h, all chains, all bands):

  1. token -> its main pool on GeckoTerminal (free, keyless, covers
     Solana / BSC / Base / Robinhood Chain);
  2. real 5-minute candles from GeckoTerminal's OHLCV endpoint;
  3. BUY at the first candle after the alert time (+ENTRY_DELAY_MIN, the
     real reaction lag), with the paper ledger's cost estimate;
  4. run the SAME exit logic live money uses -- stage (exit_rules +
     moonbag ladder) and compound scalper (scalp_exit_decision) -- via
     backtest_trades.sim_stage / sim_scalper;
  5. win rate, average result and P&L by band, chain, and the live
     Auto-buy verdict (would the real gate have bought it?).

Honest limits (printed): trades still open at the end of available data
are marked to the last candle; coins whose pool GeckoTerminal can't find
are skipped and counted; young coins may have only a few hours of data.
Read-only; needs the Upstash secrets to read the feed. No MadeOnSol, no
Birdeye.
"""
import argparse
import time
from collections import defaultdict

import state
from config import CONFIG
from utils.http import get_json
from backtest_trades import sim_stage, sim_scalper, POSITION_USD
from executor.compound_scalper import estimate_round_trip_cost_pct

GT_NET = {"solana": "solana", "bsc": "bsc", "base": "base", "robinhood_chain": "robinhood"}
GT_PAUSE_SECONDS = 5.0          # run 2 hit 429 on ~every other call at 2.2 s
ENTRY_DELAY_MIN = 2


def _band(item):
    tag = str((item.get("tags") or {}).get("Score", ""))
    if "band " in tag:
        b = tag.split("band ", 1)[1][:1].upper()
        return b if b in "ABCD" else None
    return None


_GT_ERRORS = defaultdict(int)


def _gt_get(url, params):
    """GeckoTerminal GET with pacing and a long back-off on 429 (the first
    replay run lost 91 of 120 coins to rate-limit replies read as 'no pool')."""
    r = {"ok": False}
    for attempt in range(4):
        try:
            r = get_json(url, params=params)
        except Exception as e:                       # network-level failure
            _GT_ERRORS[type(e).__name__] += 1
            time.sleep(GT_PAUSE_SECONDS)
            continue
        time.sleep(GT_PAUSE_SECONDS)
        if r.get("ok"):
            return r
        status = r.get("status_code") or r.get("status")
        _GT_ERRORS[str(status)] += 1
        if str(status) != "429":
            return r
        time.sleep(20 * (attempt + 1))
    return r


def top_pools_dexscreener(chain, tokens):
    """{token: (pool_address, liquidity_usd)} from ONE DexScreener batch call
    per 30 tokens -- instead of one GeckoTerminal call per coin."""
    from layers.layer14_revival import fetch_dexscreener_batch
    out = {}
    try:
        found = fetch_dexscreener_batch(chain, tokens)
    except Exception as e:
        print(f"DexScreener batch failed for {chain}: {e}")
        found = {}
    for tok, pair in found.items():
        if pair.get("pairAddress"):
            out[tok] = (pair["pairAddress"], float((pair.get("liquidity") or {}).get("usd") or 0))
    return out


def top_pool(chain, token):
    net = GT_NET.get(chain)
    if not net:
        return None, None
    r = _gt_get(f"{CONFIG.geckoterminal_base_url}/networks/{net}/tokens/{token}/pools", {"page": 1})
    data = (r.get("json") or {}).get("data") if r.get("ok") else None
    if not data:
        return None, None
    best = max(data, key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
    return best.get("attributes", {}).get("address"), float(best["attributes"].get("reserve_in_usd") or 0)


def candles_since(chain, pool, since_ts):
    net = GT_NET[chain]
    r = _gt_get(f"{CONFIG.geckoterminal_base_url}/networks/{net}/pools/{pool}/ohlcv/minute",
                {"aggregate": 5, "limit": 1000, "currency": "usd"})
    rows = (((r.get("json") or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") if r.get("ok") else None
    if not rows:
        return []
    out = [{"unixTime": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)}
           for t, o, h, l, c, _v in rows if o and h and l and c]
    out.sort(key=lambda x: x["unixTime"])
    return [c for c in out if c["unixTime"] >= since_ts - 300]


def stats(rows):
    if not rows:
        return "-"
    wins = sum(1 for r in rows if r["pnl"] > 0)
    avg = sum(r["pnl"] for r in rows) / len(rows) / POSITION_USD * 100
    tot = sum(r["pnl"] for r in rows)
    return f"{wins:>3}/{len(rows):<3} {wins / len(rows) * 100:>4.0f}%  avg {avg:+6.1f}%  P&L ${tot:+8.1f}"


def report(results, skipped):
    print("\n" + "=" * 90)
    for strat in ("stage", "scalper"):
        rs = [r for r in results if r["strat"] == strat]
        print(f"{strat.upper():8s} ALL            {stats(rs)}")
        for b in "ABCD":
            print(f"         band {b}         {stats([r for r in rs if r['band'] == b])}")
        for ch in GT_NET:
            sub = [r for r in rs if r["chain"] == ch]
            if sub:
                print(f"         {ch:15s}{stats(sub)}")
        for g in ("WOULD BUY", "refused", "no verdict"):
            sub = [r for r in rs if r["gate"] == g]
            if sub:
                print(f"         gate: {g:9s}{stats(sub)}")
        exits = defaultdict(int)
        for r in rs:
            exits[r["how"]] += 1
        print(f"         exits: {dict(exits)}")
        print("-" * 90)
    open_end = sum(1 for r in results if r["how"].startswith("still_open"))
    print(f"GeckoTerminal non-OK replies by status: {dict(_GT_ERRORS)}")
    print(f"skipped: {dict(skipped)}; trades still open at data end (marked to last candle): {open_end}")
    print("Limits: last-24h live alerts only; open trades marked to market; entry = alert time + "
          f"{ENTRY_DELAY_MIN} min.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-coins", type=int, default=120)
    ap.add_argument("--source", choices=["alerts", "fomo"], default="alerts",
                    help="alerts = the system's own alerts; fomo = every Fomo trader's buy (copy-trade test)")
    ap.add_argument("--min-age-hours", type=float, default=4.0,
                    help="only alerts at least this old, so trades have had time to play out")
    ap.add_argument("--budget-minutes", type=float, default=18.0,
                    help="stop replaying and print the report after this long")
    args = ap.parse_args()

    if args.source == "fomo":
        # Copy-trade test: every Fomo trader's buy, scored as if we copied it.
        feed = [dict(b, tags={"Score": "0/100 (band B)"}) for b in state.get_fomo_buy_archive()]
    else:
        feed = state.get_alert_feed(limit=300) + state.get_replay_archive()
    min_ts = time.time() - args.min_age_hours * 3600
    seen, alerts = set(), []
    for it in sorted(feed, key=lambda x: x.get("ts", 0)):          # first alert per coin
        tok, chain = it.get("token_address"), it.get("chain")
        if not tok or chain not in GT_NET or (tok, it.get("trader")) in seen or _band(it) is None or it.get("ts", 0) > min_ts:
            continue
        seen.add((tok, it.get("trader")))
        alerts.append(it)
    # Band B first (the real-money band), newest first, then band C.
    alerts = sorted(alerts, key=lambda a: (_band(a) != "B", -a.get("ts", 0)))[:args.max_coins]
    verdicts = state.get_autobuy_verdicts([a["token_address"] for a in alerts])
    band_counts = {b: sum(1 for a in alerts if _band(a) == b) for b in "ABCD"}
    print(f"Alert replay: {len(alerts)} unique alerted coins from the live feed (bands {band_counts})\n")

    pools = {}
    for ch in GT_NET:
        toks = [a["token_address"] for a in alerts if a["chain"] == ch]
        if toks:
            pools.update(top_pools_dexscreener(ch, toks))
    print(f"DexScreener found pools for {len(pools)}/{len(alerts)} coins\n")

    results, skipped = [], defaultdict(int)
    samples = []
    deadline = time.time() + args.budget_minutes * 60
    for n, a in enumerate(alerts):
        if time.time() > deadline:
            skipped["time budget reached (not replayed)"] += len(alerts) - n
            break
        chain, tok, band = a["chain"], a["token_address"], _band(a)
        pool, liq = pools.get(tok) or top_pool(chain, tok)
        if not pool:
            skipped["no pool (DexScreener + GeckoTerminal)"] += 1
            print(f"  {chain:15s} {tok[:10]} skipped: no pool")
            continue
        entry_ts = int(a["ts"]) + ENTRY_DELAY_MIN * 60
        cs = candles_since(chain, pool, entry_ts)
        idx = next((i for i, c in enumerate(cs) if c["unixTime"] >= entry_ts), None)
        if idx is None or len(cs) - idx < 3:
            skipped["not enough candles after alert"] += 1
            print(f"  {chain:15s} {tok[:10]} skipped: {len(cs)} candles since alert "
                  f"({(time.time() - a['ts']) / 60:.0f} min ago)")
            continue
        cost = estimate_round_trip_cost_pct(chain, POSITION_USD, liq)
        samples.append({"cs": cs, "idx": idx, "cost": cost, "chain": chain, "ts": a["ts"], "trader": a.get("trader")})
        v = verdicts.get(tok) or {}
        gate = "WOULD BUY" if v.get("fired") else ("refused" if v else "no verdict")
        for strat, fn in (("stage", sim_stage), ("scalper", sim_scalper)):
            pnl, how = fn(cs, 300, idx, cost)
            results.append({"strat": strat, "chain": chain, "band": band, "gate": gate, "pnl": pnl, "how": how,
                            "hours": (cs[-1]["unixTime"] - cs[idx]["unixTime"]) / 3600})
        st = [r for r in results if r["strat"] == "stage"][-1]
        sc = [r for r in results if r["strat"] == "scalper"][-1]
        print(f"  {chain:15s} {tok[:10]} band {band} {gate:10s} stage {st['pnl'] / POSITION_USD * 100:+6.0f}% "
              f"{st['how']:22s} scalper {sc['pnl'] / POSITION_USD * 100:+6.0f}% {sc['how']}")

    report(results, skipped)
    if samples:
        import rule_search
        ranked = rule_search.run(samples)
        if args.source == "fomo" and ranked:
            rule_search.by_trader(samples, ranked[0]["rule"])

if __name__ == "__main__":
    main()

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
GT_PAUSE_SECONDS = 2.2          # GeckoTerminal free tier: 30 calls/min
ENTRY_DELAY_MIN = 2


def _band(item):
    tag = str((item.get("tags") or {}).get("Score", ""))
    if "band " in tag:
        b = tag.split("band ", 1)[1][:1].upper()
        return b if b in "ABCD" else None
    return None


def top_pool(chain, token):
    net = GT_NET.get(chain)
    if not net:
        return None, None
    r = get_json(f"{CONFIG.geckoterminal_base_url}/networks/{net}/tokens/{token}/pools", params={"page": 1})
    time.sleep(GT_PAUSE_SECONDS)
    data = (r.get("json") or {}).get("data") if r.get("ok") else None
    if not data:
        return None, None
    best = max(data, key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
    return best.get("attributes", {}).get("address"), float(best["attributes"].get("reserve_in_usd") or 0)


def candles_since(chain, pool, since_ts):
    net = GT_NET[chain]
    r = get_json(f"{CONFIG.geckoterminal_base_url}/networks/{net}/pools/{pool}/ohlcv/minute",
                 params={"aggregate": 5, "limit": 1000, "currency": "usd"})
    time.sleep(GT_PAUSE_SECONDS)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-coins", type=int, default=120)
    args = ap.parse_args()

    feed = state.get_alert_feed(limit=300)
    seen, alerts = set(), []
    for it in sorted(feed, key=lambda x: x.get("ts", 0)):          # first alert per coin
        tok, chain = it.get("token_address"), it.get("chain")
        if not tok or chain not in GT_NET or tok in seen or _band(it) is None:
            continue
        seen.add(tok)
        alerts.append(it)
    alerts = alerts[-args.max_coins:]
    verdicts = state.get_autobuy_verdicts([a["token_address"] for a in alerts])
    band_counts = {b: sum(1 for a in alerts if _band(a) == b) for b in "ABCD"}
    print(f"Alert replay: {len(alerts)} unique alerted coins from the live feed (bands {band_counts})\n")

    results, skipped = [], defaultdict(int)
    for a in alerts:
        chain, tok, band = a["chain"], a["token_address"], _band(a)
        pool, liq = top_pool(chain, tok)
        if not pool:
            skipped["no pool on GeckoTerminal"] += 1
            continue
        entry_ts = int(a["ts"]) + ENTRY_DELAY_MIN * 60
        cs = candles_since(chain, pool, entry_ts)
        idx = next((i for i, c in enumerate(cs) if c["unixTime"] >= entry_ts), None)
        if idx is None or len(cs) - idx < 3:
            skipped["not enough candles after alert"] += 1
            continue
        cost = estimate_round_trip_cost_pct(chain, POSITION_USD, liq)
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
    print(f"skipped: {dict(skipped)}; trades still open at data end (marked to last candle): {open_end}")
    print("Limits: last-24h live alerts only; open trades marked to market; entry = alert time + "
          f"{ENTRY_DELAY_MIN} min.")


if __name__ == "__main__":
    main()

"""Backtest the SNIPER EXIT ENGINE on real recent Solana pump.fun-graduated coins (GeckoTerminal, free).

Run on Ali's PC:  python backtest_sniper.py            (new pools, last hours)
                  python backtest_sniper.py --source top --pages 5   (top-volume pumpswap pools, ~days)
                  python backtest_sniper.py --delay 5   (enter N minutes after first candle)

For every coin it replays 1-minute candles through the SAME exit rules the live engine uses
(executor.paper_ledger.EARLY_RULES: take-profit ladder, hard stop, trailing stop, dead-coin
time stop, 24h max hold) for each lane profile, with the 10% round-trip paper cost, and prints
peak multiple, where the coin stands NOW, and what each lane would have made.

HONEST LIMITS (printed again at the end):
 - Public candle data exists only AFTER graduation (a DEX pool). Pre-graduation prices
   (the true pre-early entry) are NOT public; those are collected forward by the recorder.
 - Wallet-based signals (dev list, smart wallets, repeat graduates) can't be replayed from candles.
 - 'top' source has survivorship bias (high-volume coins); 'new' is closer to unbiased but short.
"""
import argparse, json, os, sys, time, urllib.request
from collections import defaultdict

GT = "https://api.geckoterminal.com/api/v2"
COST = float(os.environ.get("EARLY_PAPER_COST", "0.10"))
STAKE = 30.0


def _get(path):
    req = urllib.request.Request(GT + path, headers={"Accept": "application/json;version=20230302",
                                                      "User-Agent": "s1c-backtest"})
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:
            if "429" in str(e):
                time.sleep(15 * (i + 1))
            elif i == 3:
                return None
            else:
                time.sleep(2)
    return None


def discover(source, pages):
    out = []
    for p in range(1, pages + 1):
        path = (f"/networks/solana/new_pools?page={p}" if source == "new"
                else f"/networks/solana/dexes/pumpswap/pools?page={p}&sort=h24_volume_usd_desc")
        d = _get(path)
        time.sleep(2.2)
        for x in (d or {}).get("data", []):
            a = x["attributes"]
            dex = x["relationships"]["dex"]["data"]["id"]
            if "pump" not in dex:
                continue
            out.append({"pool": a["address"], "name": a["name"], "fdv": float(a.get("fdv_usd") or 0),
                        "created": a.get("pool_created_at")})
    return out


def candles(pool):
    d = _get(f"/networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&limit=1000&currency=usd")
    time.sleep(2.2)
    rows = ((d or {}).get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or []
    rows = sorted(rows, key=lambda r: r[0])
    return [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows if r[1] and r[3]]


def simulate(cs, entry_idx, profile, rules=None):
    """Return (pnl_pct_on_stake, exit_type). Intra-candle order is pessimistic (low before high)."""
    from executor.paper_ledger import EARLY_RULES
    ladder, stop, trail = (rules or EARLY_RULES)[profile]
    entry = cs[entry_idx]["o"]
    t0 = cs[entry_idx]["t"]
    remaining, proceeds, peak, fired = 1.0, 0.0, 1.0, set()
    dead_min = float(os.environ.get("EARLY_DEAD_MIN", "20"))
    max_min = float(os.environ.get("EARLY_MAX_HOLD_MIN", "1440"))

    def sell(frac, mult):
        nonlocal remaining, proceeds
        frac = min(frac, remaining)
        proceeds += frac * mult * (1 - COST)
        remaining -= frac

    why = "open_at_end"
    for c in cs[entry_idx:]:
        age = (c["t"] - t0) / 60.0
        for price in (c["l"], c["h"], c["c"]):
            mult = price / entry
            peak = max(peak, mult)
            for tier, frac in sorted(ladder.items()):
                if tier not in fired and mult >= tier and remaining > 1e-9:
                    sell(frac, tier); fired.add(tier)
            if remaining <= 1e-9:
                return (proceeds - 1.0) * 100, "fully_sold"
            r = None
            if peak < 2.0 and mult <= stop:
                r, m = "stop_loss", stop
            elif peak >= 2.0 and mult <= peak * trail:
                r, m = "trailing_stop", peak * trail
            elif age >= dead_min and peak < 1.5 and mult < 1.2:
                r, m = "time_stop", mult
            elif age >= max_min:
                r, m = "max_hold", mult
            if r:
                sell(remaining, min(m, mult) if r != "trailing_stop" else m)
                return (proceeds - 1.0) * 100, r
    sell(remaining, cs[-1]["c"] / entry)
    return (proceeds - 1.0) * 100, why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="new", choices=["new", "top"])
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--delay", type=int, default=0, help="minutes after first candle to enter")
    ap.add_argument("--max", type=int, default=120)
    a = ap.parse_args()
    from executor.paper_ledger import EARLY_RULES
    pools = discover(a.source, a.pages)[: a.max]
    print(f"{len(pools)} pump.fun-graduated pools found ({a.source}). Replaying exits...\n")
    tot = defaultdict(list)
    rows = 0
    for p in pools:
        cs = candles(p["pool"])
        if len(cs) < 10 + a.delay:
            continue
        i = a.delay
        entry = cs[i]["o"]
        peak = max(c["h"] for c in cs[i:]) / entry
        now = cs[-1]["c"] / entry
        res = {}
        for prof in ("sprint", "core1", "core2", "compound", "leaderboard"):
            pnl, how = simulate(cs, i, prof)
            res[prof] = (pnl, how)
            tot[prof].append(pnl)
        rows += 1
        print(f"{p['name'][:22]:22s} {len(cs):4d}m peak {peak:6.1f}x now {now:6.2f}x | " +
              " ".join(f"{k[:5]} {v[0]:+6.0f}%" for k, v in res.items()))
    print("\n" + "=" * 70)
    for prof, v in tot.items():
        if not v:
            continue
        w = sum(1 for x in v if x > 0)
        print(f"{prof:12s} coins {len(v):3d}  wins {w}/{len(v)} ({100*w/len(v):.0f}%)  "
              f"avg {sum(v)/len(v):+6.1f}%  best {max(v):+7.0f}%  worst {min(v):+5.0f}%  "
              f"P&L ${sum(STAKE*x/100 for x in v):+.0f} on ${STAKE:.0f}/coin")
    print("=" * 70)
    print(__doc__.split("HONEST LIMITS")[1])


if __name__ == "__main__":
    main()

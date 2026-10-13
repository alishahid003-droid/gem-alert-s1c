"""END-TO-END REPLAY TEST: real entry gate + real exits + tiers + slots on an UNBIASED sample of new coins.

  python e2e_test.py --collect 120      fetch up to 120 not-yet-cached new pools (all 4 chains), 1 API call each
  python e2e_test.py                    replay everything cached and print the verdict (no API calls)
  python e2e_test.py --chains solana    only one chain

Sample = every NEW pool GeckoTerminal lists (winners AND duds), not a winners list. Run --collect a few times a
day: the sample grows by itself and the verdict gets tighter. Cache: logs/e2e_cache.json (separate from the
winners cache, so winners never leak in).

What it measures: for each coin the entry gate (replay_coin.replay, proxy mode) would have taken, the stake tier,
exits (executor.paper_ledger.EARLY_RULES, 10% round-trip cost, low-before-high), 2%-of-mcap stake cap, chain
multipliers. Output: net result per entry with a bootstrap confidence interval, per-chain/per-tier split, how much
rests on the single best coin, and $100 compounding projections per book. VERDICT = GO only if the lower
confidence bound is positive AND the edge survives removing the best coin.
HONEST LIMITS: only coins that got a DEX pool (post-graduation on pump.fun); candle fills are optimistic;
buyers/pressure signals are not available in candles, so tiers are capped at Standard here.
"""
import argparse, json, os, random, sys, time

import replay_coin as rc

CACHE = os.path.join("logs", "e2e_cache.json")
CHAINS = ["solana", "bsc", "base", "robinhood"]
STAKE_MULT = {"solana": 1.0, "bsc": 0.3, "base": 1.0, "robinhood": 0.6}
TIER_PCT = {"probe": 0.15, "standard": 0.40, "conviction": 0.60}
BOOKS = {"sprint": 1.0, "core1": 0.8, "core2": 0.6}       # stake scale per book
RULE_LANE = {"sprint": "sprint", "core1": "core1", "core2": "core2"}


def _load():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(c):
    os.makedirs("logs", exist_ok=True)
    json.dump(c, open(CACHE, "w", encoding="utf-8"))


def collect(chains, limit, pages=3):
    cache = _load()
    done = 0
    for chain in chains:
        todo = []
        for p in range(1, pages + 1):
            d = rc._get(f"/networks/{chain}/new_pools", {"page": p})
            time.sleep(rc.PAUSE)
            for x in (d or {}).get("data", []):
                a = x["attributes"]
                pool = a["address"]
                key = f"{chain}:{pool}"
                try:
                    price, fdv = float(a.get("base_token_price_usd")), float(a.get("fdv_usd"))
                except (TypeError, ValueError):
                    continue
                if key in cache or price <= 0 or fdv <= 0:
                    continue
                todo.append((key, pool, a, price, fdv))
        print(f"{chain}: {len(todo)} new pools to fetch")
        for key, pool, a, price, fdv in todo:
            if done >= limit:
                break
            created = rc._iso(a.get("pool_created_at")) or 0
            d = rc._get(f"/networks/{chain}/pools/{pool}/ohlcv/minute",
                        {"aggregate": 1, "limit": 1000, "before_timestamp": int(created + 1000 * 60), "currency": "usd"})
            time.sleep(rc.PAUSE)
            cl = sorted(((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or [], key=lambda c: c[0])
            cache[key] = {"chain": chain, "created_ts": created, "supply": round(fdv / price),
                          "symbol": (a.get("name") or pool[:6]).split(" / ")[0], "candles": cl}
            done += 1
            if done % 10 == 0:
                _save(cache); print(f"  fetched {done}")
    _save(cache)
    print(f"collected {done} this run | cache now {len(cache)} pools")


def entries(cache, chains):
    """One entry per pool the proxy gate fires on -> dict(time, chain, mcap, conf, rules inputs, multiples per book)."""
    out = []
    for key, v in cache.items():
        if v["chain"] not in chains or len(v.get("candles", [])) < 10:
            continue
        meta = {"supply": v["supply"], "created_ts": v["created_ts"], "symbol": v["symbol"]}
        r = rc._build(meta, v["candles"], v["chain"])
        f = (r.get("fired") or {}).get("proxy") if r.get("ok") else None
        if not f:
            continue
        cd = v["candles"]
        i = min(range(len(cd)), key=lambda k: abs(cd[k][4] * v["supply"] - f["mcap"]))
        conf = [c for c in f["conf"] if c not in ("buyers", "pressure")]   # no fake buyer/pressure confirmations
        tier = "standard" if len(conf) >= 4 else "probe"
        mult = {b: rc.simulate_exit(cd, v["supply"], i, RULE_LANE[b], dead_min=90)[0] for b in BOOKS}
        out.append({"sym": v["symbol"], "chain": v["chain"], "t": cd[i][0], "mc": f["mcap"], "tier": tier,
                    "peak": f["peak_mult"], "mult": mult})
    out.sort(key=lambda e: e["t"])
    return out


def net_per_entry(ent, book):
    """Stake-weighted return of one entry in a book = (multiple-1) * tier share; expectation per $ of equity."""
    return [(e["mult"][book] - 1.0) for e in ent]


def bootstrap(xs, n=2000, seed=7):
    rnd = random.Random(seed)
    if not xs:
        return (0, 0, 0)
    means = sorted(sum(rnd.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return (means[int(n * 0.05)], sum(xs) / len(xs), means[int(n * 0.95)])


def compound(ent, book, seed_usd=100.0, extra_cash_cap=True):
    """Chronological $ book: tier stake x book scale x chain mult, <=80%, <=2% of mcap, slots 3/4/5 by equity (+2 strong)."""
    eq, openp, skipped, taken = seed_usd, [], 0, 0
    for e in ent:
        for p in [p for p in openp if p["tx"] <= e["t"]]:
            eq += p["stake"] * (p["m"] - 1); openp.remove(p)
        if eq < 5:
            break
        committed = sum(p["stake"] for p in openp)
        limit = 3 if eq < 300 else 4 if eq < 3000 else 5 if eq < 30000 else 6
        if len(openp) >= limit:
            skipped += 1; continue
        st = min(eq * TIER_PCT[e["tier"]] * BOOKS[book] * STAKE_MULT[e["chain"]], eq * 0.8, eq - committed,
                 0.02 * e["mc"])
        if st < 5:
            skipped += 1; continue
        m = e["mult"][book]
        openp.append({"stake": st, "m": m, "tx": e["t"] + 6 * 3600}); taken += 1   # hold-time proxy: realised by +6h
    for p in openp:
        eq += p["stake"] * (p["m"] - 1)
    return eq, taken, skipped


def report(ent, span_h):
    print(f"\nSAMPLE: {len(ent)} entries the gate would have taken, over {span_h:.0f} h of new pools")
    if len(ent) < 30:
        print("TOO FEW ENTRIES for a verdict (need >= 30). Run:  python e2e_test.py --collect 150  and repeat.")
    for book in BOOKS:
        xs = net_per_entry(ent, book)
        lo, mean, hi = bootstrap(xs)
        wins = sum(1 for x in xs if x > 0)
        best = max(xs) if xs else 0
        xs2 = sorted(xs)[:-1]
        lo2, mean2, _ = bootstrap(xs2)
        print(f"\n[{book}] per $1 staked: mean {mean:+.1%} (90% range {lo:+.1%} .. {hi:+.1%}) | winners {wins}/{len(xs)}"
              f" | best coin x{best + 1:.1f} | WITHOUT best coin: mean {mean2:+.1%} (low {lo2:+.1%})")
        for ch in CHAINS:
            sub = [e for e in ent if e["chain"] == ch]
            if sub:
                m = sum(e["mult"][book] - 1 for e in sub) / len(sub)
                print(f"    {ch:10s} n={len(sub):3d} mean {m:+.1%}")
        for tier in ("probe", "standard"):
            sub = [e for e in ent if e["tier"] == tier]
            if sub:
                print(f"    tier {tier:9s} n={len(sub):3d} mean {sum(e['mult'][book]-1 for e in sub)/len(sub):+.1%}")
        eq, taken, sk = compound(ent, book)
        print(f"    $100 chronological: -> ${eq:,.2f} ({taken} taken, {sk} skipped for slots/cash) over this window")
        go = lo2 > 0 and len(ent) >= 30
        print(f"    VERDICT {book}: {'GO (edge survives removing the best coin)' if go else 'NO-GO / NOT ENOUGH DATA (not proven)'}")
    print("\nNOTE: window is short; a GO here means 'worth a tiny live test', never 'safe'. Candle fills are optimistic.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", type=int, default=0)
    ap.add_argument("--chains", default=",".join(CHAINS))
    ap.add_argument("--pages", type=int, default=3)
    a = ap.parse_args()
    chains = [c for c in a.chains.split(",") if c]
    if a.collect:
        collect(chains, a.collect, a.pages)
    cache = _load()
    ent = entries(cache, chains)
    ts = [v["created_ts"] for v in cache.values() if v.get("created_ts")]
    report(ent, (max(ts) - min(ts)) / 3600 if ts else 0)


if __name__ == "__main__":
    main()

"""END-TO-END REPLAY TEST: real entry gate + real exits + tiers + slots on an UNBIASED sample of new coins.

  python e2e_test.py --loop             run until Ctrl+C: lists new pools, replays each once it is 4 h old, prints the verdict
  python e2e_test.py --collect 120      one pass (fetch up to 120 mature pools)
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


def _env_key():
    k = os.environ.get("COINGECKO_API_KEY", "").strip()
    if not k:
        try:
            for line in open(".env", encoding="utf-8"):
                if line.strip().startswith("COINGECKO_API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return k


CG_KEY = _env_key()
CALLS = os.path.join("logs", "e2e_calls.json")
MAX_CALLS = int(os.environ.get("E2E_MAX_CALLS", "4000"))      # total test budget (CoinGecko demo plan = 10,000/month)
PAUSE = 2.3 if CG_KEY else rc.PAUSE                            # ~26 calls/min with a key, GeckoTerminal public pace without


def _calls_used():
    try:
        return int(json.load(open(CALLS))["n"])
    except Exception:  # noqa: BLE001
        return 0


def _count():
    os.makedirs("logs", exist_ok=True)
    json.dump({"n": _calls_used() + 1}, open(CALLS, "w"))


def _get(path, params=None):
    """CoinGecko onchain (free demo key, own quota) when COINGECKO_API_KEY is in .env, else GeckoTerminal public."""
    if _calls_used() >= MAX_CALLS:
        raise SystemExit(f"e2e call budget used up ({MAX_CALLS}); raise E2E_MAX_CALLS if you want more")
    if not CG_KEY:
        return rc._get(path, params)
    import requests
    for i in range(5):
        _count()
        try:
            r = requests.get("https://api.coingecko.com/api/v3/onchain" + path, params=params, timeout=25,
                             headers={"Accept": "application/json", "x-cg-demo-api-key": CG_KEY})
        except requests.RequestException as e:
            print("   network error:", e); time.sleep(10); continue
        if r.status_code == 429:
            print("   rate-limited, waiting 20s ..."); time.sleep(20); continue
        if r.status_code in (401, 403):
            raise SystemExit(f"CoinGecko rejected the key (HTTP {r.status_code}). Check COINGECKO_API_KEY in .env")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    return None


def _load():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(c):
    os.makedirs("logs", exist_ok=True)
    json.dump(c, open(CACHE, "w", encoding="utf-8"))


MIN_AGE_H = float(os.environ.get("E2E_MIN_AGE_H", "4"))   # a pool is replayed only once it is this old (full path known)


def discover(cache, chains, pages):
    """List new pools (1 call per page) and remember them; candles are fetched later, when the pool is old enough."""
    added = 0
    for chain in chains:
        for p in range(1, pages + 1):
            d = _get(f"/networks/{chain}/new_pools", {"page": p})
            time.sleep(PAUSE)
            for x in (d or {}).get("data", []):
                a = x["attributes"]
                key = f"{chain}:{a['address']}"
                try:
                    price, fdv = float(a.get("base_token_price_usd")), float(a.get("fdv_usd"))
                except (TypeError, ValueError):
                    continue
                if key in cache or price <= 0 or fdv <= 0:
                    continue
                cache[key] = {"chain": chain, "pool": a["address"], "created_ts": rc._iso(a.get("pool_created_at")) or 0,
                              "supply": round(fdv / price), "symbol": (a.get("name") or a["address"][:6]).split(" / ")[0],
                              "candles": None, "final": False}
                added += 1
    return added


def fetch_mature(cache, chains, limit):
    now = time.time()
    todo = sorted((k for k, v in cache.items() if v["chain"] in chains and not v.get("final") and v.get("created_ts")
                   and now - v["created_ts"] >= MIN_AGE_H * 3600), key=lambda k: cache[k]["created_ts"])
    done = 0
    for key in todo[:limit]:
        v = cache[key]
        pool = v.get("pool") or key.split(":", 1)[1]
        d = _get(f"/networks/{v['chain']}/pools/{pool}/ohlcv/minute",
                    {"aggregate": 1, "limit": 1000, "before_timestamp": int(v["created_ts"] + 1000 * 60), "currency": "usd"})
        time.sleep(PAUSE)
        v["candles"] = sorted(((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or [], key=lambda c: c[0])
        v["final"] = True
        done += 1
        if done % 10 == 0:
            _save(cache); print(f"  fetched {done} (waiting: {len(todo) - done})")
    _save(cache)
    return done, len(todo) - done


def collect(chains, limit, pages=5):
    cache = _load()
    n = discover(cache, chains, pages)
    _save(cache)
    done, left = fetch_mature(cache, chains, limit)
    young = sum(1 for v in cache.values() if not v.get("final"))
    print(f"discovered {n} new pools | replay-ready fetched {done} | {young} pools still too young (< {MIN_AGE_H:.0f} h) "
          f"| cache {len(cache)}")


def loop(chains, pages=5):
    """Runs until Ctrl+C: every cycle lists new pools, fetches the ones that are old enough, prints the verdict."""
    cache = _load()
    last_disc = 0.0
    try:
        while True:
            n = 0
            if time.time() - last_disc > 20 * 60:           # listing new pools every 20 min is plenty
                n = discover(cache, chains, pages); _save(cache); last_disc = time.time()
            done, left = fetch_mature(cache, chains, 40)
            ent = entries(cache, chains)
            print(f"[{time.strftime('%H:%M')}] +{n} listed, +{done} fetched, {left} ready & waiting, "
                  f"{sum(1 for v in cache.values() if v.get('final'))} replayed, {len(ent)} entries (need 30 for a verdict)")
            if len(ent) >= 30 and done:
                ts = [v["created_ts"] for v in cache.values() if v.get("final")]
                report(ent, (max(ts) - min(ts)) / 3600)
            time.sleep(60 if not done else 5)
    except KeyboardInterrupt:
        pass
    ts = [v["created_ts"] for v in cache.values() if v.get("final")]
    report(entries(cache, chains), (max(ts) - min(ts)) / 3600 if ts else 0)


def entries(cache, chains):
    """One entry per pool the proxy gate fires on -> dict(time, chain, mcap, conf, rules inputs, multiples per book)."""
    out = []
    for key, v in cache.items():
        if v["chain"] not in chains or not v.get("final") or len(v.get("candles") or []) < 10:
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
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    chains = [c for c in a.chains.split(",") if c]
    if a.loop:
        return loop(chains, a.pages)
    if a.collect:
        collect(chains, a.collect, a.pages)
    cache = _load()
    ent = entries(cache, chains)
    ts = [v["created_ts"] for v in cache.values() if v.get("final")]
    report(ent, (max(ts) - min(ts)) / 3600 if ts else 0)


if __name__ == "__main__":
    main()

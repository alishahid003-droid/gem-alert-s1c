"""discover_winners.py - find real runners of the last ~2-3 months on pump.fun, StonkFun and Four.meme (plus other
Solana launchpads), replay them through our entry rules, and print the PATTERN per platform.

Usage (CMD, in the project folder; 20-35 minutes because the free API is paced - leave the window open):
    python discover_winners.py                       # default: named coins + all platforms + our own losers
    python discover_winners.py PAWNS "USE" FOO       # add extra coin names to look up

How coins are found and labelled
  pump.fun   Solana coins whose address ends in "pump" (pump.fun's own address pattern), found through
             GeckoTerminal's biggest / trending Solana pools (pump-fun and pumpswap pools).
  Robinhood  Robinhood Chain coins (GeckoTerminal network "robinhood"): biggest / trending pools launched 1-75 days ago
             with >= $100K market cap (the chain is young) - this is where PONS launched.
  Four.meme  BSC coins whose address ends in "4444" (Four.meme's pattern), found through GeckoTerminal's
             biggest / trending BSC pools (they trade on PancakeSwap after graduating).
  StonkFun   StonkFun's own keyless /tokens API (same one the bot already uses), ranked by its peakMarketCapUsd.
  other      the remaining big Solana pools (Meteora DBC, Raydium, ...) - where AUTON / LOAD came from.
  named      the coin names you gave (FEELSGOOD, PONS, AMC, ...). Copycat tickers are resolved AUTOMATICALLY: every coin
             with that ticker is found, the biggest + the earliest-with-liquidity are replayed, and the one whose own
             price history peaked highest is kept as the original (the rest are listed as copycats).
  our losers coins our own sniper entered and lost on (control group, so winners are compared with losers).
Each coin is replayed through the live entry rules (replay_coin.py). Output: winners.json (all data) and
logs\\winners_report.txt (per-coin lines + a PATTERN table per platform). Cached: a re-run resumes.

Honest limits: finds runners that are still alive (dead ones have no pool to look up); StonkFun and brand-new
curve-phase coins that GeckoTerminal never indexed are listed with StonkFun's own numbers but cannot be replayed.
"""
import os, sys, json, time, statistics

import requests

import replay_coin as rc

PER_PLATFORM = int(os.environ.get("WIN_PER_PLATFORM", 8))
MAX_LOSERS = int(os.environ.get("WIN_MAX_LOSERS", 10))
JUNK = {"WETH", "ETH", "WBTC", "USDC", "USDT", "SUSD", "DAI", "USDS", "SOL", "WSOL", "BTC", "STETH", "CBBTC"}
NAMES = ["FEELSGOOD", "PONS@robinhood", "AMC", "USE", "AUTON", "VEIL", "LOAD", "CLAUDIA"]   # NAME@chain forces the chain
RHC_SLUGS = ("robinhood", "robinhood-chain", "robinhood_chain")
CHAIN_SLUG = {"robinhood_chain": "robinhood"}


def platform_of(chain, addr):
    a = (addr or "")
    if chain.startswith("robinhood"):
        return "robinhood"
    if chain == "solana":
        return "pumpfun" if a.endswith("pump") else "other_solana"
    if chain == "bsc":
        return "fourmeme" if a.lower().endswith("4444") else "other_bsc"
    return "other_" + chain


def _tok(pool):
    rid = ((pool.get("relationships") or {}).get("base_token") or {}).get("data", {}).get("id", "")
    return rid.split("_", 1)[1] if "_" in rid else None


def _pool_info(pool, chain):
    a = pool.get("attributes") or {}
    addr = _tok(pool)
    return {"addr": addr, "chain": chain, "name": (a.get("name") or "").split(" / ")[0],
            "fdv": float(a.get("fdv_usd") or 0), "created": rc._iso(a.get("pool_created_at")) or 0,
            "vol24": float((a.get("volume_usd") or {}).get("h24") or 0),
            "liq": float(a.get("reserve_in_usd") or 0), "platform": platform_of(chain, addr)}


def find_by_name(name, chain="solana"):
    """Every coin with this exact ticker launched in the last 120 days (pages 1-2 of GeckoTerminal search), by token."""
    now, c, seen = time.time(), [], set()
    for page in (1, 2):
        d = rc._get("/search/pools", {"query": name, "network": chain, "page": page})
        time.sleep(rc.PAUSE)
        for p in (d or {}).get("data", []):
            i = _pool_info(p, chain)
            if i["addr"] and i["addr"] not in seen and i["name"].upper() == name.upper() and now - i["created"] < 120 * 86400:
                seen.add(i["addr"]); c.append(i)
        if len((d or {}).get("data", [])) < 20:
            break
    c.sort(key=lambda i: -(i["fdv"] or 0))
    return c


def pick_original(name, chain, say, k_fdv=2):
    """Copycat tickers are common after a runner. The ORIGINAL is the candidate whose own price history peaked
    highest; replayed: the biggest by current market cap plus the earliest launched with real liquidity.
    A candidate whose CURRENT market cap already exceeds the best peak, or whose peak is within 2x of it, is also
    kept (it is a runner in its own right, or the ambiguity is real). Returns (list of items to keep, all cands)."""
    cands = find_by_name(name, chain)
    if not cands:
        return [], []
    pick = cands[:k_fdv]
    liquid = sorted([x for x in cands if x["liq"] >= 1000], key=lambda x: x["created"])
    if liquid and liquid[0] not in pick:
        pick.append(liquid[0])
    if len(pick) == 1:
        pick[0]["verdict"] = "only candidate"
        return [pick[0]], cands
    scored = []
    for it in pick:
        r = rc.analyze(it["addr"], chain=CHAIN_SLUG.get(it["chain"], it["chain"]))
        scored.append((r.get("peak") or 0, it["fdv"], it, r))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    best_peak = scored[0][0]
    keep = []
    say(f"   {name}: {len(cands)} coins use this ticker; replayed {len(pick)} to find the original:")
    for n_, (pk, fdv, it, r) in enumerate(scored):
        also = n_ > 0 and (fdv >= best_peak > 0 or pk * 2 >= best_peak > 0)
        tag = "<-- ORIGINAL (highest peak)" if n_ == 0 else ("<-- ALSO KEPT: runner in its own right / ambiguous" if also else "(copycat / smaller)")
        say(f"        {it['addr'][:10]}.. peak ${pk:,.0f}  now ${fdv:,.0f}  launched {r.get('created', '?')}"
            f"{'' if r.get('ok') else '  [' + str(r.get('error', 'not replayable'))[:60] + ']'}   {tag}")
        if n_ == 0 or also:
            it["verdict"] = "highest peak" if n_ == 0 else "also a runner / ambiguous"
            keep.append(it)
    return keep, cands


def resolve_rhc():
    forced = os.environ.get("MC_RHC_SLUG")
    for slug in ([forced] if forced else RHC_SLUGS):
        d = rc._get(f"/networks/{slug}/trending_pools", {"duration": "24h", "page": 1})
        time.sleep(rc.PAUSE)
        if d and d.get("data") is not None:
            return slug
    return None


def discover(chain, min_fdv, pages=3, min_age=3):
    """Big pools launched min_age-75 days ago on a chain (= coins that ran recently and are still alive)."""
    now, seen, out = time.time(), set(), []
    reqs = [(f"/networks/{chain}/pools", {"page": p, "sort": "h24_volume_usd_desc"}) for p in range(1, pages + 1)]
    reqs.append((f"/networks/{chain}/trending_pools", {"duration": "24h", "page": 1}))
    for path, params in reqs:
        d = rc._get(path, params)
        time.sleep(rc.PAUSE)
        for p in (d or {}).get("data", []):
            i = _pool_info(p, chain)
            age_d = (now - i["created"]) / 86400
            if i["name"].upper() in JUNK:
                continue                                    # wrapped / stable tokens are not memecoins
            if i["addr"] and i["addr"] not in seen and min_age <= age_d <= 75 and i["fdv"] >= min_fdv:
                seen.add(i["addr"]); out.append(i)
    return out


def stonkfun_top():
    try:
        from config import CONFIG
        base = CONFIG.stonkfun_base_url
    except Exception:  # noqa: BLE001
        base = os.environ.get("STONKFUN_BASE_URL", "")
    if not base:
        print("   StonkFun base URL not found (config.stonkfun_base_url); skipping StonkFun"); return []
    rows, seen = [], set()
    for sort in ("newest", "marketCap", "volume", "trending"):
        try:
            r = requests.get(f"{base}/tokens", params={"sort": sort, "limit": 100}, timeout=25)
            body = r.json() if r.status_code == 200 else {}
            got = len(body.get("data") or body.get("tokens") or []) if isinstance(body, dict) else 0
            print(f"   StonkFun sort={sort}: HTTP {r.status_code}, {got} tokens"
                  + ("" if got else f" (first 150 chars: {r.text[:150]!r})"))
        except Exception as e:  # noqa: BLE001
            print(f"   StonkFun sort={sort}: {type(e).__name__}"); continue
        time.sleep(2)
        for t in (body.get("data") or body.get("tokens") or []):
            if isinstance(t, str) and len(t) > 30 and t not in seen:      # API sometimes returns bare mint strings
                seen.add(t)
                rows.append({"addr": t, "chain": "solana", "name": t[:6], "platform": "stonkfun", "sf_peak": 0})
                continue
            if not isinstance(t, dict) or not t.get("mint") or t["mint"] in seen:
                continue
            seen.add(t["mint"])
            m = t.get("market") or {}
            rows.append({"addr": t["mint"], "chain": "solana", "name": t.get("symbol") or t["mint"][:6],
                         "platform": "stonkfun", "sf_peak": m.get("peakMarketCapUsd") or m.get("marketCapUsd") or 0,
                         "sf_now": m.get("marketCapUsd"), "sf_created": t.get("createdAt"),
                         "sf_grad": t.get("graduationProgress")})
    rows.sort(key=lambda r: -(float(r["sf_peak"] or 0)))
    return rows[:PER_PLATFORM]


def our_losers():
    try:
        with open(".gem_alert_state.json", encoding="utf-8") as fh:
            s = json.load(fh)
    except Exception:  # noqa: BLE001
        return []
    seen, out = set(), []
    for r in s.get("paper_closed_positions", []):
        ch = CHAIN_SLUG.get(r.get("chain"), r.get("chain"))
        if ch not in ("solana", "bsc", "robinhood") or r.get("strategy") != "early":
            continue
        if (r.get("pnl_pct") or 0) <= -50 and r.get("token") and r["token"] not in seen:
            seen.add(r["token"])
            out.append({"addr": r["token"], "chain": ch, "name": (r.get("tags") or {}).get("name") or r["token"][:6],
                        "platform": "our_losers"})
    return out[:MAX_LOSERS]


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def summarize(rows, label, say):
    ok = [r for r in rows if r.get("ok")]
    fired = [r["fired"]["proxy"] for r in ok if r["fired"]["proxy"]]
    say(f"\n--- {label}: {len(rows)} coins, {len(ok)} replayable, sniper (proxy) would fire on {len(fired)} ---")
    if not fired:
        return
    say(f"   fire minute after launch (median): {_med([f['minute'] for f in fired])}   entry mcap (median): ${_med([f['mcap'] for f in fired]):,.0f}")
    say(f"   signals (median): {_med([f['n_conf'] for f in fired])}   5-min volume at fire (median): ${_med([f['vol5'] for f in fired]):,.0f}"
        f"   5-min price change (median): {_med([f['chg5'] for f in fired])}%   volume vs prior 5 min (median x): {_med([f['vol_ratio'] for f in fired])}")
    say(f"   peak multiple after entry (median): x{_med([f['peak_mult'] for f in fired])}   best: x{max(f['peak_mult'] for f in fired)}"
        f"   reached 2x: {100 * sum(f['hit_2x'] for f in fired) // len(fired)}%   reached 5x: {100 * sum(f['peak_mult'] >= 5 for f in fired) // len(fired)}%")
    dips = [f["dip_before_2x"] for f in fired if f["hit_2x"]]
    if dips:
        say(f"   deepest dip before a coin reached 2x (median): {_med(dips)}x of entry; worst winner dipped to {min(dips):.2f}x"
            f" (a stop tighter than {1 - min(dips):.0%} below entry would have killed it)")


def exit_sweep(rows, label, say):
    fired = [r["fired"]["proxy"] for r in rows if r.get("ok") and r["fired"]["proxy"] and r["fired"]["proxy"].get("exits")]
    if not fired:
        return
    say(f"   exit sweep ({label}, {len(fired)} entries, equal stake each, after 10% cost; ladder 2x/4x/10x + trail as live):")
    for sv in ("0.35", "0.45", "0.55", "0.7"):
        xs = [f["exits"][sv] for f in fired]
        say(f"      hard stop {sv}: average x{statistics.mean(xs):.2f}, median x{statistics.median(xs):.2f}, "
            f"total x{sum(xs):.1f} on {len(xs)} stakes, coins ending below 1.0x: {sum(x < 1 for x in xs)}")


def main():
    names = NAMES + [a for a in sys.argv[1:] if a.upper().split("@")[0] not in [n.upper().split("@")[0] for n in NAMES]]
    os.makedirs("logs", exist_ok=True)
    lines = []
    def say(s=""):
        print(s); lines.append(s)
    groups = {}
    def add(item):
        groups.setdefault(item["platform"] if item.get("platform") != "named" else "named", []).append(item)
    say("STEP 1: named coins")
    named_addrs = set()
    rhc = None
    for n_ in names:
        n, _, ch = n_.partition("@")
        ch = ch or "solana"
        if ch.startswith("robinhood"):
            rhc = rhc or resolve_rhc()
            if not rhc:
                say(f"   {n}: Robinhood Chain not found on GeckoTerminal (set MC_RHC_SLUG)"); continue
            ch = rhc
        keep, cands = pick_original(n, ch, say)
        if not keep:
            say(f"   {n}: not found among {ch} pools launched in the last 120 days"); continue
        for top in keep:
            top = dict(top, platform="named")
            say(f"   {n}: using {top['addr']} ({top['verdict']}; {platform_of(ch, top['addr'])}, chain {ch})")
            add(top); named_addrs.add(top["addr"])
    say("STEP 2: runners of the last ~2 months, by platform")
    per = {}
    rhc = rhc or resolve_rhc()
    plan = [("solana", 1_000_000, 3), ("bsc", 500_000, 3)] + ([(rhc, 100_000, 1)] if rhc else [])
    if not rhc:
        say("   Robinhood Chain slug not found on GeckoTerminal - skipping it (set MC_RHC_SLUG to force)")
    for chain, fdv, min_age in plan:
        for it in discover(chain, fdv, pages=5 if chain == "bsc" else 3, min_age=min_age):
            if it["addr"] in named_addrs:
                continue
            per.setdefault(it["platform"], []).append(it)
    for plat, items in per.items():
        lim = (12 if plat == "robinhood" else PER_PLATFORM) if plat in ("pumpfun", "fourmeme", "robinhood", "other_solana") else 4
        say(f"   {plat}: {len(items)} candidates (taking {min(len(items), lim)})")
        for it in items[:lim]:
            add(it)
    sf = stonkfun_top()
    say(f"   stonkfun: {len(sf)} tokens from StonkFun's own API")
    for it in sf:
        if it["addr"] not in named_addrs:
            add(it)
    say("STEP 3: our own losing entries (control)")
    for it in our_losers():
        add(it)
    total = sum(len(v) for v in groups.values())
    say(f"STEP 4: replaying {total} coins (about {total * 25 // 60 + 1} minutes)")
    results, n = {}, 0
    for g, items in groups.items():
        results[g] = []
        for it in items:
            n += 1
            print(f"[{n}/{total}] {g}: {it.get('name')} ...")
            r = rc.analyze(it["addr"], chain=CHAIN_SLUG.get(it["chain"], it["chain"]))
            r.update({"group": g, "label": it.get("name")})
            for k in ("sf_peak", "sf_now", "sf_created", "sf_grad"):
                if k in it:
                    r[k] = it[k]
            results[g].append(r)
            with open("winners.json", "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=1)
    say("\n\n================ PER COIN ================")
    for g, rows in results.items():
        say(f"\n##### {g}")
        for r in rows:
            rc.show(r, say)
            if "sf_peak" in r:
                say(f"   StonkFun's own numbers: peak ${float(r['sf_peak'] or 0):,.0f}, now {r.get('sf_now')}, created {r.get('sf_created')}, graduation {r.get('sf_grad')}")
    say("\n\n================ PATTERN BY PLATFORM ================")
    for g, rows in results.items():
        summarize(rows, g, say)
        exit_sweep(rows, g, say)
    allrows = [r for rows in results.values() for g_, r in [(None, r) for r in rows] if r.get("group") != "our_losers"]
    say("\n--- ALL WINNER GROUPS COMBINED (everything except our_losers) ---")
    summarize(allrows, "all winners", say)
    exit_sweep(allrows, "all winners", say)
    say("\nRead: where winners fire at a similar minute/mcap with MORE signals and higher volume-vs-prior than our_losers,"
        " that gap is the rule to tighten; the dip figure tells us how wide the stop must be, per platform.")
    with open(os.path.join("logs", "winners_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\nDone. Send me logs\\winners_report.txt (and winners.json if you can).")


if __name__ == "__main__":
    main()

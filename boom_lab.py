"""ONE script: finds the last ~6 months' big winners on Solana / Base / BSC / Robinhood chain (all launchpads/DEXes the free
GeckoTerminal feed lists), adds the coins Ali named, measures how each moved, and for Solana winners pulls creator, insider/sniper/
bundler risk data and the first buyers, then finds wallets that show up early in MANY winners.
Run in NORMAL CMD:   python boom_lab.py        (~20-25 min; safe to stop and re-run, it resumes from cache)
Output folder boom_lab/: report.txt (read this), winners.csv, repeat_wallets.csv, cache/*.json"""
import json, os, sys, time, csv, collections, datetime, hashlib, statistics as st, urllib.request, urllib.parse, urllib.error
OUT = "boom_lab"; CACHE = f"{OUT}/cache"; os.makedirs(CACHE, exist_ok=True)
GT = "https://api.geckoterminal.com/api/v2"; ST = "https://data.solanatracker.io"
DAYS = 185; MIN_FDV = float(os.environ.get("BOOM_MIN_FDV", 3e6)); PAGES = 3; ANALYSE_TOP = 90; DEEP_TOP = 25; GAP = 3.2
NAMED = ["PONS", "MARS", "USELESS", "STONK", "SHROOM", "FEELS GOOD", "JOHN PHIL", "CLAWS", "PAWN", "CLAUDIA", "WHALE"]
def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
STK = env("SOLANATRACKER_API_KEY"); st_used = [0]; _last = [0.0]
rep = open(f"{OUT}/report.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); rep.write(t + "\n"); rep.flush()
def get(url, headers=None, gap=GAP, cache=True):
    fn = f"{CACHE}/{hashlib.md5(url.encode()).hexdigest()}.json"
    if cache and os.path.exists(fn):
        try: return json.load(open(fn, encoding="utf-8"))
        except Exception: pass
    h = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}; h.update(headers or {})
    err = "unknown"
    for i in range(6):
        w = gap - (time.time() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.time()
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40))
            if cache: json.dump(d, open(fn, "w", encoding="utf-8"))
            return d
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code == 429: time.sleep(20 * (i + 1)); continue
            if e.code in (404, 400): return {"_error": err}
            time.sleep(5)
        except Exception as e:
            err = str(e); time.sleep(5)
    return {"_error": err}
def parse(t):
    try: return datetime.datetime.strptime(str(t)[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception: return None
NOW = datetime.datetime.utcnow()
def pinfo(p):
    a = p["attributes"]; rel = p.get("relationships", {})
    return dict(net=p["id"].split("_")[0], pool=a["address"], name=a.get("name"), dex=rel.get("dex", {}).get("data", {}).get("id"),
                base=rel.get("base_token", {}).get("data", {}).get("id", "").split("_", 1)[-1], created=parse(a.get("pool_created_at")),
                fdv=float(a.get("fdv_usd") or 0), liq=float(a.get("reserve_in_usd") or 0), vol24=float((a.get("volume_usd") or {}).get("h24") or 0))
nets = [n["id"] for p in (1, 2) for n in get(f"{GT}/networks?page={p}").get("data", [])]
targets = [n for n in nets if n in ("solana", "base", "bsc") or "robin" in n]
say("chains used:", targets, "(Robinhood chain listed by the free feed: %s)" % any("robin" in n for n in nets))
cands = {}
def add(p, force=False):
    i = pinfo(p)
    if not i["created"]: return
    if not force and ((NOW - i["created"]).days > DAYS or i["fdv"] < MIN_FDV): return
    i["named"] = force; cands[i["pool"]] = i
for nm in NAMED:
    ps = [pinfo(p) for p in get(f"{GT}/search/pools?query={urllib.parse.quote(nm)}&page=1").get("data", [])]
    key = nm.lower().replace(" ", "")
    ps = [p for p in ps if key in (p["name"] or "").lower().replace(" ", "")]
    ps.sort(key=lambda p: -(p["liq"] + p["vol24"]))
    if ps and ps[0]["created"]:
        ps[0]["named"] = True; cands[ps[0]["pool"]] = ps[0]
    else: say("named coin not found on free feed:", nm)
for net in targets:
    dexes = [d["id"] for d in get(f"{GT}/networks/{net}/dexes?page=1").get("data", [])]
    say(net, "platforms/DEXes:", dexes[:30])
    for dx in dexes[:10]:
        for pg in range(1, PAGES + 1):
            d = get(f"{GT}/networks/{net}/dexes/{dx}/pools?page={pg}").get("data", [])
            for p in d: add(p)
            if len(d) < 20: break
    for srt in ("h24_volume_usd_desc", "h24_tx_count_desc"):
        for pg in range(1, 4):
            for p in get(f"{GT}/networks/{net}/pools?page={pg}&sort={srt}").get("data", []): add(p)
    for p in get(f"{GT}/networks/{net}/trending_pools?duration=24h&page=1").get("data", []): add(p)
    say(net, "candidates so far:", sum(1 for c in cands.values() if c["net"] == net))
say("total candidates:", len(cands))
def candles(net, pool):
    d = get(f"{GT}/networks/{net}/pools/{pool}/ohlcv/day?aggregate=1&limit=1000&currency=usd")
    return sorted((d.get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or [])
rows = []
order = sorted(cands.values(), key=lambda c: (not c["named"], -c["fdv"]))[:ANALYSE_TOP + len(NAMED)]
for k, c in enumerate(order):
    day = candles(c["net"], c["pool"])
    if not day: continue
    px = day[-1][4]; sup = c["fdv"] / px if px else 0
    f = day[0]; peak = max(day, key=lambda x: x[2]); e50 = next((x for x in day if x[4] * sup >= 5e4), None)
    rows.append(dict(name=c["name"], named=c["named"], net=c["net"], dex=c["dex"], created=str(c["created"])[:10], age_days=(NOW - c["created"]).days,
        born_fdv=round(f[1] * sup), day1_high_fdv=round(f[2] * sup), peak_fdv=round(peak[2] * sup), peak_after_days=(peak[0] - f[0]) // 86400,
        fdv_now=round(c["fdv"]), liq_now=round(c["liq"]), x_from_50k_close=round(peak[2] / e50[4], 1) if e50 and peak[0] >= e50[0] else "",
        first_50k_after_days=(e50[0] - f[0]) // 86400 if e50 else "", token=c["base"], pool=c["pool"]))
    if k % 10 == 0: say("...analysed", k, "of", len(order))
if not rows: say("NO ROWS - feed unreachable"); sys.exit()
with open(f"{OUT}/winners.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); [w.writerow(r) for r in rows]
big = [r for r in rows if r["peak_fdv"] >= MIN_FDV]
say("\n================ 1. WHERE THE WINNERS WERE BORN (chain / platform) ================")
g = collections.defaultdict(list)
for r in big: g[(r["net"], r["dex"])].append(r)
for (net, dx), L in sorted(g.items(), key=lambda x: -len(x[1])):
    say(f"{net:8s} {str(dx):22s} winners={len(L):3d} | born FDV median ${int(st.median(r['born_fdv'] for r in L)):,} | peak FDV median ${int(st.median(r['peak_fdv'] for r in L)):,} | days to peak median {int(st.median(r['peak_after_days'] for r in L))}")
say("\n================ 2. HOW BIG WERE THEY WHEN THEY STARTED? ================")
b = collections.Counter(("<10k" if r["born_fdv"] < 1e4 else "10-50k" if r["born_fdv"] < 5e4 else "50-200k" if r["born_fdv"] < 2e5 else ">200k") for r in big); say(dict(b))
xs = [r["x_from_50k_close"] for r in big if r["x_from_50k_close"] != ""]
if xs: say("multiple from first $50k+ close to peak: median %.1fx, top quartile %.1fx, max %.0fx (n=%d)" % (st.median(xs), sorted(xs)[int(len(xs) * .75)], max(xs), len(xs)))
say("days from launch to first $50k close, median:", st.median([r["first_50k_after_days"] for r in big if r["first_50k_after_days"] != ""] or [0]))
say("\n================ 3. TOP 40 WINNERS ================")
for r in sorted(big, key=lambda r: -r["peak_fdv"])[:40]:
    say(f"{'*' if r['named'] else ' '}{str(r['name'])[:26]:26s} {r['net']:7s} {str(r['dex'])[:16]:16s} born ${r['born_fdv']:>9,} peak ${r['peak_fdv']:>13,} in {r['peak_after_days']:>3}d  x-from-50k {r['x_from_50k_close']}  created {r['created']}")
say("\n================ 4. YOUR NAMED COINS (*) ================")
for r in rows:
    if r["named"]: say(f"{r['name']} | {r['net']} {r['dex']} | created {r['created']} | born ${r['born_fdv']:,} -> peak ${r['peak_fdv']:,} after {r['peak_after_days']}d | x from first $50k close: {r['x_from_50k_close']} | now ${r['fdv_now']:,}")
# ---------- Solana deep dive ----------
say("\n================ 5. SOLANA DEEP DIVE: creator, insiders, snipers, first buyers ================")
sol = [r for r in sorted(rows, key=lambda r: -r["peak_fdv"]) if r["net"] == "solana"][:DEEP_TOP]
buyers = collections.defaultdict(list); creators = collections.Counter()
def stget(path):
    if not STK or st_used[0] >= 90: return {"_error": "no key / credit cap"}
    d = get(f"{ST}{path}", {"x-api-key": STK}); st_used[0] += 1; return d
def short(x, n=260): return json.dumps(x, default=str)[:n]
for r in sol:
    t = stget(f"/tokens/{r['token']}"); fbd = stget(f"/first-buyers/{r['token']}")
    tok = t.get("token") or {}; risk = t.get("risk") or {}
    cr = tok.get("creator") or t.get("creator") or (t.get("deployer")) or ""
    if cr: creators[cr] += 1
    pools = [(p.get("market"), str(p.get("createdAt"))[:10]) for p in (t.get("pools") or [])][:3]
    say(f"\n{r['name']} | {r['dex']} | born ${r['born_fdv']:,} peak ${r['peak_fdv']:,}")
    say("   launch pools:", pools, "| creator:", cr or "?")
    say("   risk:", short({k: risk.get(k) for k in risk if k not in ("risks",)}, 420) if risk else t.get("_error"))
    rows_fb = fbd if isinstance(fbd, list) else fbd.get("buyers") or fbd.get("data") or []
    got = []
    for x in rows_fb:
        if isinstance(x, dict) and x.get("wallet"):
            pnl = x.get("pnl") or x.get("total") or x.get("realized") or 0; buyers[x["wallet"]].append((r["name"], pnl)); got.append((x["wallet"], pnl))
    got.sort(key=lambda a: -(a[1] or 0))
    say("   first buyers: %d fetched; top by profit:" % len(got), [(w[:8], round(p)) for w, p in got[:5]])
say("\n================ 6. WALLETS THAT WERE EARLY IN MANY WINNERS (copy-trade candidates) ================")
rep_w = sorted(((w, v) for w, v in buyers.items() if len(v) >= 2), key=lambda x: (-len(x[1]), -sum(p for _, p in x[1])))
with open(f"{OUT}/repeat_wallets.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["wallet", "winners_count", "total_pnl", "coins"])
    for wal, v in rep_w: w.writerow([wal, len(v), round(sum(p for _, p in v)), "; ".join(n for n, _ in v)])
ours = set()
try:
    sys.path.insert(0, "."); import state
    ours = set(state.get_value("st_early_wallets_v1") or [])
except Exception: pass
say("wallets early in >=2 of the top %d Solana winners: %d  (already in our watch list: %d)" % (len(sol), len(rep_w), sum(1 for w, _ in rep_w if w in ours)))
for wal, v in rep_w[:25]: say("  ", wal, "| in", len(v), "winners | profit total $%d" % sum(p for _, p in v), "|", ", ".join(n for n, _ in v)[:90], "| ours" if wal in ours else "")
if creators: say("creators with >1 winner:", [(c, n) for c, n in creators.items() if n > 1])
say("\nSolana Tracker credits used this run:", st_used[0], "(of 2,500/month)")
say("DONE")

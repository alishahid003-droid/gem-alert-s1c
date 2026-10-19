"""grad_runners.py - what do GRADUATED pump.fun coins that later ran 5x-10x+ look like, versus the ones that died?
Universe = coins OUR OWN stream saw graduate (early_log 'grad' rows: unbiased, not only survivors). For each we pull the real
price path after graduation (GeckoTerminal, free), label runners, then compare at-the-time features (our early_log) and
Solana Tracker data (risk/bundlers/snipers/holders, first buyers, top traders) between runners and duds, and look for wallets
that show up in many runners but rarely in duds. Also simulates a "buy after graduation" entry on the same coins.
Run in a NORMAL CMD in the project folder:   python grad_runners.py      (~35-45 min, resumable; uses ~300 of the 2,500 Solana Tracker credits)
Options (set before running, e.g.  set GR_UNIVERSE=150):  GR_UNIVERSE (default 300 coins), GR_ST_CAP (default 330 credits), GR_DRY=1 (offline check)
Output folder grad_runners/: report.txt (send me this), coins.csv, repeat_wallets.csv, cache/"""
import json, os, sys, time, csv, glob, random, hashlib, collections, statistics as st, datetime, urllib.request, urllib.error
OUT = "grad_runners"; CACHE = f"{OUT}/cache"; os.makedirs(CACHE, exist_ok=True)
GT = "https://api.geckoterminal.com/api/v2"; STU = "https://data.solanatracker.io"
UNIV = int(os.environ.get("GR_UNIVERSE", 300)); ST_CAP = int(os.environ.get("GR_ST_CAP", 330)); DRY = os.environ.get("GR_DRY") == "1"
GAP = 3.2; SUPPLY = 1e9; COST = 0.15
def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
STK = env("SOLANATRACKER_API_KEY"); used = [0]; _last = [0.0]
rep = open(f"{OUT}/report.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); rep.write(t + "\n"); rep.flush()
def get(url, headers=None, gap=GAP):
    fn = f"{CACHE}/{hashlib.md5(url.encode()).hexdigest()}.json"
    if os.path.exists(fn):
        try: return json.load(open(fn, encoding="utf-8"))
        except Exception: pass
    if DRY: return {"_error": "dry"}
    h = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}; h.update(headers or {}); err = "unknown"
    for i in range(6):
        w = gap - (time.time() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.time()
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40))
            json.dump(d, open(fn, "w", encoding="utf-8")); return d
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code == 429: time.sleep(20 * (i + 1)); continue
            if e.code in (404, 400): d = {"_error": err}; json.dump(d, open(fn, "w")); return d
            time.sleep(5)
        except Exception as e:
            err = str(e); time.sleep(5)
    return {"_error": err}
def stget(path):
    if not STK or used[0] >= ST_CAP: return {"_error": "no key / credit cap"}
    fn = f"{CACHE}/{hashlib.md5((STU + path).encode()).hexdigest()}.json"
    if not os.path.exists(fn): used[0] += 1
    return get(STU + path, {"x-api-key": STK}, gap=1.1)
# ---------------- 1. universe from our own early_log ----------------
create = {}; grad = {}; snaps = collections.defaultdict(list)
for f in sorted(glob.glob("early_log/*.jsonl")):
    for l in open(f, errors="ignore"):
        try: r = json.loads(l)
        except Exception: continue
        t = r.get("t"); m = r.get("mint")
        if t == "create": create[m] = r
        elif t == "grad": grad.setdefault(m, r["ts"])
        elif t == "snap" and r.get("mc"): snaps[m].append(r)
say("graduations logged by our stream:", len(grad), "| with create row:", sum(1 for m in grad if m in create))
now = time.time()
pool_m = [m for m, ts in grad.items() if m in create and now - ts > 3 * 86400]   # need >=3 days of aftermath
if os.environ.get("GR_CACHED"): pool_m = [m for m in pool_m if os.path.exists(f"{CACHE}/{hashlib.md5((GT + '/networks/solana/tokens/' + m + '/pools?page=1').encode()).hexdigest()}.json")]
random.seed(7); random.shuffle(pool_m); uni = pool_m[:UNIV]
say("universe used (graduated >3 days ago, random sample):", len(uni))
# ---------------- 2. price path after graduation ----------------
def path(m):
    d = get(f"{GT}/networks/solana/tokens/{m}/pools?page=1")
    ps = d.get("data") or []
    if not ps: return None
    ps.sort(key=lambda p: -float(p["attributes"].get("reserve_in_usd") or 0)); p = ps[0]
    pool = p["attributes"]["address"]
    c = get(f"{GT}/networks/solana/pools/{pool}/ohlcv/hour?aggregate=1&limit=1000&currency=usd")
    L = sorted((c.get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or [])
    return dict(pool=pool, candles=L, liq=float(p["attributes"].get("reserve_in_usd") or 0))
coins = []
for k, m in enumerate(uni):
    if k % 20 == 0: say("...price paths", k, "of", len(uni))
    P = path(m)
    if not P or len(P["candles"]) < 3: continue
    G = grad[m]; L = P["candles"]
    i0 = next((i for i, x in enumerate(L) if x[0] + 3600 > G), None)       # candle containing / right after graduation
    if i0 is None or i0 + 2 >= len(L): continue
    ent = L[i0][4]; emc = ent * SUPPLY                                       # close of graduation hour ~ a trader entering within the hour
    if os.environ.get("GR_BAND") and not 25000 < emc < 400000: continue
    if ent <= 0 or max(x[2] for x in L[i0 + 1:i0 + 1 + 14 * 24]) / ent > 2000: continue   # drop broken price data
    after = L[i0 + 1:i0 + 1 + 14 * 24]
    if len(after) < 24: continue
    peak = max(x[2] for x in after) / ent
    path_x = [(x[2] / ent, x[3] / ent) for x in after]
    cr = create[m]; sn = [s for s in snaps[m] if s["ts"] <= G]; last = sn[-1] if sn else {}
    coins.append(dict(mint=m, pool=P["pool"], grad_ts=G, entry_mc=round(emc), peak_x=round(peak, 2), path=path_x,
        first_hr_vol=round(L[i0 + 1][5]) if len(L[i0 + 1]) > 5 else 0, liq=round(P["liq"]),
        mins_to_grad=round((G - cr["ts"]) / 60, 1), creator_launches=cr.get("creator_launches", 0), creator_grads=cr.get("creator_grads", 0),
        init_sol=cr.get("init_sol", 0), pre_b=last.get("b", 0), pre_s=last.get("s", 0), pre_v=last.get("v", 0), hour=datetime.datetime.utcfromtimestamp(G).hour,
        creator=cr.get("creator", "")))
say("coins with usable price path (entry mcap NOT filtered, supply assumed 1B so mcap column is unreliable):", len(coins))
if not coins: say("NOTHING to analyse (feeds unreachable or early_log too new)"); sys.exit()
N = len(coins); say("\n================ 1. BASE RATES AFTER GRADUATION (buy at close of graduation hour) ================")
for t in (2, 3, 5, 10, 20): say(f"  reached {t}x within 14d: {sum(c['peak_x'] >= t for c in coins)} of {N} ({100 * sum(c['peak_x'] >= t for c in coins) / N:.1f}%)")
say("  median entry mcap $%d | died (never above 1.2x and ended <0.3x): %d" % (st.median(c["entry_mc"] for c in coins), sum(1 for c in coins if c["peak_x"] < 1.2 and c["path"][-1][1] < 0.3)))
def sim(sub, tp, sl):
    R = []
    for c in sub:
        out = None
        for hi, lo in c["path"]:
            if lo <= sl: out = sl; break                 # conservative: stop assumed first when same hour touches both
            if hi >= tp: out = tp; break
        if out is None: out = c["path"][-1][1]
        R.append(out * (1 - COST))
    return R
say("\n================ 2. BUY-AFTER-GRADUATION SIMULATION (cost 15%, hourly candles, stop first if ambiguous) ================")
for tp, sl in ((2, .6), (3, .6), (5, .5), (10, .5)):
    R = sim(coins, tp, sl); s = sorted(R, reverse=True); say(f"  TP{tp} SL{sl}: EV={sum(R)/len(R):.2f} ex-best-2={sum(s[2:])/(len(s)-2):.2f} (n={N})")
RUN = [c for c in coins if c["peak_x"] >= 5]; DUD = [c for c in coins if c["peak_x"] < 1.5]
say(f"\nrunners (>=5x): {len(RUN)} | duds (<1.5x): {len(DUD)}")
# ---------------- 3. features at the time ----------------
say("\n================ 3. FEATURES AT GRADUATION TIME: runner rate (>=3x) by bucket ================")
def bucket(name, fn, edges):
    say(f"-- {name}")
    for lo, hi in edges:
        sub = [c for c in coins if lo <= fn(c) < hi]
        if len(sub) >= 8:
            r3 = sum(c["peak_x"] >= 3 for c in sub); r5 = sum(c["peak_x"] >= 5 for c in sub)
            say(f"   {lo}..{hi}: n={len(sub):3d}  >=3x {100*r3/len(sub):4.1f}%  >=5x {100*r5/len(sub):4.1f}%  EV(TP3/SL.6)={sum(sim(sub,3,.6))/len(sub):.2f}")
bucket("minutes from launch to graduation", lambda c: c["mins_to_grad"], [(0, 5), (5, 15), (15, 60), (60, 360), (360, 1e9)])
bucket("entry mcap at graduation $", lambda c: c["entry_mc"], [(25e3, 45e3), (45e3, 60e3), (60e3, 90e3), (90e3, 400e3)])
bucket("creator previous graduations", lambda c: c["creator_grads"], [(0, 1), (1, 3), (3, 1e9)])
bucket("creator launch count", lambda c: c["creator_launches"], [(0, 2), (2, 10), (10, 1e9)])
bucket("buys seen before graduation (last snap)", lambda c: c["pre_b"], [(0, 1), (1, 30), (30, 80), (80, 1e9)])
bucket("hour of day (UTC) of graduation", lambda c: c["hour"], [(0, 6), (6, 12), (12, 18), (18, 24)])
bucket("first hour after graduation: volume $", lambda c: c["first_hr_vol"], [(0, 20e3), (20e3, 80e3), (80e3, 300e3), (300e3, 1e12)])
bucket("pool liquidity at start $", lambda c: c["liq"], [(0, 20e3), (20e3, 40e3), (40e3, 80e3), (80e3, 1e12)])
# creator repeat: do runner creators repeat?
cc = collections.Counter(c["creator"] for c in RUN if c["creator"]); say("\nrunner creators with >1 runner in sample:", [(k[:8], v) for k, v in cc.items() if v > 1])
# ---------------- 4. Solana Tracker deep data ----------------
if DRY or not STK:
    say("\n(no Solana Tracker key / dry mode: skipping part 4-5)")
else:
    random.seed(11); ctrl = random.sample(DUD, min(len(DUD), 100)); runs = sorted(RUN, key=lambda c: -c["peak_x"])[:55]
    say("\n================ 4. SOLANA TRACKER: risk / insider data, runners vs duds ================")
    feat = {}
    def tokfeat(c):
        t = stget(f"/tokens/{c['mint']}")
        if not isinstance(t, dict) or "_error" in t: return None
        risk = t.get("risk") or {}; bd = risk.get("bundlers") or {}
        return dict(holders=t.get("holders") or 0, bundlers=bd.get("count") or 0, bundled_pct=bd.get("totalInitialPercentage") or 0,
            snipers=(risk.get("snipers") or {}).get("count") or 0, insiders=(risk.get("insiders") or {}).get("count") or 0,
            top10=risk.get("top10") or 0, dev_pct=(risk.get("dev") or {}).get("percentage") or 0, txns=t.get("txns") or 0)
    for grp, L in (("runner", runs), ("dud", ctrl)):
        for c in L:
            f = tokfeat(c)
            if f: feat[c["mint"]] = (grp, f)
    for k in ("holders", "bundlers", "bundled_pct", "snipers", "insiders", "top10", "dev_pct"):
        a = [f[k] for g, f in feat.values() if g == "runner"]; b = [f[k] for g, f in feat.values() if g == "dud"]
        if a and b: say(f"   {k:12s} runners median {st.median(a):10.2f} (n={len(a)}) | duds median {st.median(b):10.2f} (n={len(b)})   [NOTE: values are as of today, not at graduation]")
    # ---- first buyers + top traders ----
    say("\n================ 5. WALLETS: who is in the runners but not in the duds ================")
    fbr = {}; fbd = {}; top = collections.defaultdict(list); sample_keys = []
    def fbuy(m):
        d = stget(f"/first-buyers/{m}"); L = d if isinstance(d, list) else (d.get("buyers") or d.get("data") or []) if isinstance(d, dict) else []
        return [(x["wallet"], float(x.get("pnl") or x.get("total") or 0)) for x in L if isinstance(x, dict) and x.get("wallet")]
    for c in runs[:40]: fbr[c["mint"]] = fbuy(c["mint"])
    for c in ctrl[:40]: fbd[c["mint"]] = fbuy(c["mint"])
    cntR = collections.Counter(w for L in fbr.values() for w in {x for x, _ in L}); cntD = collections.Counter(w for L in fbd.values() for w in {x for x, _ in L})
    pnlR = collections.defaultdict(float)
    for L in fbr.values():
        for w, p in L: pnlR[w] += p
    bots = set()
    try:
        bots = set(x.strip() for x in open("bot_wallets.txt") if x.strip() and not x.startswith("#"))
    except Exception: pass
    rows = []
    for w, n in cntR.most_common():
        if n < 2: break
        d = cntD.get(w, 0); rows.append((w, n, d, round(pnlR[w])))
    with open(f"{OUT}/repeat_wallets.csv", "w", newline="") as fh:
        wr = csv.writer(fh); wr.writerow(["wallet", "in_runners", "in_duds", "runner_pnl", "known_bot"]); [wr.writerow(list(r) + [r[0] in bots]) for r in rows]
    say(f"first-buyer lists: {len(fbr)} runners, {len(fbd)} duds | wallets in >=2 runners: {len(rows)}")
    say("wallet | runner lists | dud lists | runner pnl $ | bot?   (a real signal = many runner lists, ~0 dud lists, NOT a bot)")
    for w, n, d, p in rows[:25]:
        say(f"   {w} | {n}/{len(fbr)} | {d}/{len(fbd)} | {p:>10,} | {'BOT' if w in bots else ''}")
    # top traders on runners (who actually made money, includes post-graduation buyers)
    for c in runs[:35]:
        d = stget(f"/top-traders/{c['mint']}")
        L = d if isinstance(d, list) else (d.get("wallets") or d.get("traders") or d.get("data") or []) if isinstance(d, dict) else []
        if L and not sample_keys and isinstance(L[0], dict): sample_keys = list(L[0].keys())
        for x in L:
            if isinstance(x, dict) and x.get("wallet"):
                top[x["wallet"]].append((c["mint"], float(x.get("total") or x.get("realized") or 0)))
    if sample_keys: say("\ntop-traders fields available:", sample_keys)
    mult = [(w, v) for w, v in top.items() if len(v) >= 3 and sum(p for _, p in v) > 3000]
    mult.sort(key=lambda x: -sum(p for _, p in x[1]))
    say(f"wallets profitable (>$3k total) across >=3 of the top runners: {len(mult)}  (these are the 'FOMO top trader' candidates)")
    for w, v in mult[:25]:
        say(f"   {w} | {len(v)} runners | total profit ${sum(p for _, p in v):,.0f} {'BOT' if w in bots else ''}")
    with open(f"{OUT}/multi_runner_traders.csv", "w", newline="") as fh:
        wr = csv.writer(fh); wr.writerow(["wallet", "runners", "profit"]); [wr.writerow([w, len(v), round(sum(p for _, p in v))]) for w, v in mult]
with open(f"{OUT}/coins.csv", "w", newline="") as fh:
    cols = [k for k in coins[0] if k != "path"]; wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); wr.writeheader(); [wr.writerow(c) for c in coins]
say("\nSolana Tracker credits used:", used[0], "of cap", ST_CAP)
say("DONE - send me grad_runners/report.txt (and multi_runner_traders.csv if it exists)")

"""RUNNER LAB: what do coins that later ran (>=3x after the 1-minute mark) look like at launch, and which clean wallets are early in many of them?
Uses OUR OWN launch logs (early_log) for the labels + Solana Tracker (/tokens/{mint} risk data, /first-buyers/{mint}) for launch-time features.
Run in NORMAL CMD:  python runner_lab.py        (~20 min, ~430 Solana Tracker credits; resumes from cache)
Dry run (no network):  set RUNNER_DRY=1  &  python runner_lab.py
Output: runner_lab/report.txt, features.csv, clean_wallets.csv"""
import json, glob, os, sys, time, csv, random, collections, statistics as st, urllib.request, urllib.error, hashlib
OUT = "runner_lab"; CACHE = f"{OUT}/cache"; os.makedirs(CACHE, exist_ok=True)
ST = "https://data.solanatracker.io"; DRY = os.environ.get("RUNNER_DRY") == "1"
MAX_RUNNERS = 110; N_DUD = 160; DUD_FB = 45; CALL_CAP = 450; GAP = 0.6
def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
KEY = env("SOLANATRACKER_API_KEY"); used = [0]; _last = [0.0]
rep = open(f"{OUT}/report.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); rep.write(t + "\n"); rep.flush()
def get(path):
    fn = f"{CACHE}/{hashlib.md5(path.encode()).hexdigest()}.json"
    if os.path.exists(fn):
        try: return json.load(open(fn, encoding="utf-8"))
        except Exception: pass
    if DRY or used[0] >= CALL_CAP or not KEY: return {"_error": "skipped"}
    w = GAP - (time.time() - _last[0])
    if w > 0: time.sleep(w)
    _last[0] = time.time(); err = "?"
    for i in range(4):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(ST + path, headers={"x-api-key": KEY, "accept": "application/json"}), timeout=40))
            used[0] += 1; json.dump(d, open(fn, "w", encoding="utf-8")); return d
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code == 429: time.sleep(15 * (i + 1)); continue
            if e.code in (400, 404): used[0] += 1; json.dump({"_error": err}, open(fn, "w")); return {"_error": err}
            time.sleep(4)
        except Exception as e: err = str(e); time.sleep(4)
    return {"_error": err}
# ---------- 1. labels from our own logs ----------
S = collections.defaultdict(list); C = {}
for f in sorted(glob.glob("early_log/2026-10-1*.jsonl")):
    for l in open(f, errors="ignore"):
        try: r = json.loads(l)
        except Exception: continue
        if r["t"] == "snap": S[r["mint"]].append(r)
        elif r["t"] == "create" and r["mint"].endswith("pump"): C[r["mint"]] = r
runners, duds = [], []
for m, L in S.items():
    c = C.get(m)
    if not c: continue
    L.sort(key=lambda r: r["age"]); ok = [r for r in L if r["mc"] and 0 < r["mc"] < 30e6]
    ent = [r for r in ok if 45 <= r["age"] <= 150]
    if not ent: continue
    e = ent[0]; later = [r["mc"] for r in ok if r["age"] > e["age"]]
    if not later: continue
    pk = max(later) / e["mc"]
    if pk > 200: continue
    rec = dict(mint=m, peak_x=pk, ts=c["ts"], creator=c.get("creator"), launches=c.get("creator_launches", 1), grads=c.get("creator_grads", 0),
               init_sol=c.get("init_sol", 0), b1=e["b"], s1=e["s"], v1=e["v"], mc1=e["mc"], age_snap=e["age"])
    if pk >= 3: runners.append(rec)
    elif pk < 1.5: duds.append(rec)
say("coins with a clean 1-minute snapshot: runners(>=3x) %d, duds(<1.5x) %d" % (len(runners), len(duds)))
random.seed(7); runners.sort(key=lambda r: -r["ts"]); runners = runners[:MAX_RUNNERS]; duds = random.sample(duds, min(N_DUD, len(duds)))
POP_DUD = len([1 for m, L in S.items() if m in C]) - len(runners)
say("sampled: %d runners, %d duds (population weight for duds ~%.0fx)" % (len(runners), len(duds), max(1, (POP_DUD * 0.97) / max(1, len(duds)))))
W = max(1.0, (POP_DUD * 0.97) / max(1, len(duds)))
if DRY: say("DRY RUN: stopping before any API call"); sys.exit()
# ---------- 2. launch-time features from Solana Tracker ----------
rows = []
for k, r in enumerate(runners + duds):
    r["label"] = 1 if r in runners else 0
    t = get(f"/tokens/{r['mint']}"); risk = t.get("risk") or {}
    bd = risk.get("bundlers") or {}; sn = risk.get("snipers") or {}; ins = risk.get("insiders") or {}
    r.update(bundlers=bd.get("count") or 0, bundled_init_pct=bd.get("totalInitialPercentage") or 0, snipers=sn.get("count") or 0, sniper_pct=sn.get("totalPercentage") or 0,
             insiders=ins.get("count") or 0, dev_pct=(risk.get("dev") or {}).get("percentage") or 0, top10=risk.get("top10") or 0, holders=t.get("holders") or 0, rugged=1 if risk.get("rugged") else 0,
             st_ok=0 if "_error" in t else 1)
    if r["label"] == 1 or random.random() < DUD_FB / max(1, N_DUD):
        fb = get(f"/first-buyers/{r['mint']}"); rr = fb if isinstance(fb, list) else fb.get("buyers") or fb.get("data") or []
        r["fb"] = [(x.get("wallet"), x.get("pnl") or x.get("total") or 0) for x in rr if isinstance(x, dict) and x.get("wallet")]
    rows.append(r)
    if k % 25 == 0: say("...", k, "of", len(runners) + len(duds), "| credits used this run", used[0])
ok = [r for r in rows if r["st_ok"]]
say("tokens with Solana Tracker data: %d of %d" % (len(ok), len(rows)))
with open(f"{OUT}/features.csv", "w", newline="", encoding="utf-8") as fh:
    cols = ["mint", "label", "peak_x", "launches", "grads", "init_sol", "b1", "s1", "v1", "mc1", "bundlers", "bundled_init_pct", "snipers", "sniper_pct", "insiders", "dev_pct", "top10", "holders", "rugged"]
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader(); [w.writerow(r) for r in rows]
# ---------- 3. which launch-time features separate runners ----------
say("\n===== A. LAUNCH FEATURES: runners vs duds (median) =====")
for k in ("launches", "grads", "init_sol", "b1", "s1", "v1", "mc1", "bundlers", "bundled_init_pct", "snipers", "sniper_pct", "insiders", "top10"):
    a = [r[k] for r in ok if r["label"]]; b = [r[k] for r in ok if not r["label"]]
    if a and b: say(f"{k:18s} runners {st.median(a):12.3f}   duds {st.median(b):12.3f}")
def prec(rule, label):
    R = sum(1 for r in ok if r["label"] and rule(r)); D = sum(1 for r in ok if not r["label"] and rule(r))
    if R + D < 5: return
    pop = R / (R + D * W) if R else 0
    base = len([1 for r in ok if r["label"]]) / (len([1 for r in ok if r["label"]]) + len([1 for r in ok if not r["label"]]) * W)
    say(f"{label:44s} runners {R:3d} duds {D:3d} -> P(runner | rule) ~{100*pop:5.2f}%  (base {100*base:.2f}%, lift {pop/base if base else 0:.1f}x)")
say("\n===== B. SINGLE RULES (population-corrected hit rate) =====")
for th in (1, 5, 20, 100): prec(lambda r, th=th: r["bundlers"] >= th, f"bundlers >= {th}")
for th in (0.5, 5, 20, 50): prec(lambda r, th=th: r["bundled_init_pct"] >= th, f"bundled at launch >= {th}% of supply")
for th in (1, 3): prec(lambda r, th=th: r["snipers"] >= th, f"snipers >= {th}")
prec(lambda r: r["bundlers"] == 0, "no bundlers")
for th in (1, 3): prec(lambda r, th=th: r["launches"] >= th and r["launches"] <= 15, f"creator launches {th}-15")
prec(lambda r: r["grads"] >= 1, "creator has a graduated coin")
for th in (6, 10, 20): prec(lambda r, th=th: r["b1"] >= th, f"buys >= {th} at 1 min")
for th in (0.5, 2): prec(lambda r, th=th: r["init_sol"] >= th, f"dev buy >= {th} SOL")
prec(lambda r: r["holders"] >= 50, "holders >= 50 (now)")
# ---------- 4. best 2-rule combos with time holdout ----------
say("\n===== C. BEST COMBINATIONS, tested on the LAST 30% of coins by time (not used to choose) =====")
rows_t = sorted(ok, key=lambda r: r["ts"]); cut = int(len(rows_t) * 0.7); trn, tst = rows_t[:cut], rows_t[cut:]
rules = {"bundlers>=5": lambda r: r["bundlers"] >= 5, "bundlers>=50": lambda r: r["bundlers"] >= 50, "bundled>=1%": lambda r: r["bundled_init_pct"] >= 1, "snipers>=1": lambda r: r["snipers"] >= 1,
         "launches3-15": lambda r: 3 <= r["launches"] <= 15, "grads>=1": lambda r: r["grads"] >= 1, "buys1m>=10": lambda r: r["b1"] >= 10, "devbuy>=0.5": lambda r: r["init_sol"] >= 0.5,
         "no_sniper": lambda r: r["snipers"] == 0, "holders>=50": lambda r: r["holders"] >= 50}
def score(rs, f):
    R = sum(1 for r in rs if r["label"] and f(r)); D = sum(1 for r in rs if not r["label"] and f(r)); return R, D
best = []
names = list(rules)
for i, a in enumerate(names):
    for b in names[i + 1:]:
        f = lambda r, a=a, b=b: rules[a](r) and rules[b](r); R, D = score(trn, f)
        if R >= 6: best.append((R / (R + D * W), a, b, R, D))
best.sort(reverse=True)
for p, a, b, R, D in best[:6]:
    f = lambda r, a=a, b=b: rules[a](r) and rules[b](r); R2, D2 = score(tst, f)
    say(f"{a} AND {b}: train {R}r/{D}d (P~{100*p:.1f}%) | HOLDOUT {R2} runners / {D2} duds -> P~{100*(R2/(R2+D2*W) if R2 else 0):.1f}%")
# ---------- 5. wallets early in many runners ----------
say("\n===== D. CLEAN WALLETS: early in many runners with real profit, rarely in duds =====")
try: import bot_wallets; bots = bot_wallets.load()
except Exception: bots = set()
wr = collections.defaultdict(list); wd = collections.Counter()
for r in rows:
    for wlt, pnl in (r.get("fb") or [])[:60]:
        if wlt in bots: continue
        if r["label"]: wr[wlt].append(pnl)
        else: wd[wlt] += 1
cl = []
for wlt, ps in wr.items():
    good = [p for p in ps if p > 100]
    if len(good) >= 3 and wd[wlt] <= 2: cl.append((wlt, len(ps), len(good), round(sum(good)), wd[wlt]))
cl.sort(key=lambda x: (-x[2], -x[3]))
with open(f"{OUT}/clean_wallets.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["wallet", "runners_seen", "runners_profitable_gt100", "total_profit_usd", "duds_seen"]); [w.writerow(x) for x in cl]
say("clean candidates (profitable early in >=3 runners, <=2 duds): %d" % len(cl))
for x in cl[:30]: say("  ", x[0], "| early in", x[1], "runners, profitable in", x[2], "| total $%d" % x[3], "| in %d duds" % x[4])
say("\nSolana Tracker credits used this run:", used[0]); say("DONE")

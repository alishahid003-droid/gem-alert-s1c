"""WALLET PROBE: is the wallet that was an early buyer in ~80 of 103 runners a real signal, or a selection illusion?
Takes a RANDOM sample of coins (all outcomes), fetches their first buyers (Solana Tracker, reuses runner_lab cache) and measures, for each candidate
wallet: how often it is early, and what happened to coins where it was early (2x/3x/5x rates and take-profit/stop-loss return after costs).
Run in NORMAL CMD:  python wallet_probe.py     (~250 Solana Tracker credits, ~10 min, resumable)   Output: wallet_probe/report.txt"""
import json, glob, os, sys, time, csv, random, collections, hashlib, urllib.request, urllib.error
OUT = "wallet_probe"; os.makedirs(OUT, exist_ok=True); CACHE = "runner_lab/cache"; os.makedirs(CACHE, exist_ok=True)
ST = "https://data.solanatracker.io"; SAMPLE = 300; CALL_CAP = 320; used = [0]; _last = [0.0]
def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
KEY = env("SOLANATRACKER_API_KEY")
rep = open(f"{OUT}/report.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); rep.write(t + "\n"); rep.flush()
def get(path):
    fn = f"{CACHE}/{hashlib.md5(path.encode()).hexdigest()}.json"
    if os.path.exists(fn):
        try: return json.load(open(fn, encoding="utf-8"))
        except Exception: pass
    if used[0] >= CALL_CAP or not KEY: return {"_error": "cap"}
    w = 0.6 - (time.time() - _last[0])
    if w > 0: time.sleep(w)
    _last[0] = time.time()
    for i in range(4):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(ST + path, headers={"x-api-key": KEY, "accept": "application/json"}), timeout=40))
            used[0] += 1; json.dump(d, open(fn, "w", encoding="utf-8")); return d
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(15 * (i + 1)); continue
            used[0] += 1; json.dump({"_error": f"HTTP {e.code}"}, open(fn, "w")); return {"_error": f"HTTP {e.code}"}
        except Exception: time.sleep(4)
    return {"_error": "net"}
cands = []
try: cands = [r["wallet"] for r in csv.DictReader(open("runner_lab/clean_wallets.csv", encoding="utf-8"))][:6]
except Exception: pass
if not cands: say("no runner_lab/clean_wallets.csv - run runner_lab.py first"); sys.exit()
S = collections.defaultdict(list); C = {}
for f in sorted(glob.glob("early_log/2026-10-1*.jsonl")):
    for l in open(f, errors="ignore"):
        try: r = json.loads(l)
        except Exception: continue
        if r["t"] == "snap": S[r["mint"]].append(r)
        elif r["t"] == "create" and r["mint"].endswith("pump"): C[r["mint"]] = r
pool = []
for m, L in S.items():
    if m not in C: continue
    L.sort(key=lambda r: r["age"]); ok = [r for r in L if r["mc"] and 0 < r["mc"] < 30e6]
    ent = [r for r in ok if 45 <= r["age"] <= 150]
    if not ent: continue
    e = ent[0]; path = [r["mc"] / e["mc"] for r in ok if r["age"] > e["age"]]
    if not path or max(path) > 200: continue
    pool.append((m, path, C[m]["ts"]))
say("coins in pool:", len(pool), "| candidate wallets:", [c[:8] for c in cands])
random.seed(11); samp = random.sample(pool, min(SAMPLE, len(pool)))
data = []
for k, (m, path, ts) in enumerate(samp):
    fb = get(f"/first-buyers/{m}"); rr = fb if isinstance(fb, list) else fb.get("buyers") or fb.get("data") or []
    ws = [x.get("wallet") for x in rr if isinstance(x, dict)]
    data.append((m, path, ws)); 
    if k % 50 == 0: say("...", k, "of", len(samp), "| credits", used[0])
def tpsl(path, tp, sl, cost):
    for x in path:
        if x >= tp: return tp * (1 - cost)
        if x <= sl: return sl * (1 - cost)
    return path[-1] * (1 - cost)
def report(label, sel):
    S_ = [(m, p) for m, p, w in data if sel(w)]
    if len(S_) < 3: say(f"{label:30s} coins where present: {len(S_)} (too few)"); return
    P = [p for _, p in S_]; n = len(P)
    h = lambda t: 100 * sum(max(p) >= t for p in P) / n
    ev = {c: max(sum(tpsl(p, tp, sl, c) for p in P) / n for tp in (2, 3, 5) for sl in (.5, .7)) for c in (.15, .08)}
    say(f"{label:30s} present in {n:3d} coins | 2x {h(2):4.0f}% 3x {h(3):4.0f}% 5x {h(5):4.0f}% | best TP/SL return after cost: 15% -> {ev[.15]:.2f}x  8% -> {ev[.08]:.2f}x")
say("\nBASE (random coins):"); report("ALL sampled coins", lambda w: True)
say("\nEACH CANDIDATE WALLET (present among first 100 buyers):")
for c in cands:
    report(c[:10] + "..", lambda w, c=c: c in w)
    report(c[:10] + ".. (rank<=5)", lambda w, c=c: c in w[:5])
report("ANY of the candidates", lambda w: any(c in w for c in cands))
report("ANY candidate rank<=5", lambda w: any(c in w[:5] for c in cands))
say("\nSolana Tracker credits used this run:", used[0]); say("DONE")

"""wallet_edge_builder.py - Ali's "insider/sniper" idea: find wallets that get into a coin before it runs,
across a MUCH bigger sample than the 55v100 in grad_runners.py section 5, with an actual significance check
(not just eyeballing a table). Answers: is there a small set of wallets who show up early in future 5x/10x
coins far more often than their overall base rate would predict, and rarely show up in duds?

RESUMABLE across days: everything it learns goes into grad_runners/wallet_db.json, which this script adds to
on every run instead of overwriting, so it's fine to run it once a day for a week to grow the sample instead
of burning the whole Solana Tracker credit budget in one run.

Run in the same folder as grad_runners.py, in a normal CMD:   python wallet_edge_builder.py
(set GR_ST_CAP=400 to spend more credits per run; default is conservative)
Output: grad_runners/wallet_edge_report.txt (send me this) and grad_runners/wallet_db.json (keep -- it's the
accumulated memory this rebuilds from next time)."""
import json, os, glob, random, hashlib, collections, time, urllib.request, urllib.error
OUT = "grad_runners"; CACHE = f"{OUT}/cache"; DB_PATH = f"{OUT}/wallet_db.json"
GT = "https://api.geckoterminal.com/api/v2"; STU = "https://data.solanatracker.io"
ST_CAP = int(os.environ.get("GR_ST_CAP", 250)); GAP = 3.2
SUPPLY = 1e9; RUNNER_X = 5.0; DUD_X = 1.5

def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
STK = env("SOLANATRACKER_API_KEY"); used = [0]; _last = [0.0]
import bot_wallets
rep = open(f"{OUT}/wallet_edge_report.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); rep.write(t + "\n"); rep.flush()

def get(url, headers=None, gap=GAP):
    fn = f"{CACHE}/{hashlib.md5(url.encode()).hexdigest()}.json"
    if os.path.exists(fn):
        try: return json.load(open(fn, encoding="utf-8"))
        except Exception: pass
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

def load_db():
    if os.path.exists(DB_PATH):
        try: return json.load(open(DB_PATH, encoding="utf-8"))
        except Exception: pass
    return {"checked_mints": [], "runner_mints": [], "dud_mints": [], "wallet": {}}

def save_db(db):
    json.dump(db, open(DB_PATH, "w", encoding="utf-8"), indent=0)

# ---------------- rebuild the same universe grad_runners.py uses (free, cache-only) ----------------
create = {}; grad = {}
for f in sorted(glob.glob("early_log/*.jsonl")):
    for l in open(f, errors="ignore"):
        try: r = json.loads(l)
        except Exception: continue
        t = r.get("t"); m = r.get("mint")
        if t == "create": create[m] = r
        elif t == "grad": grad.setdefault(m, r["ts"])
now = time.time()
pool_m = [m for m, ts in grad.items() if m in create and now - ts > 3 * 86400]
pool_m = [m for m in pool_m if os.path.exists(f"{CACHE}/{hashlib.md5((GT + '/networks/solana/tokens/' + m + '/pools?page=1').encode()).hexdigest()}.json")]
random.seed(7); random.shuffle(pool_m)
say(f"universe available (same cache grad_runners.py already built): {len(pool_m)} mints")

def peak_x(m):
    """Cache-only, must already exist from a prior grad_runners.py / grad_fast_top10.py run."""
    fn = f"{CACHE}/{hashlib.md5((GT + '/networks/solana/tokens/' + m + '/pools?page=1').encode()).hexdigest()}.json"
    d = get(f"{GT}/networks/solana/tokens/{m}/pools?page=1")
    ps = d.get("data") or []
    if not ps: return None
    ps.sort(key=lambda p: -float(p["attributes"].get("reserve_in_usd") or 0)); p = ps[0]
    pool = p["attributes"]["address"]
    c = get(f"{GT}/networks/solana/pools/{pool}/ohlcv/hour?aggregate=1&limit=1000&currency=usd")
    L = sorted((c.get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or [])
    if not L: return None
    G = grad[m]
    i0 = next((i for i, x in enumerate(L) if x[0] + 3600 > G), None)
    if i0 is None: return None
    eA = L[i0][1]
    win = L[i0:i0 + 14 * 24]
    if eA <= 0 or max(x[2] for x in win) / eA > 5000: return None
    return max(x[2] for x in win) / eA

db = load_db()
checked = set(db["checked_mints"])
unseen = [m for m in pool_m if m not in checked]
random.seed(23); random.shuffle(unseen)
say(f"already in wallet_db from prior runs: {len(checked)} mints | will check up to {len(unseen)} new ones this run (credit-capped)")

new_runners = new_duds = new_other = 0
for m in unseen:
    if used[0] >= ST_CAP - 1: break   # leave room for the first-buyers call itself
    px = peak_x(m)
    if px is None: continue
    label = "runner" if px >= RUNNER_X else ("dud" if px < DUD_X else None)
    if label is None:
        new_other += 1; checked.add(m); continue
    d = stget(f"/first-buyers/{m}")
    L = d if isinstance(d, list) else (d.get("buyers") or d.get("data") or []) if isinstance(d, dict) else []
    wallets = [x["wallet"] for x in L if isinstance(x, dict) and x.get("wallet")]
    if not wallets:
        continue   # didn't actually spend a fresh credit's worth of useful data; still mark checked below
    checked.add(m)
    if label == "runner":
        db["runner_mints"].append(m); new_runners += 1
    else:
        db["dud_mints"].append(m); new_duds += 1
    for w in set(wallets):
        rec = db["wallet"].setdefault(w, {"runner_hits": 0, "dud_hits": 0})
        rec["runner_hits" if label == "runner" else "dud_hits"] += 1

db["checked_mints"] = sorted(checked)
save_db(db)
say(f"this run: +{new_runners} runner mints, +{new_duds} dud mints checked (skipped {new_other} ambiguous, 1.5x-5x)")
say(f"Solana Tracker credits used this run: {used[0]} of cap {ST_CAP}")

n_runners = len(db["runner_mints"]); n_duds = len(db["dud_mints"]); n_total = n_runners + n_duds
say(f"\n================ ACCUMULATED SO FAR: {n_runners} runner mints, {n_duds} dud mints checked (total across every run of this script) ================")
if n_total < 60:
    say("Still too small a sample to trust any wallet list (under 60 labeled coins total). Run this again on a later day to grow it -- it picks up where it left off.")
else:
    base_rate = n_runners / n_total
    say(f"base rate: a random labeled coin in this sample is a runner {100*base_rate:.0f}% of the time")
    bots = bot_wallets.load()
    rows = []
    for w, rec in db["wallet"].items():
        if w in bots: continue
        n_seen = rec["runner_hits"] + rec["dud_hits"]
        if n_seen < 3: continue                                  # need to show up at least 3 times to say anything
        expected_runner_hits = n_seen * base_rate
        edge = rec["runner_hits"] - expected_runner_hits          # how many MORE runner-hits than base rate predicts
        if edge <= 0: continue
        rows.append((w, rec["runner_hits"], rec["dud_hits"], n_seen, round(edge, 1)))
    rows.sort(key=lambda r: -r[-1])
    say(f"\n================ CANDIDATE SMART WALLETS: more runner-list appearances than base rate predicts (bots excluded) ================")
    say("wallet | runner hits | dud hits | times seen | edge-over-base-rate")
    for w, rh, dh, n, e in rows[:30]:
        say(f"   {w} | {rh:3d} | {dh:3d} | {n:3d} | +{e}")
    confident = [r for r in rows if r[1] >= 5 and r[2] <= max(1, r[1] // 4)]
    say(f"\nwallets confident enough to watch live (>=5 runner hits, dud hits <= 1/4 of runner hits): {len(confident)}")
    with open(f"{OUT}/smart_wallets.csv", "w", newline="") as fh:
        import csv
        wr = csv.writer(fh); wr.writerow(["wallet", "runner_hits", "dud_hits", "seen", "edge"])
        for r in confident: wr.writerow(r)
    say("written to grad_runners/smart_wallets.csv -- layer19_smart_wallet_watch.py reads this file.")
    say("\nRead: this is early-buyer PRESENCE, not proof of foreknowledge -- some of this is just skilled/fast sniping,")
    say("not literal insider information, and a wallet can still be a bot even if not on bot_wallets.txt yet.")
    say("Treat this as a watchlist to validate live (layer19, alert-only), not something to trade on yet.")

say("\nDONE - send me grad_runners/wallet_edge_report.txt. Re-run this script on other days to grow the sample before trusting it.")

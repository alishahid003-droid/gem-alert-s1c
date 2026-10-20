"""grad_fast_top10.py - tests the one combined filter the grad_runners.py report flagged as worth trying:
fast graduation (0-5 min from launch) AND low top-10 holder concentration (the strongest single pattern
found: runners median top10=22%, duds median top10=80%). Question: does requiring BOTH push EV above 1.0?

Reuses grad_runners.py's own cache (GeckoTerminal price paths + Solana Tracker token data already on disk)
for zero cost, and only spends NEW Solana Tracker credits on fast-grad coins we haven't already looked up.
Run in the same folder as grad_runners.py, in a normal CMD:  python grad_fast_top10.py
Output: grad_runners/filter_test_report.txt (send me this)."""
import json, os, glob, random, hashlib, collections, statistics as st, time
OUT = "grad_runners"; CACHE = f"{OUT}/cache"
GT = "https://api.geckoterminal.com/api/v2"; STU = "https://data.solanatracker.io"
UNIV = int(os.environ.get("GR_UNIVERSE", 1000)); ST_CAP = int(os.environ.get("GR_ST_CAP", 350))
GAP = 3.2; SUPPLY = 1e9; COST = 0.15
FAST_MIN = float(os.environ.get("FAST_GRAD_MAX_MIN", 5))     # "fast graduation" bucket from the report
TOP10_MAX = float(os.environ.get("TOP10_MAX", 0.30))         # low concentration threshold to test (runner median was 0.22)
SAMPLE_NEW = int(os.environ.get("ST_SAMPLE_NEW", 280))       # how many NEW Solana Tracker lookups to spend on fast-grad coins

def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
STK = env("SOLANATRACKER_API_KEY"); used = [0]; _last = [0.0]
import urllib.request, urllib.error
rep = open(f"{OUT}/filter_test_report.txt", "w", encoding="utf-8")
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

# ---------------- rebuild the exact same 998-coin universe grad_runners.py built, from cache only (free, no new GT calls) ----------------
create = {}; grad = {}; snaps = collections.defaultdict(list)
for f in sorted(glob.glob("early_log/*.jsonl")):
    for l in open(f, errors="ignore"):
        try: r = json.loads(l)
        except Exception: continue
        t = r.get("t"); m = r.get("mint")
        if t == "create": create[m] = r
        elif t == "grad": grad.setdefault(m, r["ts"])
        elif t == "snap" and r.get("mc"): snaps[m].append(r)
now = time.time()
pool_m = [m for m, ts in grad.items() if m in create and now - ts > 3 * 86400]
pool_m = [m for m in pool_m if os.path.exists(f"{CACHE}/{hashlib.md5((GT + '/networks/solana/tokens/' + m + '/pools?page=1').encode()).hexdigest()}.json")]
random.seed(7); random.shuffle(pool_m); uni = pool_m[:UNIV]
say(f"universe (same seed as grad_runners.py, cache-only): {len(uni)} candidate mints")

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
for m in uni:
    P = path(m)
    if not P or len(P["candles"]) < 1: continue
    G = grad[m]; L = P["candles"]
    i0 = next((i for i, x in enumerate(L) if x[0] + 3600 > G), None)
    if i0 is None: continue
    eA = L[i0][1]
    winA = L[i0:i0 + 14 * 24]
    if eA <= 0 or max(x[2] for x in winA) / eA > 5000: continue
    pathA = [(x[2] / eA, x[3] / eA) for x in winA]; peakA = max(h for h, _ in pathA)
    cr = create[m]
    coins.append(dict(mint=m, grad_ts=G, entry_mc=round(eA * SUPPLY), path=pathA, peak_x=round(peakA, 2),
        mins_to_grad=round((G - cr["ts"]) / 60, 1)))
say(f"usable coins (price path rebuilt from cache): {len(coins)}")

def sim(sub, tp, sl):
    R = []
    for c in sub:
        out = None
        for hi, lo in c["path"]:
            if lo <= sl: out = sl; break
            if hi >= tp: out = tp; break
        if out is None: out = c["path"][-1][1]
        R.append(out * (1 - COST))
    return R

baseline_ev = sum(sim(coins, 3, .6)) / len(coins)
fast = [c for c in coins if c["mins_to_grad"] < FAST_MIN]
fast_ev = sum(sim(fast, 3, .6)) / len(fast) if fast else 0
say(f"\nbaseline (all {len(coins)} coins)        EV(TP3/SL.6) = {baseline_ev:.2f}")
say(f"fast-grad only (<{FAST_MIN:.0f}min, n={len(fast)})  EV(TP3/SL.6) = {fast_ev:.2f}")

# ---------------- spend NEW Solana Tracker credits on a sample of the fast-grad coins we haven't already looked up ----------------
already = [c for c in fast if os.path.exists(f"{CACHE}/{hashlib.md5((STU + '/tokens/' + c['mint']).encode()).hexdigest()}.json")]
need = [c for c in fast if c not in already]
random.seed(13); random.shuffle(need)
to_fetch = already + need[:max(0, SAMPLE_NEW)]
say(f"\nfast-grad coins with Solana Tracker data already cached: {len(already)} | fetching up to {SAMPLE_NEW} more new ones")

top10 = {}
for c in to_fetch:
    if used[0] >= ST_CAP: break
    t = stget(f"/tokens/{c['mint']}")
    if isinstance(t, dict) and "_error" not in t:
        risk = t.get("risk") or {}
        top10[c["mint"]] = risk.get("top10")
say(f"Solana Tracker credits used this run: {used[0]} of cap {ST_CAP}")
say(f"fast-grad coins with a usable top10 reading: {len(top10)}")

tested = [c for c in fast if c["mint"] in top10 and top10[c["mint"]] is not None]
if len(tested) < 20:
    say(f"\nToo few coins ({len(tested)}) got top10 data to test the combined filter -- re-run with ST_SAMPLE_NEW set higher, or on a later day once more credits are available.")
else:
    low = [c for c in tested if top10[c["mint"]] <= TOP10_MAX]
    high = [c for c in tested if top10[c["mint"]] > TOP10_MAX]
    say(f"\n================ COMBINED FILTER: fast-grad (<{FAST_MIN:.0f}min) + top10 concentration <= {TOP10_MAX:.0%} ================")
    say(f"of {len(tested)} fast-grad coins with top10 data: {len(low)} pass the low-concentration filter, {len(high)} don't")
    if low:
        ev_low = sum(sim(low, 3, .6)) / len(low)
        r3 = sum(c["peak_x"] >= 3 for c in low); r5 = sum(c["peak_x"] >= 5 for c in low)
        say(f"  PASS (top10<={TOP10_MAX:.0%}): n={len(low):3d}  >=3x {100*r3/len(low):4.1f}%  >=5x {100*r5/len(low):4.1f}%  EV(TP3/SL.6)={ev_low:.2f}")
        for tp, sl in ((2, .6), (5, .5), (10, .5)):
            ev = sum(sim(low, tp, sl)) / len(low)
            say(f"                                      EV(TP{tp}/SL{sl})={ev:.2f}")
    if high:
        ev_high = sum(sim(high, 3, .6)) / len(high)
        say(f"  FAIL (top10>{TOP10_MAX:.0%}):  n={len(high):3d}  EV(TP3/SL.6)={ev_high:.2f}   (control group -- should be worse if the filter means anything)")
    say(f"\nfor reference, baseline all-coins EV(TP3/SL.6) = {baseline_ev:.2f}, fast-grad-only EV = {fast_ev:.2f}")
    say("Read: this is still a 'does filtering help at all' check, not a final answer -- n is small, and top10 is read TODAY, not at the moment of graduation (the report flagged this).")

say("\nDONE - send me grad_runners/filter_test_report.txt")

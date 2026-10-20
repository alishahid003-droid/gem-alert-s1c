"""wallet_edge_validate.py - the REAL, leak-free test of the Layer 19 "smart wallet" idea, runnable RIGHT NOW
(reads only the first-buyers data already cached on disk by wallet_edge_builder.py -- zero new API calls, zero
wait). wallet_edge_builder.py's "confident" list was built and tested on the SAME 580 coins -- that number
describes how well the list fits its own training data, not whether it predicts anything new. This script does
a proper time-ordered split: build the wallet scores on the EARLIER labeled coins only, then check, on the
LATER labeled coins the wallets never saw, whether a "confident" wallet buying early still predicts a runner.
That is the honest answer to "does this actually work," available tonight instead of after days of live watching.

Run: python wallet_edge_validate.py   (same folder as grad_runners.py / wallet_edge_builder.py)
Output: grad_runners/wallet_edge_validation.txt
"""
import json, os, glob, hashlib, time
OUT = "grad_runners"; CACHE = f"{OUT}/cache"; DB_PATH = f"{OUT}/wallet_db.json"
STU = "https://data.solanatracker.io"
rep = open(f"{OUT}/wallet_edge_validation.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t, flush=True); rep.write(t + "\n"); rep.flush()

def cached_first_buyers(mint):
    fn = f"{CACHE}/{hashlib.md5((STU + f'/first-buyers/{mint}').encode()).hexdigest()}.json"
    if not os.path.exists(fn):
        return None
    try:
        d = json.load(open(fn, encoding="utf-8"))
    except Exception:
        return None
    L = d if isinstance(d, list) else (d.get("buyers") or d.get("data") or []) if isinstance(d, dict) else []
    return [x["wallet"] for x in L if isinstance(x, dict) and x.get("wallet")]

db = json.load(open(DB_PATH, encoding="utf-8"))
labeled = {m: "runner" for m in db["runner_mints"]}
labeled.update({m: "dud" for m in db["dud_mints"]})
say(f"labeled coins in wallet_db: {len(labeled)} ({len(db['runner_mints'])} runner, {len(db['dud_mints'])} dud)")

# grad timestamps for time-ordering (free, from early_log -- same source wallet_edge_builder.py used)
grad = {}
for f in sorted(glob.glob("early_log/*.jsonl")):
    for l in open(f, errors="ignore"):
        try: r = json.loads(l)
        except Exception: continue
        if r.get("t") == "grad" and r.get("mint"):
            grad.setdefault(r["mint"], r["ts"])

ordered = sorted([m for m in labeled if m in grad], key=lambda m: grad[m])
say(f"labeled coins with a known graduation time: {len(ordered)}")
split_i = int(len(ordered) * 0.70)
train_mints, test_mints = ordered[:split_i], ordered[split_i:]
say(f"TRAIN (earlier, chronologically): {len(train_mints)} coins | TEST (later, held out): {len(test_mints)} coins")

# rebuild first-buyers from disk cache only -- same data wallet_edge_builder.py already paid for, no new credits
import bot_wallets
bots = bot_wallets.load()

def hits_for(mints):
    wallet_stats = {}
    n_runner_seen = n_total = 0
    missing = 0
    for m in mints:
        buyers = cached_first_buyers(m)
        if buyers is None:
            missing += 1; continue
        n_total += 1
        is_runner = labeled[m] == "runner"
        n_runner_seen += is_runner
        for w in set(buyers):
            if w in bots: continue
            rec = wallet_stats.setdefault(w, {"runner_hits": 0, "dud_hits": 0})
            rec["runner_hits" if is_runner else "dud_hits"] += 1
    return wallet_stats, n_runner_seen, n_total, missing

train_wallets, train_runners, train_total, train_missing = hits_for(train_mints)
say(f"TRAIN: usable (cached) {train_total}/{len(train_mints)} ({train_missing} not in disk cache) | base rate {100*train_runners/train_total:.0f}% runner" if train_total else "TRAIN: no cached data")

# "confident" wallets, built ONLY from TRAIN -- exact same rule wallet_edge_builder.py uses
confident = {w for w, r in train_wallets.items()
             if r["runner_hits"] >= 5 and r["dud_hits"] <= max(1, r["runner_hits"] // 4)}
say(f"confident wallets from TRAIN only: {len(confident)} (vs {sum(1 for r in train_wallets.values() if r['runner_hits']+r['dud_hits']>=3)} candidates with >=3 sightings)")

# now the real test: on TEST coins the wallets never touched, does a confident-wallet hit predict runner?
hit_mints, miss_mints, test_missing = [], [], 0
for m in test_mints:
    buyers = cached_first_buyers(m)
    if buyers is None:
        test_missing += 1; continue
    (hit_mints if confident & set(buyers) else miss_mints).append(m)

def rate(mints):
    if not mints: return None, 0
    r = sum(1 for m in mints if labeled[m] == "runner")
    return r / len(mints), len(mints)

hit_rate, n_hit = rate(hit_mints)
miss_rate, n_miss = rate(miss_mints)
base_rate, n_test = rate(hit_mints + miss_mints)
say(f"\n================ HELD-OUT TEST (coins the wallet list never saw) ================")
say(f"TEST coins usable: {n_test} ({test_missing} not in disk cache)")
say(f"  coins where >=1 confident wallet was an early buyer: n={n_hit:3d}  runner rate = {hit_rate*100:.0f}%" if n_hit else "  coins with a confident-wallet hit: 0 -- cannot test (list too small / sample too small)")
say(f"  coins with no confident wallet among early buyers:  n={n_miss:3d}  runner rate = {miss_rate*100:.0f}%" if n_miss else "  coins with no confident-wallet hit: 0")
if n_hit and n_miss:
    lift = hit_rate - miss_rate
    say(f"\nLIFT (held-out, honest): {lift*100:+.0f} points ({hit_rate*100:.0f}% vs {miss_rate*100:.0f}% base)")
    if n_hit < 20:
        say(f"CAVEAT: only {n_hit} held-out coins actually had a confident-wallet hit -- this lift number has a huge")
        say("margin of error at this sample size. Treat the SIGN (does hit-rate beat miss-rate at all) as the finding,")
        say("not the exact number, until the sample grows past ~50 hit-coins.")
    if lift > 0:
        say("READ: the edge survives a genuine out-of-sample check -- worth continuing to validate live via layer19.")
    else:
        say("READ: the edge does NOT survive out-of-sample -- the wallet_edge_builder.py 'confident' list is likely")
        say("overfit to its own training data (see wallet_edge_report.txt: 250/580 wallets flagged 'confident' is a")
        say("very loose filter). Do not trust smart_wallets.csv as-is; it needs a stricter filter before layer19 is")
        say("worth running live.")
else:
    say("\nNot enough held-out hits to measure a lift yet -- need a bigger labeled sample (keep running")
    say("wallet_edge_builder.py) before this validation means anything.")
say("\nDONE - send me grad_runners/wallet_edge_validation.txt")

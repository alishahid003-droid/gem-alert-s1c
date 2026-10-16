"""Find the big winners of the last ~6 months across Solana / Base / BSC / Robinhood chain, by launch platform, and measure how they moved.
Run in NORMAL CMD AFTER history_study.py prints DONE:   python boomers.py        (~15 min, free)
Output: boomers/boomers.csv  boomers/report.txt"""
import json, os, sys, time, csv, collections, datetime, urllib.request, urllib.parse
OUT = "boomers"; os.makedirs(OUT, exist_ok=True)
GT = "https://api.geckoterminal.com/api/v2"; DAYS = 185; MIN_FDV = float(os.environ.get("BOOM_MIN_FDV", 3e6)); PAGES = 5
_last = [0.0]
def get(url, gap=2.3):
    w = gap - (time.time() - _last[0])
    if w > 0: time.sleep(w)
    _last[0] = time.time()
    for i in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(25); continue
            return {}
        except Exception: time.sleep(3)
    return {}
def now(): return datetime.datetime.utcnow()
def parse(t):
    try: return datetime.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception: return None
rep = open(f"{OUT}/report.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t); rep.write(t + "\n"); rep.flush()
nets = [n["id"] for p in (1, 2) for n in get(f"{GT}/networks?page={p}").get("data", [])]
targets = [n for n in nets if n in ("solana", "base", "bsc") or "robin" in n]
say("chains:", targets)
cands = {}
def add(p, net):
    a = p["attributes"]; c = parse(a.get("pool_created_at") or "")
    if not c or (now() - c).days > DAYS: return
    fdv = float(a.get("fdv_usd") or 0)
    if fdv < MIN_FDV: return
    rel = p.get("relationships", {})
    cands[a["address"]] = dict(net=net, pool=a["address"], name=a.get("name"), dex=rel.get("dex", {}).get("data", {}).get("id"),
                               base=rel.get("base_token", {}).get("data", {}).get("id", "").split("_", 1)[-1], created=c, fdv=fdv,
                               liq=float(a.get("reserve_in_usd") or 0), vol24=float((a.get("volume_usd") or {}).get("h24") or 0))
for net in targets:
    dexes = [d["id"] for d in get(f"{GT}/networks/{net}/dexes?page=1").get("data", [])]
    say(net, "dexes:", dexes[:30])
    for dx in dexes[:14]:
        for pg in range(1, PAGES + 1):
            d = get(f"{GT}/networks/{net}/dexes/{dx}/pools?page={pg}").get("data", [])
            for p in d: add(p, net)
            if len(d) < 20: break
    for sort in ("h24_volume_usd_desc", "h24_tx_count_desc"):
        for pg in range(1, 6):
            for p in get(f"{GT}/networks/{net}/pools?page={pg}&sort={sort}").get("data", []): add(p, net)
    d = get(f"{GT}/networks/{net}/trending_pools?duration=24h&page=1")
    for p in d.get("data", []): add(p, net)
say("candidate winners (launched <=%d days ago, FDV>=$%d): %d" % (DAYS, MIN_FDV, len(cands)))
def candles(net, pool, frame):
    return sorted((get(f"{GT}/networks/{net}/pools/{pool}/ohlcv/{frame}?aggregate=1&limit=1000&currency=usd").get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or [])
rows = []
for k, c in enumerate(sorted(cands.values(), key=lambda c: -c["fdv"])[:140]):
    day = candles(c["net"], c["pool"], "day"); first = candles(c["net"], c["pool"], "minute") if (now() - c["created"]).days >= 0 else []
    if not day: continue
    px = day[-1][4]; sup = c["fdv"] / px if px else 0
    f = day[0]; born = f[1] * sup
    peak = max(day, key=lambda x: x[2]); pkf = peak[2] * sup
    e50 = next((x for x in day if x[4] * sup >= 5e4), None); e50v = e50[4] * sup if e50 else None
    mult50 = round(peak[2] / e50[4], 1) if e50 and peak[0] >= e50[0] else None
    rows.append(dict(name=c["name"], net=c["net"], dex=c["dex"], created=str(c["created"])[:10], born_fdv=round(born), first_day_high_fdv=round(f[2] * sup),
                     peak_fdv=round(pkf), peak_day=datetime.datetime.utcfromtimestamp(peak[0]).strftime("%Y-%m-%d"), fdv_now=round(c["fdv"]),
                     day_of_peak=(peak[0] - day[0][0]) // 86400, mult_from_50k_close=mult50, token=c["base"], pool=c["pool"], liq=round(c["liq"])))
    if k % 10 == 0: say("...", k, "analysed")
with open(f"{OUT}/boomers.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["none"]); w.writeheader(); [w.writerow(r) for r in rows]
say("\n===== BY CHAIN / PLATFORM: how many winners, how they were born, how fast they peaked =====")
g = collections.defaultdict(list)
for r in rows: g[(r["net"], r["dex"])].append(r)
import statistics as st
for (net, dx), L in sorted(g.items(), key=lambda x: -len(x[1])):
    say(f"{net:8s} {str(dx):22s} winners={len(L):3d} | born FDV median ${int(st.median(r['born_fdv'] for r in L)):,} | peak FDV median ${int(st.median(r['peak_fdv'] for r in L)):,} | days to peak median {int(st.median(r['day_of_peak'] for r in L))}")
say("\n===== Where did they start? (born FDV buckets) =====")
b = collections.Counter()
for r in rows:
    v = r["born_fdv"]; b["<10k" if v < 1e4 else "10-50k" if v < 5e4 else "50-200k" if v < 2e5 else ">200k"] += 1
say(dict(b))
say("\n===== TOP 40 by peak FDV =====")
for r in sorted(rows, key=lambda r: -r["peak_fdv"])[:40]:
    say(f"{str(r['name'])[:28]:28s} {r['net']:7s} {str(r['dex'])[:18]:18s} born ${r['born_fdv']:>10,} peak ${r['peak_fdv']:>13,} day {r['day_of_peak']:>3} x-from-50k {r['mult_from_50k_close']}  created {r['created']}")
say("DONE")

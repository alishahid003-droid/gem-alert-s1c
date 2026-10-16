"""Boomer history study. Run in NORMAL CMD:  python history_study.py   (about 5 min, free GeckoTerminal + a few Solana Tracker credits)
Writes history_study/*.json and history_study/summary.txt. Do not close the window until it says DONE."""
import json, os, sys, time, urllib.request, urllib.parse, datetime
OUT = "history_study"; os.makedirs(OUT, exist_ok=True)
NAMES = sys.argv[1:] or ["PONS", "MARS", "USELESS", "STONK", "SHROOM", "FEELS GOOD", "JOHN PHIL", "CLAWS", "PAWN", "CLAUDIA", "WHALE"]
GT = "https://api.geckoterminal.com/api/v2"; ST = "https://data.solanatracker.io"
def env(k):
    try:
        for l in open(".env", errors="ignore"):
            if l.startswith(k + "="): return l.split("=", 1)[1].strip().strip('"')
    except Exception: pass
    return os.environ.get(k, "")
STK = env("SOLANATRACKER_API_KEY"); st_calls = 0
_last = [0.0]
def get(url, headers=None, gap=2.3):
    w = gap - (time.time() - _last[0])
    if w > 0: time.sleep(w)
    _last[0] = time.time()
    h = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}; h.update(headers or {})
    for i in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(20); continue
            return {"_error": f"HTTP {e.code}"}
        except Exception as e:
            time.sleep(3); err = str(e)
    return {"_error": err}
def ts(t): return datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M")
def pool_info(p):
    a = p["attributes"]; rel = p.get("relationships", {})
    net = p["id"].split("_")[0]
    return dict(net=net, pool=a["address"], name=a.get("name"), dex=rel.get("dex", {}).get("data", {}).get("id"),
                base=rel.get("base_token", {}).get("data", {}).get("id", "").split("_", 1)[-1],
                created=a.get("pool_created_at"), fdv=float(a.get("fdv_usd") or 0), liq=float(a.get("reserve_in_usd") or 0),
                vol24=float((a.get("volume_usd") or {}).get("h24") or 0))
def candles(net, pool, frame, n=1000):
    d = get(f"{GT}/networks/{net}/pools/{pool}/ohlcv/{frame}?aggregate=1&limit={n}&currency=usd")
    L = (d.get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or []
    return sorted(L)           # [t,o,h,l,c,v]
def analyse(info):
    day = candles(info["net"], info["pool"], "day"); hour = candles(info["net"], info["pool"], "hour")
    cs = hour if hour else day
    res = {}
    if not cs: return res
    price_now = cs[-1][4]
    supply = info["fdv"] / price_now if price_now else 0
    res["first_candle_time"] = ts(cs[0][0]); res["first_candle_fdv"] = round(cs[0][1] * supply)
    peak = max(cs, key=lambda c: c[2]); res["peak_fdv"] = round(peak[2] * supply); res["peak_time"] = ts(peak[0])
    for thr in (50e3, 100e3):
        hit = next((c for c in cs if c[4] * supply >= thr), None)
        if hit:
            after = [c for c in cs if c[0] >= hit[0]]
            pk = max(after, key=lambda c: c[2])
            res[f"first_close_ge_{int(thr/1000)}k"] = ts(hit[0]); res[f"peak_multiple_from_{int(thr/1000)}k"] = round(pk[2] / hit[4], 1)
            res[f"hours_to_peak_from_{int(thr/1000)}k"] = round((pk[0] - hit[0]) / 3600, 1)
            low = min(c[3] for c in after if c[0] <= pk[0]); res[f"worst_dip_before_peak_{int(thr/1000)}k"] = round(low / hit[4], 2)
    res["candles"] = len(cs); res["frame"] = "hour" if hour else "day"
    return res
def solana_extra(mint):
    global st_calls
    out = {}
    if not STK or st_calls > 40: return out
    H = {"x-api-key": STK}
    out["token"] = get(f"{ST}/tokens/{mint}", H); st_calls += 1
    out["first_buyers"] = get(f"{ST}/first-buyers/{mint}", H); st_calls += 1
    return out
S = open(f"{OUT}/summary.txt", "w", encoding="utf-8")
def say(*a):
    t = " ".join(str(x) for x in a); print(t); S.write(t + "\n"); S.flush()
nets = get(f"{GT}/networks?page=1").get("data", []) + get(f"{GT}/networks?page=2").get("data", [])
say("networks:", [n["id"] for n in nets][:60])
cases = []
for nm in NAMES:
    d = get(f"{GT}/search/pools?query={urllib.parse.quote(nm)}&page=1")
    ps = [pool_info(p) for p in d.get("data", [])]
    key = nm.lower().replace(" ", "")
    ps = [p for p in ps if key in (p["name"] or "").lower().replace(" ", "")] or ps
    ps.sort(key=lambda p: -(p["liq"] + p["vol24"]))
    if not ps: say("NOT FOUND:", nm); continue
    cases.append((nm, ps[0], ps[1:4]))
# discovery: today's bigger movers on each network = more examples of how boomers look
for net in [n["id"] for n in nets if n["id"] in ("solana", "base", "bsc") or "robin" in n["id"]]:
    d = get(f"{GT}/networks/{net}/trending_pools?duration=24h&page=1")
    for p in d.get("data", [])[:6]:
        i = pool_info(p); cases.append((f"trend:{i['name']}", i, []))
say("cases", len(cases))
for label, info, alts in cases:
    r = analyse(info)
    ex = solana_extra(info["base"]) if info["net"] == "solana" and not label.startswith("trend:") else {}
    rec = dict(label=label, info=info, stats=r, alts=alts, extra=ex)
    json.dump(rec, open(f"{OUT}/{label.replace(':','_').replace('/','_').replace(' ','_')[:60]}.json", "w"), default=str)
    say("="*70); say(label, "|", info["net"], "| dex:", info["dex"], "| pool created:", info["created"], "| fdv now $%d" % info["fdv"], "| liq $%d" % info["liq"])
    say("  ", r)
    t = (ex.get("token") or {})
    if t and "_error" not in t:
        risk = t.get("risk") or {}
        say("   launchpad pools:", [(p.get("market"), p.get("createdAt")) for p in (t.get("pools") or [])][:3])
        say("   creator:", (t.get("token") or {}).get("creator") or t.get("creator"), "| risk:", {k: risk.get(k) for k in ("score", "snipers", "insiders", "top10", "rugged", "bundlers", "dev") if k in risk})
    fb = (ex.get("first_buyers") or {})
    rows = fb if isinstance(fb, list) else fb.get("buyers") or fb.get("data") or []
    if rows:
        top = sorted(rows, key=lambda x: -(x.get("pnl") or x.get("total") or 0))[:5] if isinstance(rows[0], dict) else rows[:5]
        say("   first buyers (top by pnl):", [(str(x.get("wallet"))[:8], round(x.get("pnl") or x.get("total") or 0)) for x in top])
say("DONE")

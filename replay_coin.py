"""replay_coin.py - "what would our sniper have done on this coin?"

Usage (CMD, in the project folder):
    python replay_coin.py <token_address> [<token_address> ...]

For each Solana token it pulls the coin's own history from GeckoTerminal (free, 2 calls per coin, paced),
replays it minute by minute through the SAME entry rules multichain_sniper.py uses, and reports:
  - launchpad / pool, launch time, peak market cap
  - minute at which it first crossed 10k, 15k, 20k, 40k, 60k, 80k, 250k
  - the FIRST minute the sniper would have fired and at what market cap, in two scenarios:
        STRICT  only confirmations that candles can measure (volume, velocity, fresh, growth)
        PROXY   strict + buyers/pressure assumed present when 5-min volume >= MC_MIN_VOL_USD
  - after the entry: peak multiple, deepest dip before the first 2x, minutes to peak
Results are cached in logs\\replay_cache.json so re-runs cost no API calls.

Limits (honest): candles carry price and volume only, not per-wallet buyer counts, so PROXY is an estimate.
"""
import os, sys, time, json, datetime

try:
    import requests
except ImportError:  # pragma: no cover
    print("pip install requests"); sys.exit(1)

GT = "https://api.geckoterminal.com/api/v2"
LEVELS = (10_000, 15_000, 20_000, 40_000, 60_000, 80_000, 250_000)
PAUSE = 11          # seconds between GeckoTerminal calls (free tier ~6 ok calls/min from a home IP)
CACHE = os.path.join("logs", "replay_cache.json")


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _get(path, params=None, tries=6):
    for i in range(tries):
        try:
            r = requests.get(GT + path, params=params, timeout=25,
                             headers={"Accept": "application/json;version=20230302"})
        except requests.RequestException as e:
            print("   network error:", e); time.sleep(10); continue
        if r.status_code == 429:
            wait = 20 + 10 * i
            print(f"   rate-limited, waiting {wait}s ..."); time.sleep(wait); continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    return None


def _iso(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


COST = 0.90          # paper ledger charges a 10% round-trip cost; proceeds are multiplied by this


def _exit_rules(lane):
    try:
        from executor import paper_ledger as pl
        return pl.EARLY_RULES.get(lane) or pl.EARLY_RULES["core1"]
    except Exception:  # noqa: BLE001
        return ({2: 0.40, 4: 0.25, 10: 0.15}, 0.55, 0.60)


def simulate_exit(candles, supply, i, lane="core2", stop=None, dead_min=20, max_hold_min=1440):
    """Replays the live exit engine (ladder / hard stop / trail after 2x / dead-coin time stop) on 1-minute
    candles for a position opened at candle i's close. Conservative: inside a candle the LOW is tested before
    the HIGH. Returns (realized multiple of the stake after the 10% cost, exit reason)."""
    ladder, hard, trail = _exit_rules(lane)
    stop = hard if stop is None else stop
    entry = candles[i][4] * supply
    rem, got, peak, fired = 1.0, 0.0, 1.0, set()
    for j in range(i + 1, len(candles)):
        hi, lo, cl = (candles[j][k] * supply / entry for k in (2, 3, 4))
        age = (candles[j][0] - candles[i][0]) / 60.0
        if peak < 2.0 and lo <= stop:
            return round((got + rem * stop) * COST, 3), "stop"
        if peak >= 2.0 and lo <= peak * trail:
            return round((got + rem * peak * trail) * COST, 3), "trail"
        peak = max(peak, hi)
        for tier, frac in sorted(ladder.items()):
            if tier not in fired and hi >= tier and rem > 1e-9:
                take = min(frac, rem)
                got += take * tier; rem -= take; fired.add(tier)
        if age >= dead_min and peak < 1.5 and cl < 1.2:
            return round((got + rem * cl) * COST, 3), "dead"
        if age >= max_hold_min:
            return round((got + rem * cl) * COST, 3), "max_hold"
    last = candles[-1][4] * supply / entry
    return round((got + rem * last) * COST, 3), "end_of_data"


def replay(candles, supply, created_ts, chain="solana"):
    """candles: [[ts,o,h,l,c,v],...] oldest first, 1-minute. Pure function (unit-tested)."""
    cap = _f("MC_MAX_MCAP_USD", 60_000)
    mom_cap = _f("MC_MOMENTUM_MAX_MCAP_USD", 250_000)
    min_mc = _f("MC_MIN_MCAP_USD", 3000)
    max_age = _f("MC_MAX_AGE_MIN", 60)
    min_conf = int(_f("MC_MIN_CONF", 3))
    min_c5, max_c5 = _f("MC_MIN_CHG5", 15), _f("MC_MAX_CHG5", 400)
    min_vol = _f("MC_MIN_VOL_USD", 3000)
    out = {"crossed": {}, "fired": {"strict": None, "proxy": None}, "peak": 0.0, "why": {}, "after": {}}
    mc = [(c[0], c[4] * supply) for c in candles]
    for ts, m in mc:
        out["peak"] = max(out["peak"], m)
        for lv in LEVELS:
            if m >= lv and lv not in out["crossed"]:
                out["crossed"][lv] = ((ts - created_ts) / 60.0, m)
    blockers = {"strict": {}, "proxy": {}}
    for i in range(5, len(candles)):
        ts, m = mc[i]
        age = (ts - created_ts) / 60.0
        vol5 = sum(c[5] for c in candles[i - 4:i + 1])
        vol5_prev = sum(c[5] for c in candles[max(0, i - 9):i - 4]) or 1.0
        base = mc[i - 5][1]
        chg5 = (m / base - 1) * 100 if base else None
        momentum = chain == "solana" and cap < m <= mom_cap       # live code: momentum bucket is Solana-only
        for mode in ("strict", "proxy"):
            if out["fired"][mode]:
                continue
            why = None
            if m < min_mc:
                why = "below min mcap"
            elif m > mom_cap or (m > cap and chain != "solana"):
                why = "above momentum cap (>250k)" if chain == "solana" else f"above cap (>{cap/1000:.0f}k, non-Solana)"
            elif age > max_age:
                why = f"older than {max_age:.0f} min (age cap)"
            elif chg5 is not None and chg5 > max_c5:
                why = "already exploded (>400% in 5m)"
            conf = []
            if chg5 is not None and min_c5 <= chg5 <= max_c5:
                conf.append("velocity")
            if vol5 >= min_vol:
                conf.append("volume")
            if age <= 15:
                conf.append("fresh")
            if i >= 3 and mc[i - 3][1] and m >= 1.25 * mc[i - 3][1]:
                conf.append("growth")
            if mode == "proxy" and vol5 >= min_vol and chg5 is not None and chg5 > 0:
                conf += ["buyers", "pressure"]
            need = 4 if momentum else min_conf
            if not why and len(conf) < need:
                why = f"only {len(conf)} confirmations ({', '.join(conf) or 'none'}), need {need}"
            if why:
                blockers[mode][why] = blockers[mode].get(why, 0) + 1
                continue
            # fired: measure what happened afterwards
            entry = m
            peak, peak_i, dip, hit2x = entry, i, 1.0, False
            for j in range(i + 1, len(candles)):
                hi, lo = candles[j][2] * supply, candles[j][3] * supply
                if hi > peak:
                    peak, peak_i = hi, j
                if not hit2x:
                    dip = min(dip, lo / entry)
                    if hi >= 2 * entry:
                        hit2x = True
            out["fired"][mode] = {
                "minute": round(age, 1), "mcap": round(m), "conf": conf, "momentum": momentum,
                "vol5": round(vol5), "chg5": None if chg5 is None else round(chg5, 1),
                "vol_ratio": round(vol5 / vol5_prev, 2), "n_conf": len(conf),
                "peak_mult": round(peak / entry, 2), "dip_before_2x": round(dip, 2), "hit_2x": hit2x,
                "min_to_peak": round((candles[peak_i][0] - ts) / 60.0),
                "exits": {str(sv): simulate_exit(candles, supply, i, "core2", sv)[0] for sv in (0.35, 0.45, 0.55, 0.70)},
                "exit_default": simulate_exit(candles, supply, i, "core2")[1],
            }
    out["why"] = {k: sorted(v.items(), key=lambda kv: -kv[1])[:3] for k, v in blockers.items()}
    return out


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(c):
    try:
        os.makedirs("logs", exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(c, fh)
    except OSError:
        pass


def _build(meta, candles, chain):
    """meta + raw candles -> result dict. Pure: re-running after a rule change costs no API calls."""
    res = dict(meta)
    if len(candles) < 10:
        res["ok"], res["error"] = False, f"only {len(candles)} candles (launch curve phase not indexed?)"
        return res
    r = replay(candles, meta["supply"], meta["created_ts"], chain)
    res.update({"ok": True, "peak": round(r["peak"]),
                "crossed": {str(k): round(v[0]) for k, v in r["crossed"].items()},
                "fired": r["fired"], "why": r["why"]})
    return json.loads(json.dumps(res))


def analyze(addr, sleep=time.sleep, use_cache=True, chain="solana"):
    """Fetch + replay one token (chain = GeckoTerminal network slug: solana, bsc, robinhood ...). Never raises.
    Raw candles are cached (logs/replay_cache.json) so rule changes re-replay offline."""
    cache = _load_cache() if use_cache else {}
    key = addr if chain == "solana" else f"{chain}:{addr}"
    hit = cache.get(key)
    if hit and hit.get("v") == 3:
        return _build(hit["meta"], hit.get("candles", []), chain) if hit.get("candles") is not None else hit["meta"]
    meta = {"addr": addr, "chain": chain, "ok": False}
    candles = None
    try:
        pools = _get(f"/networks/{chain}/tokens/{addr}/pools", {"page": 1})
        sleep(PAUSE)
        if not pools or not pools.get("data"):
            meta["error"] = "not found on GeckoTerminal"
        else:
            rows = []
            for p in pools["data"]:
                a = p["attributes"]
                rows.append((_iso(a.get("pool_created_at")) or 0, p["id"].split("_", 1)[-1], a,
                             (p.get("relationships", {}).get("dex", {}).get("data") or {}).get("id")))
            rows.sort(key=lambda r: r[0])
            supply, now_mc = None, 0.0
            for _, _, a, _ in rows:
                try:
                    pr, fdv = float(a.get("base_token_price_usd")), float(a.get("fdv_usd"))
                    if pr > 0 and fdv > 0:
                        supply, now_mc = fdv / pr, fdv
                        break
                except (TypeError, ValueError):
                    continue
            supply = supply or 1_000_000_000
            created, pool, a0, dex0 = rows[0]
            d = _get(f"/networks/{chain}/pools/{pool}/ohlcv/minute",
                     {"aggregate": 1, "limit": 1000, "before_timestamp": int(created + 1000 * 60), "currency": "usd"})
            sleep(PAUSE)
            cl = ((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
            candles = sorted(cl, key=lambda c: c[0])
            meta.update({"symbol": (a0.get("name") or addr[:6]).split(" / ")[0], "dex": dex0,
                         "dexes": sorted({r[3] for r in rows if r[3]}), "created_ts": created,
                         "created": datetime.datetime.utcfromtimestamp(created).strftime("%Y-%m-%d %H:%M"),
                         "supply": round(supply), "mcap_now": round(now_mc), "candles": len(candles)})
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        meta["error"] = ("GeckoTerminal free plan will not serve history this old (HTTP 401)" if "401" in msg else msg)
    res = _build(meta, candles, chain) if candles is not None else json.loads(json.dumps(meta))
    if use_cache and (candles is not None or meta.get("error", "").startswith("not found")):
        cache[key] = {"v": 3, "meta": meta, "candles": candles}
        _save_cache(cache)
    return res


def show(res, say=print):
    say("=" * 70)
    say(f"TOKEN {res['addr']}")
    if not res.get("ok"):
        say(f"   {res.get('symbol', '')} could not be replayed: {res.get('error')}"); return
    say(f"   {res['symbol']} [{res.get('chain', 'solana')}]  launchpad/dex={res['dex']}  launched {res['created']} UTC  peak(window) ${res['peak']:,}  now ~${res['mcap_now']:,}")
    say("   minutes after launch when mcap FIRST crossed: " +
        ", ".join(f"${int(k)//1000}k@{v}m" for k, v in res["crossed"].items()))
    for mode, label in (("strict", "STRICT"), ("proxy", "PROXY ")):
        f = res["fired"][mode]
        if f:
            say(f"   {label}: FIRES minute {f['minute']:.0f} at ${f['mcap']:,}{' [momentum]' if f['momentum'] else ''}"
                f" | then peak x{f['peak_mult']}, deepest dip before 2x {f['dip_before_2x']:.2f}x, "
                f"{'reached 2x' if f['hit_2x'] else 'never 2x'}, peak {f['min_to_peak']} min later | {', '.join(f['conf'])}")
            if mode == "proxy" and f.get("exits"):
                say("          realized return on the stake by hard-stop level (after 10% cost): " +
                    ", ".join(f"stop {k}: x{v}" for k, v in f["exits"].items()))
        else:
            say(f"   {label}: does NOT fire. Top blockers: {res['why'][mode]}")


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    os.makedirs("logs", exist_ok=True)
    for a in sys.argv[1:]:
        res = analyze(a.strip())
        lines = []
        show(res, lambda s: (print(s), lines.append(s)))
        try:
            with open(os.path.join("logs", f"replay_{res.get('symbol') or a[:6]}.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except OSError:
            pass


if __name__ == "__main__":
    main()

"""Fast multi-chain sniper: Solana (non-pump launchpads incl. StonkFun/LaunchLab), BSC, Base, Robinhood Chain.

Same lanes, books, stakes and EXIT ENGINE as the pump.fun sniper (executor.paper_ledger early rules,
books Core 1/2/3 + Compound), but fed by price-action instead of wallet data:
  * GeckoTerminal new_pools per chain (free, keyless, 30 calls/min -> we use ~12/min)
  * StonkFun's own keyless /tokens?sort=newest feed (pre-graduation launches with market data)
Entry = confirmations on live pool data (buy pressure, unique buyers, velocity, volume vs liquidity,
freshness, growth since first seen) + GoPlus honeypot/tax gate on EVM chains.

HONEST LIMITS: PAPER ONLY on these chains (no live trade adapter exists for BSC/Base/Robinhood);
GeckoTerminal only lists a coin once it has a DEX pool, so four.meme / launchpad bonding-curve
phase BEFORE the pool exists is not seen; new_pools returns the newest ~20 pools per call, so on very
busy chains some launches are missed. Thresholds are untuned guesses until the 24h data is analysed.
"""
import os, time, json, datetime, threading, traceback
from collections import deque
from typing import Optional

import state

GT_SLUGS = {"solana": "solana", "bsc": "bsc", "base": "base"}
RHC_CANDIDATES = ("robinhood-chain", "robinhood", "robinhood_chain")


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _num(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _ts(s) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_pools(gt_json, chain: str) -> list:
    """GeckoTerminal pools JSON -> normalized candidate dicts."""
    out = []
    for pool in (gt_json or {}).get("data", []) or []:
        a = pool.get("attributes") or {}
        rel = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
        rid = rel.get("id") or ""
        addr = rid.split("_", 1)[1] if "_" in rid else None
        if not addr:
            continue
        m5 = (a.get("transactions") or {}).get("m5") or {}
        out.append({
            "chain": chain, "addr": addr, "name": a.get("name") or addr[:8], "src": "gt",
            "dex": (((pool.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id"),
            "mcap": _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd")),
            "liq": _num(a.get("reserve_in_usd")),
            "created": _ts(a.get("pool_created_at")),
            "buys5": m5.get("buys"), "sells5": m5.get("sells"), "buyers5": m5.get("buyers"),
            "vol5": _num((a.get("volume_usd") or {}).get("m5")),
            "chg5": _num((a.get("price_change_percentage") or {}).get("m5")),
            "chg1h": _num((a.get("price_change_percentage") or {}).get("h1")),
        })
    return out


def parse_stonk(body) -> list:
    toks = (body or {}).get("data") or (body or {}).get("tokens") or []
    out = []
    for t in toks:
        if not isinstance(t, dict) or not t.get("mint"):
            continue
        m = t.get("market") or {}
        out.append({
            "chain": "solana", "addr": t["mint"], "name": t.get("symbol") or t.get("name") or t["mint"][:8],
            "src": "stonkfun", "dex": "stonkfun", "mcap": _num(m.get("marketCapUsd")),
            "liq": _num(m.get("liquidityUsd")), "created": _ts(t.get("createdAt")),
            "vol24": _num(m.get("volume24hUsd")), "grad": _num(t.get("graduationProgress")),
            "buys5": None, "sells5": None, "buyers5": None, "vol5": None, "chg5": None, "chg1h": None,
        })
    return out


class Scanner:
    def __init__(self):
        self.hist = {}      # (chain, addr) -> deque[(ts, mcap)]
        self.first = {}     # (chain, addr) -> (ts, mcap)

    def see(self, p: dict, now: float):
        k = (p["chain"], p["addr"])
        if p.get("mcap"):
            self.first.setdefault(k, (now, p["mcap"]))
            h = self.hist.setdefault(k, deque(maxlen=40))
            h.append((now, p["mcap"]))
        return k

    def evaluate(self, p: dict, now: float) -> Optional[dict]:
        k = self.see(p, now)
        mcap, liq = p.get("mcap"), p.get("liq")
        if not mcap or mcap < _f("MC_MIN_MCAP_USD", 3000) or mcap > _f("MC_MAX_MCAP_USD", 60_000):
            return None
        if p["src"] == "gt" and (liq or 0) < _f("MC_MIN_LIQ_USD", 2500):
            return None
        created = p.get("created") or self.first[k][0]
        age_min = max(0.0, (now - created) / 60.0)
        if age_min > _f("MC_MAX_AGE_MIN", 60):
            return None
        conf = []
        b, s = p.get("buys5"), p.get("sells5")
        if b is not None and s is not None and b >= _f("MC_MIN_BUYS5", 12) and b >= 1.3 * max(s, 1):
            conf.append("pressure")
        if (p.get("buyers5") or 0) >= _f("MC_MIN_BUYERS5", 8):
            conf.append("buyers")
        c5 = p.get("chg5")
        if c5 is not None and _f("MC_MIN_CHG5", 15) <= c5 <= _f("MC_MAX_CHG5", 400):
            conf.append("velocity")
        if c5 is not None and c5 > _f("MC_MAX_CHG5", 400):
            return None                                   # already exploded: don't chase
        v5 = p.get("vol5")
        vol = v5 if v5 is not None else p.get("vol24")
        if vol and (vol >= _f("MC_MIN_VOL_USD", 3000) or (liq and vol >= 0.25 * liq)):
            conf.append("volume")
        if age_min <= 15:
            conf.append("fresh")
        t0, m0 = self.first[k]
        if now - t0 >= 40 and mcap >= 1.25 * m0:
            conf.append("growth")
        if p["src"] == "stonkfun" and (p.get("grad") or 0) >= 0.3:
            conf.append("graduating")
        if b is not None and s is not None and s > 1.5 * max(b, 1):
            return None                                   # being dumped
        need = int(_f("MC_MIN_CONF_STONK" if p["src"] == "stonkfun" else "MC_MIN_CONF", 2 if p["src"] == "stonkfun" else 3))
        if len(conf) < need:
            return None
        lanes = ["core1"]
        if len(conf) >= 4 and age_min <= 15:
            lanes.append("core2")
            if age_min <= 10:
                lanes.append("sprint")
        if len(conf) >= 3 and "compound" not in lanes:
            lanes.append("compound")
        return {"chain": p["chain"], "addr": p["addr"], "name": p["name"], "src": p["src"], "mcap": mcap,
                "liq": liq, "age_min": round(age_min, 1), "confirmations": conf, "lanes": lanes, "lane": lanes[0],
                "potential": "100x+" if "core2" in lanes else "10x-50x"}


def evm_safe(chain: str, addr: str) -> tuple:
    """GoPlus honeypot / tax gate. Unscanned is allowed for paper but tagged."""
    if chain not in ("bsc", "base"):
        return True, "n/a"
    try:
        from layers.layer0_scoring import fetch_goplus_security
        r = fetch_goplus_security(chain, addr)
        if not r.get("ok"):
            return True, "unscanned"
        d = r["data"]
        if str(d.get("is_honeypot")) == "1":
            return False, "honeypot"
        for k in ("sell_tax", "buy_tax"):
            v = _num(d.get(k))
            if v is not None and v > 0.15:
                return False, f"{k} {v:.0%}"
        return True, "scanned ok"
    except Exception:  # noqa: BLE001
        return True, "unscanned"


def act(d: dict, now: Optional[float] = None) -> dict:
    """Paper entries in every lane book + dashboard feed row. Paper only on these chains."""
    from executor import books, paper_ledger
    now = now if now is not None else time.time()
    key = f"mc_seen:{d['chain']}:{d['addr']}"
    if state.cache_get(key, 24 * 3600):
        return {"skipped": "already acted"}
    ok, why = evm_safe(d["chain"], d["addr"])
    if not ok:
        state.cache_set(key, True)
        return {"skipped": f"unsafe: {why}"}
    state.cache_set(key, True)
    opened, refused = [], []
    for lane in d["lanes"]:
        can, reason = books.can_open(lane)
        stake = books.stake_usd(lane) if can else 0.0
        if stake and d["chain"] in ("bsc", "robinhood_chain", "robinhood"):
            # Oct 13 2026 first run: bsc 0 wins / 9 (avg -81%), robinhood 0 / 5 (avg -54%): evidence mode.
            stake = round(stake * (_f("MC_EVM_STAKE_MULT", 0.3) or 0.3), 2)
            stake = stake if stake >= 5.0 else 0.0
        if not stake:
            refused.append(f"{lane}: {reason}")
            continue
        pos = paper_ledger.open_paper(d["chain"], d["addr"], f"early_{lane}", f"early_{lane}", stake, d["mcap"],
                                      strategy="early", exit_profile=lane, liquidity_usd=d.get("liq"),
                                      tags={"origin": "multichain", "src": d["src"], "age_min": d["age_min"],
                                            "confirmations": d["confirmations"], "safety": why}, module=lane)
        (opened if pos else refused).append(lane if pos else f"{lane}: not opened")
    try:
        state.log_full_alert("early_" + d["lane"], d["chain"], d["name"], d["addr"],
                             f"Multichain early entry ({d['age_min']}m old, ${d['mcap']:,.0f})",
                             {"Chain": d["chain"], "Module": d["lane"], "Source": d["src"],
                              "Confirmations": ", ".join(d["confirmations"]), "Safety": why,
                              "Paper": ", ".join(opened) or "none", "Dev holding": ""})
    except Exception:  # noqa: BLE001
        pass
    return {"opened": opened, "refused": refused}


def _gt_get(slug: str):
    from layers.layer0_scoring import fetch_geckoterminal_new_pools
    r = fetch_geckoterminal_new_pools(slug)
    return r.get("json") if isinstance(r, dict) and r.get("ok") else None


def resolve_rhc() -> Optional[str]:
    forced = os.environ.get("MC_RHC_SLUG")
    for slug in ([forced] if forced else RHC_CANDIDATES):
        body = _gt_get(slug)
        if body and body.get("data") is not None:
            return slug
    return None


def run_loop(stop: Optional[threading.Event] = None):
    sc = Scanner()
    slugs = dict(GT_SLUGS)
    rhc = None
    cycle = 0
    print("[multichain] sniper loop started (solana non-pump + StonkFun, bsc, base, robinhood)", flush=True)
    while not (stop and stop.is_set()):
        t0 = time.time()
        try:
            if rhc is None and cycle % 15 == 0:
                rhc = resolve_rhc()
                if rhc:
                    slugs["robinhood_chain"] = rhc
                    print(f"[multichain] Robinhood chain slug: {rhc}", flush=True)
                else:
                    print("[multichain] Robinhood chain not found on GeckoTerminal; set MC_RHC_SLUG", flush=True)
            batches = []
            for chain, slug in slugs.items():
                body = _gt_get(slug)
                batches += parse_pools(body, chain)
                time.sleep(2.5)
            try:
                from layers.layer0c_stonkfun_scoring import fetch_new_stonkfun_tokens
                r = fetch_new_stonkfun_tokens(50)
                batches += parse_stonk((r.get("raw") or {}).get("json"))
            except Exception:  # noqa: BLE001
                pass
            now = time.time()
            for p in batches:
                d = sc.evaluate(p, now)
                if d:
                    res = act(d, now)
                    try:
                        print("[multichain] ENTRY", d["chain"], str(d["name"]).encode("ascii", "replace").decode(),
                              d["confirmations"], res, flush=True)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        cycle += 1
        time.sleep(max(2.0, _f("MC_POLL_S", 20) - (time.time() - t0)))


if __name__ == "__main__":
    run_loop()

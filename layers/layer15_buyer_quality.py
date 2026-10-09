"""
Layer 15 -- buyer quality: is the demand for this coin REAL?  (GO_LIVE_CHECKLIST 9.1-9.5)

The checklist's honest basis (Phase 9): the scanner filters for SAFETY (won't
rug) when it should filter for DEMAND (who is buying). This module is the
demand side, as small pure functions on top of data the project already
collects (layers/pumpfun_trades.py decodes real pump.fun buys: wallet, mint,
sol_delta, block_time):

  9.1 smart_wallet_hits   -- >= SMART_WALLET_MIN distinct wallets from the smart-money
                             roster bought the same coin.
  9.2 buyer_velocity      -- UNIQUE buyer wallets in the first window, and whether the
                             second half is at least as busy as the first (real demand
                             accelerating, not total volume).
  9.3 funding_cluster_report -- fake-traction detector: many buyers that all trace back
                             to 1-2 funding wallets, brand-new wallets, or identical
                             buy sizes = one bot farm. Funding lookups are injected
                             (layer10_insider_cluster.solana_first_funder in live use).
  9.4 concentration_ok    -- dev + sniper/bundle share cap (default 30%).
  9.5 deployer_weight     -- our own deployer track record (state.get_deployer_reputation)
                             as score points.

HONESTY: every threshold below is a FIRST-PASS heuristic, not a measured value.
They are env-tunable and exist so the data can finally be COLLECTED (archive_buys
records who bought what) and then BACK-TESTED (backtest_buyer_signals.py, item 9.9).
Until that back-test shows positive expectancy on unseen data, the demand verdict is
used to BLOCK clearly fake demand (bot_farm) and as information -- it never
unlocks a buy by itself.

LIMIT: "first N minutes" is measured from the first buy THIS system saw for the
mint, not the true launch time (the poller samples recent program signatures).
"""
import os
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional

import state

ARCHIVE_KEY = "buyer_archive"
FUNDER_CACHE_KEY = "funder_cache"
MAX_ARCHIVE_MINTS = 300
MAX_BUYS_PER_MINT = 80
MAX_FUNDER_CACHE = 3000
MIN_BUYERS_TO_JUDGE = 5


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


# ---------------------------------------------------------------- 9.1
def smart_wallet_hits(buys: List[dict], roster: Iterable[str]) -> dict:
    roster = set(roster or [])
    hit = sorted({b.get("wallet") for b in buys if b.get("wallet") in roster})
    need = int(_f("SMART_WALLET_MIN", 2))
    return {"count": len(hit), "wallets": hit, "fires": len(hit) >= need}


# ---------------------------------------------------------------- 9.2
def buyer_velocity(buys: List[dict], window_s: float = 300.0) -> dict:
    times = [(b["wallet"], b["block_time"]) for b in buys if b.get("wallet") and b.get("block_time")]
    if not times:
        return {"distinct": 0, "first_half": 0, "second_half": 0, "accelerating": False}
    t0 = min(t for _, t in times)
    half = window_s / 2
    first, second = set(), set()
    for w, t in times:
        dt = t - t0
        if dt < half:
            first.add(w)
        elif dt < window_s:
            second.add(w)
    distinct = len(first | second)
    return {"distinct": distinct, "first_half": len(first), "second_half": len(second),
            "accelerating": len(second) >= len(first) and len(second) > 0}


# ---------------------------------------------------------------- 9.3
def funding_cluster_report(buys: List[dict], funder_of: Dict[str, Optional[str]],
                           wallet_age_days: Optional[Dict[str, float]] = None) -> dict:
    """funder_of: wallet -> funding wallet (None/absent = unknown, never guessed)."""
    wallets = sorted({b.get("wallet") for b in buys if b.get("wallet")})
    known = {w: funder_of.get(w) for w in wallets if funder_of.get(w)}
    n_known = len(known)
    counts = Counter(known.values())
    top_share = (counts.most_common(1)[0][1] / n_known) if n_known else 0.0
    distinct_funders = len(counts)
    funder_diversity = (distinct_funders / n_known) if n_known else 0.0

    sizes = [round(abs(b["sol_delta"]), 2) for b in buys if b.get("sol_delta")]
    same_size = (Counter(sizes).most_common(1)[0][1] / len(sizes)) if len(sizes) >= MIN_BUYERS_TO_JUDGE else 0.0

    new_ratio = None
    if wallet_age_days:
        ages = [wallet_age_days[w] for w in wallets if w in wallet_age_days]
        if ages:
            new_ratio = sum(1 for a in ages if a < 1.0) / len(ages)

    farm_funding = n_known >= MIN_BUYERS_TO_JUDGE and (
        top_share >= _f("BOT_FARM_TOP_FUNDER_SHARE", 0.5) or funder_diversity <= _f("BOT_FARM_MAX_DIVERSITY", 0.3))
    farm_pattern = same_size >= 0.6 and (new_ratio is None or new_ratio >= 0.6) and len(sizes) >= MIN_BUYERS_TO_JUDGE
    organic = (n_known >= MIN_BUYERS_TO_JUDGE and distinct_funders >= 4
               and top_share < 0.4 and same_size < 0.5)
    verdict = "bot_farm" if (farm_funding or farm_pattern) else ("organic" if organic else "unknown")
    return {"verdict": verdict, "buyers": len(wallets), "funders_known": n_known,
            "distinct_funders": distinct_funders, "top_funder_share": round(top_share, 2),
            "same_size_share": round(same_size, 2), "new_wallet_ratio": new_ratio}


# ---------------------------------------------------------------- 9.4
def concentration_ok(dev_pct: Optional[float], sniper_pct: Optional[float]) -> tuple:
    """(ok, reason). Unknown data never blocks; only a confirmed bad value does."""
    if dev_pct is None and sniper_pct is None:
        return True, "dev/sniper holdings unknown"
    total = (dev_pct or 0.0) + (sniper_pct or 0.0)
    cap = _f("GUARD_MAX_DEV_SNIPER_PCT", 0.30)
    if total >= cap:
        return False, f"dev + snipers/bundles hold {total * 100:.0f}% (cap {cap * 100:.0f}%)"
    return True, f"dev + snipers hold {total * 100:.0f}% (cap {cap * 100:.0f}%)"


# ---------------------------------------------------------------- 9.5
def deployer_weight(rep: Optional[dict]) -> int:
    """Points from our OWN deployer track record (state.get_deployer_reputation)."""
    if not rep:
        return 0
    if rep.get("tier") == "blacklisted":
        return -100
    if rep.get("tier") == "trusted":
        return 15 + min(10, 5 * max(0, rep.get("wins", 0) - 2))
    if rep.get("losses", 0) > rep.get("wins", 0):
        return -5
    return 0


# ---------------------------------------------------------------- composite
def assess_demand(buys: List[dict], roster: Iterable[str], funder_of: Dict[str, Optional[str]],
                  dev_pct: Optional[float] = None, sniper_pct: Optional[float] = None,
                  deployer_rep: Optional[dict] = None,
                  wallet_age_days: Optional[Dict[str, float]] = None) -> dict:
    sm = smart_wallet_hits(buys, roster)
    vel = buyer_velocity(buys)
    fund = funding_cluster_report(buys, funder_of, wallet_age_days)
    conc_ok, conc_why = concentration_ok(dev_pct, sniper_pct)
    dep = deployer_weight(deployer_rep)

    score = 0
    reasons = []
    if sm["fires"]:
        score += 35; reasons.append(f"{sm['count']} smart wallets bought")
    if vel["distinct"] >= 8 and vel["accelerating"]:
        score += 20; reasons.append(f"{vel['distinct']} unique buyers, accelerating")
    elif vel["distinct"] >= 5:
        score += 8; reasons.append(f"{vel['distinct']} unique buyers")
    if fund["verdict"] == "organic":
        score += 25; reasons.append("buyers have independent funders")
    elif fund["verdict"] == "bot_farm":
        score -= 60; reasons.append("buyers trace to few funders / identical sizes (bot farm)")
    if dep:
        score += dep; reasons.append(f"deployer record {dep:+d}")
    if not conc_ok:
        score -= 40; reasons.append(conc_why)
    score = max(-100, min(100, score))

    if fund["verdict"] == "bot_farm":
        verdict = "bot_farm"
    elif not conc_ok:
        verdict = "concentrated"
    elif score >= 60:
        verdict = "strong"
    elif score >= 25:
        verdict = "moderate"
    else:
        verdict = "weak"
    return {"verdict": verdict, "score": score, "reasons": reasons,
            "smart_wallets": sm, "velocity": vel, "funding": fund,
            "concentration_ok": conc_ok, "deployer_points": dep}


# ---------------------------------------------------------------- data collection (feeds 9.9)
def archive_buys(decoded_buys: List[dict]) -> int:
    """Records who bought what (bounded) so the signals can be back-tested later.
    Local-state friendly: one read + one write per call, only when there are buys."""
    if not decoded_buys:
        return 0
    arch = state.get_value(ARCHIVE_KEY) or {}
    added = 0
    for b in decoded_buys:
        mint, w, t = b.get("mint"), b.get("wallet"), b.get("block_time")
        if not (mint and w and t):
            continue
        rec = arch.setdefault(mint, {"first_ts": t, "buys": []})
        if any(x["w"] == w and x["t"] == t for x in rec["buys"]):
            continue
        if len(rec["buys"]) < MAX_BUYS_PER_MINT:
            rec["buys"].append({"w": w, "t": t, "sol": b.get("sol_delta")})
            rec["first_ts"] = min(rec["first_ts"], t)
            added += 1
    if len(arch) > MAX_ARCHIVE_MINTS:
        keep = sorted(arch.items(), key=lambda kv: kv[1]["first_ts"], reverse=True)[:MAX_ARCHIVE_MINTS]
        arch = dict(keep)
    if added:
        state.set_value(ARCHIVE_KEY, arch)
    return added


def archived_buys(mint: str) -> List[dict]:
    rec = (state.get_value(ARCHIVE_KEY) or {}).get(mint)
    if not rec:
        return []
    return [{"wallet": x["w"], "mint": mint, "block_time": x["t"], "sol_delta": x.get("sol")} for x in rec["buys"]]


def lookup_funders(wallets: Iterable[str], max_new: int = 8,
                   getter: Optional[Callable[[str], Optional[str]]] = None) -> Dict[str, Optional[str]]:
    """wallet -> first funder, cached in state. At most `max_new` fresh RPC lookups per call
    (they are free but slow); everything else comes from the cache or stays unknown."""
    if getter is None:
        from layers.layer10_insider_cluster import solana_first_funder as getter
    cache = state.get_value(FUNDER_CACHE_KEY) or {}
    out, fresh = {}, 0
    for w in wallets:
        if w in cache:
            out[w] = cache[w] or None
            continue
        if fresh >= max_new:
            continue
        fresh += 1
        try:
            f = getter(w)
        except Exception:
            f = None
        cache[w] = f or ""
        out[w] = f
    if fresh:
        if len(cache) > MAX_FUNDER_CACHE:
            cache = dict(list(cache.items())[-MAX_FUNDER_CACHE:])
        state.set_value(FUNDER_CACHE_KEY, cache)
    return out


def assess_mint(mint: str, roster: Iterable[str], dev_pct: Optional[float] = None,
                sniper_pct: Optional[float] = None, deployer_rep: Optional[dict] = None,
                getter: Optional[Callable[[str], Optional[str]]] = None) -> Optional[dict]:
    """Live entry point: judge a mint from what the archive has seen. None = not enough buyers yet."""
    buys = archived_buys(mint)
    if len({b["wallet"] for b in buys}) < MIN_BUYERS_TO_JUDGE:
        return None
    funders = lookup_funders(sorted({b["wallet"] for b in buys}), getter=getter)
    return assess_demand(buys, roster, funders, dev_pct, sniper_pct, deployer_rep)

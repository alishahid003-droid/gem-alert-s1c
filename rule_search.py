"""
Rule search (Oct 1 2026) -- does ANY entry/exit rule make money on the coins
the system actually alerts on?

Ali: "dig deep -- find out how we can make this better immediately." The
paper ledger and alert replay showed 6-8% wins with the current rules
(46/50 trades ended on the time stop: the coins didn't move). Instead of
guessing new thresholds, this tests ~600 rule combinations on the real
5-minute candles of every alerted coin:

  confirm   wait for the coin to prove itself first: enter only once price
            is +0/5/10/20% above the alert price, within 30/60 min (no
            confirmation inside the window = no trade)
  tp        take profit at +5/10/20/50/100%
  sl        stop loss at -5/10/20/35%
  tmax      leave after 30/60/180/720 min

Costs per chain/liquidity are subtracted. Inside one 5-min candle that
touches both the stop and the target, the STOP is assumed first
(pessimistic). Overfitting check: rules are ranked on the OLDER half of the
alerts and then shown on the NEWER half they never saw -- only a rule that
also wins on the second half is worth anything.
"""
from itertools import product
from typing import Optional

CONFIRMS = (0.0, 0.05, 0.10, 0.20)
WINDOWS = (30, 60)
TPS = (1.05, 1.10, 1.20, 1.50, 2.00)
SLS = (0.05, 0.10, 0.20, 0.35)
TMAXS = (30, 60, 180, 720)
BAR_MIN = 5


def sim(cs: list, idx: int, cost: float, confirm: float, window: int,
        tp: float, sl: float, tmax: int) -> Optional[float]:
    """Net return (fraction) of one trade, or None if never entered."""
    base = cs[idx]["o"]
    if confirm <= 0:
        e_i, e_px = idx, base
    else:
        target = base * (1 + confirm)
        e_i = None
        for i in range(idx, min(len(cs), idx + window // BAR_MIN + 1)):
            if cs[i]["h"] >= target:
                e_i, e_px = i, target
                break
        if e_i is None:
            return None
    ret = None
    last = e_i
    for j in range(e_i + 1, len(cs)):
        if (j - e_i) * BAR_MIN > tmax:
            break
        last = j
        if cs[j]["l"] <= e_px * (1 - sl):
            ret = -sl
            break
        if cs[j]["h"] >= e_px * tp:
            ret = tp - 1
            break
    if ret is None:
        ret = cs[last]["c"] / e_px - 1
    return ret - cost


def _stats(rets: list) -> dict:
    if not rets:
        return {"n": 0, "win": 0.0, "exp": 0.0}
    return {"n": len(rets), "win": sum(1 for r in rets if r > 0) / len(rets),
            "exp": sum(rets) / len(rets)}


def combos():
    seen = set()
    for c, w, tp, sl, tm in product(CONFIRMS, WINDOWS, TPS, SLS, TMAXS):
        key = (c, 0 if c <= 0 else w, tp, sl, tm)
        if key not in seen:
            seen.add(key)
            yield key


def run(samples: list, top: int = 12) -> list:
    """samples: [{"cs", "idx", "cost", "chain", "ts"}], oldest first.
    Prints and returns the ranked rules."""
    samples = sorted(samples, key=lambda s: s["ts"])
    half = len(samples) // 2
    train, test = samples[:half], samples[half:]
    rows = []
    for key in combos():
        c, w, tp, sl, tm = key
        tr = [r for s in train if (r := sim(s["cs"], s["idx"], s["cost"], c, w, tp, sl, tm)) is not None]
        te = [r for s in test if (r := sim(s["cs"], s["idx"], s["cost"], c, w, tp, sl, tm)) is not None]
        rows.append({"rule": key, "train": _stats(tr), "test": _stats(te), "all": _stats(tr + te)})
    min_n = max(8, len(train) // 6)
    ranked = sorted([r for r in rows if r["train"]["n"] >= min_n], key=lambda r: -r["train"]["exp"])
    print("\n" + "=" * 100)
    print(f"RULE SEARCH: {len(rows)} rules on {len(samples)} alerted coins "
          f"(ranked on the older {len(train)}, checked on the newer {len(test)})")
    print(f"{'confirm':>8} {'win':>4} {'TP':>5} {'SL':>5} {'tmax':>5} | {'train n':>7} {'win%':>5} {'exp%':>6} "
          f"| {'TEST n':>6} {'win%':>5} {'exp%':>6}")
    for r in ranked[:top]:
        c, w, tp, sl, tm = r["rule"]
        a, b = r["train"], r["test"]
        print(f"{c * 100:>7.0f}% {w:>4} {(tp - 1) * 100:>4.0f}% {sl * 100:>4.0f}% {tm:>5} | {a['n']:>7} "
              f"{a['win'] * 100:>4.0f}% {a['exp'] * 100:>+6.1f} | {b['n']:>6} {b['win'] * 100:>4.0f}% {b['exp'] * 100:>+6.1f}")
    survivors = [r for r in ranked[:top * 3] if r["test"]["n"] >= 5 and r["test"]["exp"] > 0]
    print(f"\nRules positive on BOTH halves (top {top * 3} by train): {len(survivors)}")
    for r in survivors[:5]:
        c, w, tp, sl, tm = r["rule"]
        print(f"  confirm +{c * 100:.0f}% in {w} min, TP +{(tp - 1) * 100:.0f}%, SL -{sl * 100:.0f}%, {tm} min "
              f"-> all {r['all']['n']} trades, {r['all']['win'] * 100:.0f}% wins, {r['all']['exp'] * 100:+.1f}% avg")
    return ranked


def by_trader(samples: list, rule: tuple, min_trades: int = 3):
    """Copy-trade test: per Fomo trader, the result of copying their buys
    with the given rule -- who is actually worth following."""
    c, w, tp, sl, tm = rule
    per = {}
    for s in samples:
        r = sim(s["cs"], s["idx"], s["cost"], c, w, tp, sl, tm)
        if r is not None and s.get("trader"):
            per.setdefault(s["trader"], []).append(r)
    rows = sorted(((t, _stats(v)) for t, v in per.items() if len(v) >= min_trades), key=lambda x: -x[1]["exp"])
    print(f"\nCOPY-TRADE BY TRADER (rule {rule}), traders with >= {min_trades} copied buys:")
    for t, st in rows[:20]:
        print(f"  {t:24s} {st['n']:>3} buys  {st['win'] * 100:>4.0f}% wins  {st['exp'] * 100:+6.1f}% avg")
    return rows

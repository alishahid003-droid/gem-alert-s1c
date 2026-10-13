"""Winners tracker (Oct 13 2026): builds an UNBIASED winners library going forward.

The sniper only sees a coin while it is among the newest pools. This keeps a small book of every coin the
scanner sees (first-seen market cap + age), then re-checks them in batches (GeckoTerminal tokens/multi, up to
30 addresses per call, paced by the shared API budget gate) at +3h / +24h / +72h to learn the peak. Any coin that
reaches WINNER_MIN_MCAP_USD (default $1M) is a winner; we keep what the system saw at first sight and whether
the sniper entered it. Also reads early_log (pump.fun curve phase) for coins that reached $250K+.

Usage: python winners_tracker.py            (summary of the library)
State: logs/winners_book.json (coins seen in the last 14 days)."""
import json
import os
import sys
import time

BOOK = os.path.join("logs", "winners_book.json")
REVISIT_H = (3, 24, 72)
KEEP_DAYS = 14


def _f(name, default):
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


class Book:
    def __init__(self, path=BOOK):
        self.path = path
        self.coins = {}
        try:
            with open(path, encoding="utf-8") as fh:
                self.coins = json.load(fh)
        except Exception:  # noqa: BLE001
            self.coins = {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.coins, fh)
        except OSError:
            pass

    @staticmethod
    def key(chain, addr):
        return f"{chain}:{addr}"

    def record(self, p, now=None):
        """p = scanner pool dict (chain, addr, name, mcap, created)."""
        now = now if now is not None else time.time()
        mc = p.get("mcap")
        if not mc or not p.get("addr"):
            return
        k = self.key(p["chain"], p["addr"])
        c = self.coins.get(k)
        if c is None:
            created = p.get("created") or now
            c = self.coins[k] = {"name": str(p.get("name") or "")[:24], "chain": p["chain"], "addr": p["addr"],
                                 "first_ts": now, "first_mcap": mc, "first_age_min": round(max(0.0, (now - created) / 60.0), 1),
                                 "peak_mcap": mc, "peak_ts": now, "last_mcap": mc, "done": [], "entered": False,
                                 "dex": p.get("dex")}
        elif mc > c["peak_mcap"]:
            c["peak_mcap"], c["peak_ts"] = mc, now
        c["last_mcap"] = mc

    def mark_entered(self, chain, addr):
        c = self.coins.get(self.key(chain, addr))
        if c:
            c["entered"] = True

    def prune(self, now=None):
        now = now if now is not None else time.time()
        cut = now - KEEP_DAYS * 86400
        for k in [k for k, c in self.coins.items() if c["first_ts"] < cut and c["peak_mcap"] < _f("WINNER_MIN_MCAP_USD", 1_000_000)]:
            del self.coins[k]

    def due(self, now=None, limit=60):
        now = now if now is not None else time.time()
        out = []
        for k, c in self.coins.items():
            for h in REVISIT_H:
                if h not in c["done"] and now - c["first_ts"] >= h * 3600:
                    out.append((c["first_ts"], k, h))
                    break
        out.sort()
        return out[:limit]

    def revisit(self, fetch, slugs, now=None, max_batches=1):
        """fetch(slug, [addrs]) -> {addr: mcap_usd}. Re-checks the oldest due coins, one chain per batch."""
        now = now if now is not None else time.time()
        todo = {}
        for _ts, k, h in self.due(now, 200):
            c = self.coins[k]
            todo.setdefault(c["chain"], []).append((k, h))
        n = 0
        for chain, items in todo.items():
            if n >= max_batches or chain not in slugs:
                continue
            items = items[:30]
            try:
                got = fetch(slugs[chain], [self.coins[k]["addr"] for k, _ in items]) or {}
            except Exception:  # noqa: BLE001
                got = {}
            n += 1
            for k, h in items:
                c = self.coins[k]
                m = got.get(c["addr"])
                c["done"].append(h)
                if m is not None:
                    c["last_mcap"] = m
                    if m > c["peak_mcap"]:
                        c["peak_mcap"], c["peak_ts"] = m, now
        return n

    def winners(self):
        mn = _f("WINNER_MIN_MCAP_USD", 1_000_000)
        return sorted((c for c in self.coins.values() if c["peak_mcap"] >= mn), key=lambda c: -c["peak_mcap"])


def gt_multi_fetch(slug, addrs):
    from utils import http
    base = "https://api.geckoterminal.com/api/v2"
    body = http.get_json(f"{base}/networks/{slug}/tokens/multi/{','.join(addrs)}")
    js = body.get("json") if isinstance(body, dict) and "json" in body else body
    out = {}
    for t in (js or {}).get("data", []) or []:
        a = t.get("attributes") or {}
        try:
            m = float(a.get("market_cap_usd") or a.get("fdv_usd") or 0) or None
        except (TypeError, ValueError):
            m = None
        if a.get("address") and m:
            out[a["address"]] = m
    return out


def pumpfun_runners(min_usd=250_000.0, sol_usd=150.0, max_usd=20_000_000.0):
    """pump.fun coins from early_log whose +5/+15/+60m outcome reached min_usd."""
    import glob
    create, best = {}, {}
    for fp in sorted(glob.glob(os.path.join("early_log", "*.jsonl")))[-7:]:
        for line in open(fp, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("t") == "create":
                create[r["mint"]] = r
            elif r.get("t") == "outcome" and r.get("listed") and min_usd <= (r.get("mcap_usd") or 0) <= max_usd:       # > $20M for a pump.fun coin = unreliable early_log pricing
                best[r["mint"]] = max(best.get(r["mint"], 0), r["mcap_usd"])
    out = []
    for m, v in best.items():
        c = create.get(m) or {}
        out.append({"mint": m, "sym": c.get("sym"), "peak_seen": v, "launch_mcap": (c.get("mcap_sol") or 0) * sol_usd,
                    "dev_sol": c.get("init_sol"), "creator_launches": c.get("creator_launches"),
                    "creator_grads": c.get("creator_grads"), "dev_list": c.get("dev_list")})
    return sorted(out, key=lambda x: -x["peak_seen"])


def main():
    b = Book()
    w = b.winners()
    print(f"book: {len(b.coins):,} coins tracked | winners (peak >= ${_f('WINNER_MIN_MCAP_USD', 1_000_000):,.0f}): {len(w)}")
    for c in w[:40]:
        print(f"  {c['chain']:9s} {c['name']:<20s} first ${c['first_mcap']:>10,.0f} @ age {c['first_age_min']:>6}m  peak ${c['peak_mcap']:>12,.0f}  "
              f"{'SNIPER ENTERED' if c['entered'] else 'not entered'}")
    if w:
        caught = sum(1 for c in w if c["entered"])
        print(f"  -> sniper entered {caught} of {len(w)} winners ({100*caught/len(w):.0f}%)")
        fm = sorted(c["first_mcap"] for c in w)
        print(f"  first-seen mcap of winners: median ${fm[len(fm)//2]:,.0f}, min ${fm[0]:,.0f}, max ${fm[-1]:,.0f}")
    pr = pumpfun_runners()
    print(f"\npump.fun curve-phase runners in early_log (>= $250K by +5/+15/+60m): {len(pr)}")
    for r in pr[:20]:
        print(f"  {str(r['sym'])[:14]:<14s} launch ${r['launch_mcap']:>8,.0f} -> ${r['peak_seen']:>10,.0f}  dev {r['dev_sol']} SOL  creator launches {r['creator_launches']} grads {r['creator_grads']}")


if __name__ == "__main__":
    main()

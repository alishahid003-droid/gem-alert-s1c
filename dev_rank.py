"""Self-built dev scoreboard (15 Oct 2026). Replaces the screenshot dev list, whose devs won 3.9% of launches vs 5.9% average.
A creator is a GOOD DEV when, in OUR OWN logs, >= GOOD_DEV_MIN_LAUNCHES of their coins are old enough to judge and
>= GOOD_DEV_MIN_WIN of them won (graduated or reached 3x on the 5/15/60-min checks).
Time-split test (pick on first 60%, test on last 40%): coins from such devs won ~30% vs 6% average (small sample, watch it).
Also: wallet_roster() builds a smart-wallet list from early buyers of winning coins (needs the trade feed; cold start)."""
import glob, json, os, time, collections
from typing import Dict, Set, Tuple

SOL_USD = 107.55
KEY_DEVS = "good_devs_v1"
KEY_WALLETS = "smart_wallets_v1"


def _env(name, d):
    try:
        return float(os.environ.get(name, "") or d)
    except ValueError:
        return d


def scan(log_glob="early_log/*.jsonl", now=None) -> Tuple[Dict[str, list], Dict[str, list]]:
    """-> ({creator: [launches, wins]}, {wallet: [appearances, wins]}) from the logs."""
    now = now or time.time()
    creates, oc, grads, finals = {}, collections.defaultdict(dict), set(), {}
    for f in sorted(glob.glob(log_glob)):
        try:
            fh = open(f, errors="ignore")
        except OSError:
            continue
        with fh:
            for l in fh:
                head = l[:24]
                try:
                    if '"create"' in head:
                        r = json.loads(l)
                        creates[r["mint"]] = r
                    elif '"outcome"' in head:
                        r = json.loads(l)
                        if r.get("mcap_usd"):
                            oc[r["mint"]][r["cp_min"]] = r["mcap_usd"]
                    elif '"grad"' in head:
                        grads.add(json.loads(l)["mint"])
                    elif '"final"' in head:
                        r = json.loads(l)
                        if r.get("early_buyers"):
                            finals[r["mint"]] = r["early_buyers"]
                except Exception:  # noqa: BLE001
                    continue
    devs, wallets = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
    for mint, c in creates.items():
        if now - c["ts"] < 3900 or not c.get("mcap_sol"):
            continue
        entry = c["mcap_sol"] * SOL_USD
        pk = max(oc[mint].values()) / entry if oc.get(mint) else 0.0
        if pk > 500:
            continue
        win = mint in grads or pk >= 3
        d = devs[c["creator"]]
        d[0] += 1
        d[1] += 1 if win else 0
        for w in finals.get(mint, []):
            a = wallets[w]
            a[0] += 1
            a[1] += 1 if win else 0
    return dict(devs), dict(wallets)


def hot_words(log_glob="early_log/*.jsonl", now=None, hot=5.0, max_freq=30):
    """Name words of coins launched 1-6 h ago that already ran >= `hot`x (peak known). Oct 15 test (36,460 coins): a new coin
    sharing such a word reaches 10x 6.5% of the time vs 2.0% otherwise ('veil', 'wojak' style meta)."""
    import re
    now = now or time.time()
    creates, oc = {}, collections.defaultdict(dict)
    for f in sorted(glob.glob(log_glob)):
        try:
            fh = open(f, errors="ignore")
        except OSError:
            continue
        with fh:
            for l in fh:
                head = l[:24]
                try:
                    if '"create"' in head:
                        r = json.loads(l)
                        if now - 6 * 3600 <= r["ts"] <= now - 3900:
                            creates[r["mint"]] = r
                    elif '"outcome"' in head:
                        r = json.loads(l)
                        if r.get("mcap_usd"):
                            oc[r["mint"]][r["cp_min"]] = r["mcap_usd"]
                except Exception:  # noqa: BLE001
                    continue
    freq, hotc = collections.Counter(), collections.Counter()
    for m, c in creates.items():
        words = {w for w in re.findall(r"[a-z0-9]+", ((c.get("name") or "") + " " + (c.get("sym") or "")).lower()) if len(w) >= 4}
        pk = max(oc[m].values()) / (c["mcap_sol"] * SOL_USD) if oc.get(m) and c.get("mcap_sol") else 0
        if pk > 500:
            continue
        for w in words:
            freq[w] += 1
            if pk >= hot:
                hotc[w] += 1
    return sorted(w for w in hotc if freq[w] <= max_freq)


_STOP = {"solana", "token", "coin", "pump", "wrapped", "usdc", "usdt", "pool", "the", "this", "that", "with", "from"}


def trending_words(body, min_h1=50.0, min_h24=200.0) -> list:
    """Name words of Solana pools GeckoTerminal ranks as trending AND already up strongly (second source of 'what is running now',
    covers graduated runners we did not log). Parses the JSON:API body of /networks/solana/trending_pools."""
    import re
    out = set()
    for pool in (body or {}).get("data", []) or []:
        a = pool.get("attributes") or {}
        pc = a.get("price_change_percentage") or {}
        try:
            up = float(pc.get("h1") or 0) >= min_h1 or float(pc.get("h24") or 0) >= min_h24
        except (TypeError, ValueError):
            up = False
        if not up:
            continue
        nm = str(a.get("name") or "").split("/")[0].lower()
        out |= {w for w in re.findall(r"[a-z0-9]+", nm) if len(w) >= 4 and w not in _STOP}
    return sorted(out)


def fetch_trending_words() -> list:
    try:
        from utils.http import get_json
        from config import CONFIG
        r = get_json(f"{CONFIG.geckoterminal_base_url}/networks/solana/trending_pools")
        return trending_words(r.get("json")) if isinstance(r, dict) and r.get("ok") else []
    except Exception:  # noqa: BLE001
        return []


def good_devs(devs, min_launches=None, min_win=None) -> Set[str]:
    ml = int(min_launches if min_launches is not None else _env("GOOD_DEV_MIN_LAUNCHES", 3))
    mw = min_win if min_win is not None else _env("GOOD_DEV_MIN_WIN", 0.30)
    return {k for k, (n, w) in devs.items() if n >= ml and w / n >= mw}


def smart_wallets(wallets, min_seen=None, min_win=None) -> Set[str]:
    ms = int(min_seen if min_seen is not None else _env("SMART_WALLET_MIN_SEEN", 4))
    mw = min_win if min_win is not None else _env("SMART_WALLET_MIN_WIN", 0.40)
    return {k for k, (n, w) in wallets.items() if n >= ms and w / n >= mw}


def refresh(state, log_glob="early_log/*.jsonl") -> Tuple[int, int]:
    devs, wallets = scan(log_glob)
    try:
        import dev_history
        devs = dev_history.merged_devs(devs, state.get_value(dev_history.KEY) or {})
    except Exception:  # noqa: BLE001
        pass
    gd, sw = good_devs(devs), smart_wallets(wallets)
    try:
        state.set_value("hot_words_v1", sorted(set(hot_words()) | set(fetch_trending_words())))
    except Exception:  # noqa: BLE001
        pass
    state.set_value(KEY_DEVS, sorted(gd))
    state.set_value("dev_stats_v1", {k: list(devs[k]) for k in gd})   # [launches, wins] for the dev-launch alert
    state.set_value(KEY_WALLETS, sorted(sw))
    return len(gd), len(sw)

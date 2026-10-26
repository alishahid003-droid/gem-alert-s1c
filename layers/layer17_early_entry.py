"""Layer 17 -- pre-early entry engine (Ali, Oct 12 2026: "speed matters ... getting
in early ... a pre-early level launch analysis is what makes our system distinctive").

Pure state machine, no network. Feed it pump.fun launch events (create + trades,
the PumpPortal shape) and it says, within seconds of a launch, whether a coin
deserves a paper entry and in WHICH books. EVERY book enters early (Ali, Oct 12:
"it applies to everyone"); the books differ in what they do after entry:

  dev_list     a dev from our quality list launched AND real buyers showed up
  core2        earliest entry (age <= EARLY_CORE2_MAX_AGE_S, small curve mcap), >= 2 confirmations
               -> lottery book, small stakes, runners ride
  core1        early-but-confirmed (age <= EARLY_CORE1_MAX_AGE_S), >= 2 confirmations
  compound     daily-profit book (2x-10x take-profits): needs >= 3 confirmations because it
               must win often; entered IN ADDITION to a core when evidence is strong
  leaderboard  tracked smart wallet bought, nothing else qualified (copy trade)

Market cap is NOT the selector: age and confirmations are. mcap (in SOL) is only a
ceiling so a coin that already ran is not called "early".

Confirmations: dev_list (our dev list / trusted deployer), smart_wallet (roster
wallet bought), velocity (>= EARLY_MIN_BUYERS unique buyers fast), pressure
(buys >> sells), narrative (name/symbol matches a hot news term).
Hard blocks (any one rejects): blacklisted creator, creator holds > EARLY_MAX_DEV_PCT
of supply, creator dumped, one wallet is most of the buying, sell pressure.

Honest limits: thresholds are reasoned defaults, UNTUNED. Paper results decide them.
"""
import os
import time
from typing import Callable, Dict, List, Optional, Set

SUPPLY = 1_000_000_000.0       # pump.fun fixed supply


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default



_FAMOUS_DEFAULT = (
    # politics / tech leaders / AI + big brands
    "trump|elon|musk|vance|biden|obama|putin|zelensky|zuckerberg|bezos|altman|durov|vitalik|satoshi|tesla|spacex|starlink|apple|"
    "microsoft|amazon|google|nvidia|openai|chatgpt|grok|gemini|anthropic|claude|deepseek|samsung|netflix|disney|nike|adidas|"
    "mcdonald|starbucks|cocacola|pepsi|ferrari|lamborghini|bitcoin|"
    # sports people and leagues
    "ronaldo|messi|neymar|mbappe|haaland|lebron|curry|kobe|jordan|tyson|mayweather|mcgregor|khabib|kohli|dhoni|babar|federer|nadal|"
    "djokovic|verstappen|hamilton|nba|nfl|ufc|fifa|wwe|"
    # influencers / creators / celebrities
    "mrbeast|kaicenat|ishowspeed|speed|loganpaul|jakepaul|andrewtate|tate|pewdiepie|adinross|xqc|kardashian|kylie|drake|kanye|"
    "taylorswift|swift|beyonce|rihanna|eminem|snoop|diddy|rogan|hawktuah|dogecoin|"
    # viral memes / movies / games / animals / anime / music acts (anything the internet is already talking about)
    "pepe|doge|shiba|wojak|bonk|pnut|moo.?deng|labubu|barron|melania|batman|joker|spiderman|marvel|avengers|deadpool|barbie|"
    "oppenheimer|wicked|pokemon|pikachu|minecraft|roblox|gta|squidgame|zelda|mario|sonic|naruto|goku|kpop|bts|blackpink|"
    "olympic|worldcup|beast|bigfoot|alien|ufo")
_FAMOUS_CACHE = {}


def _famous_rx():
    import os as _os
    import re as _re
    pat = (_os.environ.get("EARLY_FAMOUS_NAMES") or _FAMOUS_DEFAULT).strip()
    if pat not in _FAMOUS_CACHE:
        _FAMOUS_CACHE[pat] = _re.compile(pat, _re.I) if pat and pat != "off" else _re.compile(r"(?!x)x")
    return _FAMOUS_CACHE[pat]

class Rec:
    __slots__ = ("mint", "symbol", "name", "creator", "created", "dev_tokens", "init_sol", "mcap_sol0", "mcap_sol",
                 "buyers", "buys", "sells", "buy_sol", "sell_sol", "creator_sold", "smart", "decided", "trades",
                 "fast", "last_ts", "peak_sol", "free_buys", "free_sells", "free_vol", "free_ts", "free_liq",
                 "max_buy", "max_buyer")

    def __init__(self, mint, symbol, name, creator, created, dev_tokens, init_sol, mcap_sol):
        self.mint, self.symbol, self.name, self.creator, self.created = mint, symbol, name, creator, created
        self.dev_tokens, self.init_sol, self.mcap_sol0, self.mcap_sol = dev_tokens, init_sol, mcap_sol, mcap_sol
        self.buyers: Dict[str, float] = {}
        self.buys = self.sells = self.trades = 0
        self.buy_sol = self.sell_sol = 0.0
        self.creator_sold = 0.0
        self.smart: Set[str] = set()
        self.decided = False
        self.fast: Set[str] = set()          # wallets that bought within the first seconds (bundle/sniper signature)
        self.last_ts = created
        self.peak_sol = mcap_sol
        self.free_buys = self.free_sells = 0     # free DexScreener txn counts (14 Oct): the metered trade feed only watches dev coins
        self.free_vol = 0.0
        self.free_ts = 0.0
        self.free_liq = None    # 26 Oct: pool liquidity$ at the DexScreener snapshot -- catches thin-pool rugs the free path let through
        self.max_buy = 0.0                    # biggest single non-creator buy (SOL) and who made it: the 'insider / big fresh wallet' signal
        self.max_buyer = ""


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class EarlyTracker:
    def __init__(self, dev_list: Optional[Set[str]] = None, smart_fn: Optional[Callable[[], Set[str]]] = None,
                 trusted_fn: Optional[Callable[[str], bool]] = None, blacklist_fn: Optional[Callable[[str], bool]] = None,
                 hot_terms_fn: Optional[Callable[[], Set[str]]] = None):
        self.recs: Dict[str, Rec] = {}
        self.dev_list = set(dev_list or [])
        self.launches_fn = lambda c: 0       # how many launches this creator has made (serial spammers: 0.3x lift, Oct 13 study)
        self.creator_fn = lambda c: False    # repeat-graduate creator (early_creators.Creators.repeat_grad)
        self.feed_off = False               # True when the per-coin trade feed is unavailable (no API key)
        self.smart_fn = smart_fn or (lambda: set())
        self.trusted_fn = trusted_fn or (lambda w: False)
        self.blacklist_fn = blacklist_fn or (lambda w: False)
        self.hot_terms_fn = hot_terms_fn or (lambda: set())
        self.copy_fn = lambda m: None        # copy_signals.info(): followed-trader / KOL / Fomo-new-wallet buys for this mint
        self.hot_words_fn = lambda: ()      # name words of recent runners (dev_rank.hot_words)
        self.good_dev_fn = lambda c: False   # dev_rank.py scoreboard built from our own logs
        self.claim_fn = None               # claim_fn(term, mint) -> True if this is the FIRST coin to match a trending phrase
        self._phr_cache = (None, ())
        self.stats = {"created": 0, "tracked": 0, "skipped": 0, "decisions": 0}

    # ------------------------------------------------------------------ intake
    def on_create(self, e: dict, now: Optional[float] = None) -> bool:
        """Returns True if the coin is tracked (caller then subscribes to its trades)."""
        now = now if now is not None else time.time()
        mint, creator = e.get("mint"), e.get("traderPublicKey") or e.get("creator")
        if not mint or not creator or mint in self.recs:
            return False
        self.stats["created"] += 1
        dev_tokens = _num(e.get("initialBuy") if e.get("initialBuy") is not None else e.get("tokenAmount"))
        if self.blacklist_fn(creator) or dev_tokens / SUPPLY > _f("EARLY_MAX_DEV_PCT", 0.25):
            self.stats["skipped"] += 1
            return False
        if self.launches_fn(creator) > int(_f("EARLY_MAX_CREATOR_LAUNCHES", 20)):
            self.stats["skipped"] += 1
            return False
        if len(self.recs) >= int(_f("EARLY_MAX_TRACKED", 1500)):
            self._evict_oldest()
        mcap = _num(e.get("marketCapSol"))
        r = Rec(mint, str(e.get("symbol") or "")[:20], str(e.get("name") or "")[:40], creator, now,
                dev_tokens, _num(e.get("solAmount")), mcap)
        self.recs[mint] = r
        self.stats["tracked"] += 1
        return True

    def on_trade(self, e: dict, now: Optional[float] = None) -> Optional[dict]:
        """Feeds one trade event; returns a decision dict the first time the coin qualifies."""
        now = now if now is not None else time.time()
        r = self.recs.get(e.get("mint"))
        if r is None or r.decided:
            return None
        who, sol = e.get("traderPublicKey"), _num(e.get("solAmount"))
        kind = (e.get("txType") or "").lower()
        mc = _num(e.get("marketCapSol"))
        if mc:
            r.mcap_sol = mc
            r.peak_sol = max(r.peak_sol, mc)
        r.last_ts = now
        r.trades += 1
        if kind == "buy":
            r.buys += 1
            r.buy_sol += sol
            if who and who != r.creator:
                r.buyers[who] = r.buyers.get(who, 0.0) + sol
                if sol > r.max_buy:
                    r.max_buy, r.max_buyer = sol, who
                if now - r.created <= _f("EARLY_FAST_WINDOW_S", 3.0):
                    r.fast.add(who)
        elif kind == "sell":
            r.sells += 1
            r.sell_sol += sol
            if who == r.creator:
                r.creator_sold += _num(e.get("tokenAmount"))
        return self.evaluate(r.mint, now)

    def on_free(self, mint: str, buys: int, sells: int, vol_usd: float, mcap_sol: Optional[float] = None,
                now: Optional[float] = None, liquidity_usd: Optional[float] = None) -> Optional[dict]:
        """Free-data path (DexScreener 5-minute txn counts). Gives non-dev coins buying-pressure and volume
        confirmations without the paid trade feed. Returns a decision the first time the coin qualifies.
        26 Oct 2026: liquidity_usd added -- 23 of the night's catastrophic (-75%+) stop_loss exits were ALL
        on this free path (pressure_free/velocity_free), none on the paid-feed path. Every one entered a pool
        thin enough to lose 80-98% between two 6-second re-price checks -- a liquidity-depth problem, not a
        speed problem (positions were already getting checked every 6s). See EARLY_FREE_MIN_LIQ_USD below."""
        now = now if now is not None else time.time()
        r = self.recs.get(mint)
        if r is None or r.decided:
            return None
        r.free_buys, r.free_sells, r.free_vol, r.free_ts = int(buys or 0), int(sells or 0), float(vol_usd or 0), now
        if liquidity_usd is not None:
            r.free_liq = float(liquidity_usd)
        if mcap_sol and mcap_sol > 0:
            r.mcap_sol = mcap_sol
            r.peak_sol = max(r.peak_sol, mcap_sol)
        return self.evaluate(mint, now)

    # --------------------------------------------------------------- decisions
    def confirmations(self, r: Rec, now: float) -> List[str]:
        out = []
        if r.creator in self.dev_list or self.trusted_fn(r.creator):
            out.append("dev_list")
        if self.good_dev_fn(r.creator):
            out.append("good_dev")          # creator won >=30% of >=3 judged launches in our own logs
        if r.max_buy >= _f("EARLY_WHALE_SOL", 3.0) and now - r.created <= _f("EARLY_WHALE_MAX_AGE_S", 90):
            out.append("whale_buy")         # one wallet put >=3 SOL into a young coin
        ci = self.copy_fn(r.mint)
        if ci:
            if ci.get("n", 0) >= 1:
                out.append("kol_buy")           # a followed trader / KOL wallet bought it
            if ci.get("n", 0) >= 2:
                out.append("kol_converge")      # 2+ different followed traders
            if ci.get("new_wallet"):
                out.append("fomo_new_wallet")   # a qualified new high-balance wallet bought it
        if self.creator_fn(r.creator):
            out.append("repeat_grad")       # creator already graduated a token (measured ~7x lift in a 2026 study)
        smart = self.smart_fn()
        hits = [w for w in r.buyers if w in smart]
        if hits:
            r.smart = set(hits)
            out.append("smart_wallet")
        age = max(now - r.created, 1.0)
        n = len(r.buyers)
        if n >= int(_f("EARLY_MIN_BUYERS", 10)) and n / age >= _f("EARLY_MIN_BUYER_RATE", 0.06):
            out.append("velocity")
        if r.buys >= int(_f("EARLY_MIN_BUYS", 12)) and r.buys / max(r.sells, 1) >= _f("EARLY_MIN_BUY_SELL", 2.5):
            out.append("pressure")
        # 26 Oct 2026: require real pool depth before trusting the free-path pressure/velocity signals.
        # r.free_liq is None when DexScreener hasn't reported liquidity yet (treated as unknown, not failing --
        # we don't want to silently disable the whole free path if the field is ever absent) and a real $0-ish
        # reading (thin/fake pool) blocks both confirmations below the floor.
        free_liq_ok = r.free_liq is None or r.free_liq >= _f("EARLY_FREE_MIN_LIQ_USD", 3000)
        if r.free_ts and free_liq_ok:
            if r.free_buys >= int(_f("EARLY_FREE_MIN_BUYS", 20)) and \
                    r.free_buys / max(r.free_sells, 1) >= _f("EARLY_FREE_MIN_BUY_SELL", 1.6):
                out.append("pressure_free")
            if r.free_vol >= _f("EARLY_FREE_MIN_VOL_USD", 2500):
                out.append("velocity_free")
        _nm = ((r.symbol or "") + " " + (r.name or "")).lower()
        try:
            if any(w in _nm for w in self.hot_words_fn()):
                out.append("hot_meta")       # 15 Oct: name shares a word with a recent >=5x runner: 10x rate 6.5% vs 2.0%
        except Exception:  # noqa: BLE001
            pass
        if _famous_rx().search((r.symbol or "") + " " + (r.name or "")):
            out.append("famous_name")        # 14 Oct: brand/celebrity-themed coins reached 10x at 9.2% vs 2.6% (607 vs 20,262 coins, 2 days)
        terms = self.hot_terms_fn()
        if terms:
            words = {w for w in (r.symbol + " " + r.name).lower().replace("-", " ").split() if len(w) >= 3}
            if (r.symbol or "").lower() in terms or words & terms:
                out.append("narrative")
            # trending PHRASES (e.g. a viral person's full name) match even when written as one word, and the first coin wins "narrative_first"
            sq = "".join(ch for ch in ((r.symbol or "") + (r.name or "")).lower() if ch.isalnum())
            if sq:
                if self._phr_cache[0] is not terms:
                    ph = tuple((t, t.replace(" ", "")) for t in terms if " " in t and len(t) >= 7)
                    self._phr_cache = (terms, ph)
                for t, tsq in self._phr_cache[1]:
                    if tsq in sq:
                        if "narrative" not in out:
                            out.append("narrative")
                        try:
                            if self.claim_fn and self.claim_fn(t, r.mint):
                                out.append("narrative_first")
                        except Exception:  # noqa: BLE001
                            pass
                        break
        return out

    def block_reason(self, r: Rec) -> Optional[str]:
        if self.blacklist_fn(r.creator):
            return "creator blacklisted"
        if r.dev_tokens and r.creator_sold / r.dev_tokens > _f("EARLY_MAX_DEV_SELL", 0.30):
            return "creator is dumping"
        if r.buy_sol > 0 and r.buyers:
            top = max(r.buyers.values()) / r.buy_sol
            if r.buys >= 6 and top > _f("EARLY_MAX_TOP_BUYER", 0.40):
                return f"one wallet is {top * 100:.0f}% of the buying"
        if len(r.fast) >= int(_f("EARLY_MAX_FAST_BUYERS", 10)):
            return f"bundled launch ({len(r.fast)} wallets bought in the first seconds)"
        if r.trades >= 10 and r.sells > r.buys * _f("EARLY_MAX_SELL_RATIO", 0.8):
            return "sell pressure"
        if r.free_ts and r.free_buys + r.free_sells >= 10 and r.free_sells > r.free_buys * _f("EARLY_MAX_SELL_RATIO", 0.8):
            return "sell pressure (free data)"
        return None

    def evaluate(self, mint: str, now: Optional[float] = None) -> Optional[dict]:
        now = now if now is not None else time.time()
        r = self.recs.get(mint)
        if r is None or r.decided:
            return None
        age = now - r.created
        if age > _f("EARLY_CORE1_MAX_AGE_S", 1200):
            return None
        why_block = self.block_reason(r)
        if why_block:
            r.decided = True
            return None
        # PRICE SANITY (14 Oct): a brand-new coin cannot be 8x its create print or above a hard cap within seconds.
        # XTO showed 240,253 SOL (~$36M) at 1.4s old; the old code entered it at that bogus print (dev_list had no mcap cap).
        _cap = _f("EARLY_SANITY_MAX_MCAP_SOL", 300)
        _jump = _f("EARLY_SANITY_MAX_JUMP", 8)
        if r.mcap_sol > _cap or (r.mcap_sol0 and r.mcap_sol > r.mcap_sol0 * _jump + 10):
            r.decided = True
            self.stats["skipped"] += 1
            self.stats["insane_price"] = self.stats.get("insane_price", 0) + 1
            return None
        conf = self.confirmations(r, now)
        # thin = too few independent buyers seen. Then ONLY a dev-list launch may enter (needs no trade feed).
        thin = len(r.buyers) < int(_f("EARLY_MIN_BUYERS_ANY", 4)) and \
            not (r.free_ts and r.free_buys >= int(_f("EARLY_FREE_MIN_BUYS", 20)))
        dev_min = int(_f("EARLY_DEV_MIN_BUYERS", 0)) if self.feed_off else int(_f("EARLY_MIN_BUYERS_ANY", 4))
        devish = "dev_list" in conf or "repeat_grad" in conf
        copyish = any(c in conf for c in ("kol_buy", "kol_converge", "fomo_new_wallet"))   # copy-trading needs no trade feed
        if thin and not ((devish and len(r.buyers) >= dev_min) or copyish):
            return None                      # a few independent buyers first: cheap rug filter
        c2_age, c2_mc = _f("EARLY_CORE2_MAX_AGE_S", 240), _f("EARLY_CORE2_MAX_MCAP_SOL", 55)
        c1_age, c1_mc = _f("EARLY_CORE1_MAX_AGE_S", 1200), _f("EARLY_CORE1_MAX_MCAP_SOL", 130)
        lanes = []
        if devish and age <= c2_age:
            lanes.append("dev_list")
        if age <= c2_age and r.mcap_sol <= c2_mc and (len(conf) >= 2 or copyish):
            lanes.append("core2")
        elif age <= c1_age and r.mcap_sol <= c1_mc and (len(conf) >= 2 or copyish):
            lanes.append("core1")
        if ("core1" in lanes or "core2" in lanes) and age <= _f("EARLY_SPRINT_MAX_AGE_S", 600):
            lanes.append("sprint")           # same strong early entry also feeds the 96-hour compounding book
        if len(conf) >= 3 and r.mcap_sol <= _f("EARLY_COMPOUND_MAX_MCAP_SOL", 90):
            lanes.append("compound")         # strong evidence: also a quick-profit position
        if not lanes and conf == ["smart_wallet"] and age >= _f("EARLY_LEADERBOARD_AFTER_S", 60) \
                and r.mcap_sol <= c1_mc:
            lanes.append("leaderboard")      # copy-trade fallback, only after stronger lanes had their chance
        if thin and not copyish:
            lanes = [x for x in lanes if x == "dev_list"]
        if not lanes:
            return None
        lane = lanes[0]
        r.decided = True
        self.stats["decisions"] += 1
        return {"lane": lane, "lanes": lanes, "mint": r.mint, "symbol": r.symbol, "name": r.name, "creator": r.creator,
                "age_s": round(age, 1), "mcap_sol": round(r.mcap_sol, 2), "entry_mcap_sol": round(r.mcap_sol, 2),
                "buyers": len(r.buyers), "buys": r.buys, "sells": r.sells, "confirmations": conf,
                "smart": sorted(r.smart)[:3], "launches": self.launches_fn(r.creator), "fb": r.free_buys, "fs": r.free_sells, "fv": round(r.free_vol), "uses_free": any(c.endswith("_free") for c in conf), "potential": "100x+" if "core2" in lanes else "10x-50x" if "core1" in lanes
                else "2x-10x" if "compound" in lanes else "copy"}

    # ------------------------------------------------------------------ upkeep
    def sweep(self, now: Optional[float] = None) -> List[str]:
        """Drops coins past the tracking window; returns their mints (caller unsubscribes)."""
        now = now if now is not None else time.time()
        cutoff = now - _f("EARLY_TRACK_MAX_AGE_S", 1800)
        gone = [m for m, r in self.recs.items() if r.created < cutoff]
        for m in gone:
            del self.recs[m]
        return gone

    def _evict_oldest(self, n: int = 100):
        for m in sorted(self.recs, key=lambda k: self.recs[k].created)[:n]:
            del self.recs[m]

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
# 28 Oct 2026 (Ali): 1,023 early-sniper trades across all four chains, full history, show bsc/base/
# robinhood_chain are not viable -- bsc is a flat 0% win rate across 37 trades (every single one a
# loss, -$1,073 total); base and robinhood_chain both have >55% of their trades die unpriced before
# ever getting a chance to win, and lose money overall (-$1,045 and -$2,517) despite higher win rates
# than solana, because the wins aren't big enough to cover that dead-on-arrival rate. solana is the
# only chain with real volume (753 trades) and no single-chain catastrophe -- still losing overall
# (-$11,047, 15.5% win rate) but that is a strategy-tuning problem, not "this chain cannot work at
# all" the way bsc/base/robinhood_chain's numbers show. Disabled by default; re-enable one chain by
# removing it from DISABLED_CHAINS (env override) if/when its numbers improve enough to justify it.
# 28 Oct 2026: robinhood_chain re-enabled -- layers/layer14_revival.fetch_dexscreener_batch now
# falls back to GeckoTerminal per-token when DexScreener has no pool indexed yet (the actual cause
# of its 59% dead-on-arrival rate, not a strategy problem -- see that module's GT_FALLBACK_CHAINS
# comment). bsc/base remain disabled on their own merits (bsc: 0% win rate flat; base: still losing
# overall even accounting for its dead-on-arrival rate).
# 28 Oct 2026 (Ali): base re-enabled -- its growth+volume gate (Scanner.evaluate(), a few
# lines below) is now attached, fitted to the chain-specific evidence (91.7% win / +$493.02
# net on that slice, vs 8.3% win / -$1,538.37 on everything else). bsc stays disabled: its
# full-history win rate was a flat 0% (37/37 losses, -$1,073.49) with no filtered slice found
# that flips it, unlike base and robinhood_chain.
#
# 29 Oct 2026 (Ali): solana stays in this set's POLLING (slugs), but is now separately gated
# OFF from the books inside Scanner.evaluate() (MC_SOLANA_ENABLED, default 0) -- it was never
# chain-disabled like bsc because the separate layers/layer17_early_entry.py pipeline (a
# different solana feed) DOES have a real, evidenced gate and must keep trading. This file's
# OWN solana path never had an equivalent gate and was costing -$17,404.56 (972 trades, 12.1%
# win) unfiltered into every module. See the gate's own comment a few lines below for the full
# audit.
DISABLED_CHAINS = {c.strip() for c in os.environ.get("MC_DISABLED_CHAINS", "bsc").split(",") if c.strip()}


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
            "chain": chain, "addr": addr, "name": a.get("name") or addr[:8], "src": "gt", "pool": a.get("address"),
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
        cap = _f("MC_MAX_MCAP_USD", 60_000)
        # Oct 13 2026: 60k stops the moonshot lanes chasing (all 13 first-run entries above $50k lost), but real
        # runners also keep running after 60k. Solana coins up to MC_MOMENTUM_MAX_MCAP_USD may enter ONLY the
        # core1 momentum bucket and only with >=4 confirmations -- its own bucket so the data can judge it.
        chain = p["chain"]
        rhc = chain in ("robinhood", "robinhood_chain")
        # Oct 13 2026 winners replay: Robinhood runners open above $60k (MOW/VRAX/CLAUDIA) and Solana runners
        # can open above $250k (LOAD/Orca CLAUDIA). Both get their own PROBE-size lanes (needs 5 confirmations).
        wide_cap = _f("MC_RHC_MAX_MCAP_USD", 250_000) if rhc else _f("MC_BIG_MAX_MCAP_USD", 1_000_000) if chain == "solana" else cap
        big = bool(mcap and mcap > (_f("MC_MOMENTUM_MAX_MCAP_USD", 250_000) if chain == "solana" else cap) and mcap <= wide_cap)
        momentum = bool(mcap and mcap > cap and chain == "solana"
                        and mcap <= _f("MC_MOMENTUM_MAX_MCAP_USD", 250_000))
        if not mcap or mcap < _f("MC_MIN_MCAP_USD", 3000) or (mcap > cap and not momentum and not big):
            return None
        # 29 Oct 2026 (Ali): "if any of these [smart wallet, dev reputation, verified virality]
        # should highlight for that particular coin ... that module should be working on this,
        # on that coin specifically, not bifurcating it to any other module" -- the chain-specific
        # gates below (base growth+volume, robinhood_chain mcap floor, solana blocked) are a
        # DIFFERENT decision from the quality-signal check here and must stay untouched; this is
        # the one required override path, placed before any of those gates so a real smart-wallet
        # hit is never thrown away by a chain filter it happens to fail.
        #
        # evm_wallets.hit_n is the free, keyless, already-built base/bsc/robinhood_chain analogue
        # of layer17's smart-wallet signal (GeckoTerminal pool trades, wallet early in >=3 winning
        # pools -> "smart"). Checked the real evidence before wiring this in: 8 smart_wallet-tagged
        # multichain trades exist all-time (all robinhood_chain; 0 base/bsc/solana hits so far),
        # 37.5% win / -$124.24 net -- thin and not yet profitable, same shape as layer17's own
        # smart_wallet result (also thin, also net-negative). CRITICALLY, 6 of those 8 (75%) sit
        # BELOW the robinhood_chain $30k mcap floor and were being thrown away by that gate before
        # ever reaching a book -- no evidence was accumulating on them at all. Routing smart_wallet
        # hits into the dev_list book (shared with layer17's own quality-signal routing) regardless
        # of chain-gate outcome fixes that: the coin trades on this signal alone, specifically, and
        # real evidence keeps accumulating the way Ali asked for ("should also start working and
        # giving some analysis on the paper testing at least"), without touching core1/core2/sprint/
        # compound's existing filtered routing for anything else.
        if p.get("pool"):
            try:
                import evm_wallets
                if evm_wallets.hit_n(state, chain, p["pool"], now) >= 1:
                    age_min_sw = max(0.0, (now - (p.get("created") or self.first[k][0])) / 60.0)
                    raw_snapshot_sw = {"buys5": p.get("buys5"), "sells5": p.get("sells5"), "buyers5": p.get("buyers5"),
                                        "vol5": p.get("vol5"), "vol24": p.get("vol24"), "chg5": p.get("chg5"), "chg1h": p.get("chg1h")}
                    return {"chain": p["chain"], "addr": p["addr"], "name": p["name"], "src": p["src"], "mcap": mcap,
                            "liq": liq, "age_min": round(age_min_sw, 1), "confirmations": ["smart_wallet"],
                            "lanes": ["dev_list"], "lane": "dev_list", "potential": "smart_wallet_signal",
                            "tier": "probe", "raw": raw_snapshot_sw}
            except Exception:  # noqa: BLE001
                pass
        # 29 Oct 2026 (Ali): second quality-signal source for the same bypass module, per his
        # instruction to wire in "multiple good enough free ones" once the first (smart_wallet,
        # above) was live. trend_feeds.py is a free, keyless, already-built virality detector
        # (Google Trends/Reddit/Wikipedia/Bluesky/Mastodon/trends24-Twitter-mirror/TikTok) that
        # already feeds layer17's solana pipeline's "narrative_first" tag (worker_early_watch.
        # claim_trend_term, state key trend_first_v1, keyed by TERM not by chain/pipeline -- so
        # "first coin anywhere to match a trending phrase" is exactly the right semantics here,
        # shared cleanly across both pipelines rather than needing a second dedup mechanism).
        # layer17's own narrative_first evidence: 4 trades/3 coins, 25% win, +$9.59 net -- real
        # but too thin alone to gate on; same principle as smart_wallet above: wire it in so
        # evidence accumulates on base/bsc/robinhood_chain too, where it has never run at all
        # (GeckoTerminal's new_pools feed has a name/symbol field, matched the same way layer17
        # matches mint/symbol against trending terms).
        #
        # Honest scope note: dev-reputation (good_dev/repeat_grad) does NOT get the same
        # treatment here -- layer17's dev-reputation signals are keyed off creator/deployer
        # wallet addresses from pump.fun-style launch logs (early_log/*.jsonl, dev_rank.py),
        # and GeckoTerminal's new_pools feed (this file's only data source) carries no
        # creator/deployer field at all for base/bsc/robinhood_chain. Wiring dev-reputation in
        # here would require a genuinely new data source (e.g. on-chain contract-deploy tracing
        # via a block explorer API), not just reusing existing code -- flagged rather than
        # silently skipped.
        try:
            import worker_early_watch as _wew
            terms = _wew.hot_terms()
            if terms:
                nm = (p.get("name") or "")
                sq = "".join(ch for ch in nm.lower() if ch.isalnum())
                hit_term = None
                for t in terms:
                    if " " in t and len(t) >= 7 and t.replace(" ", "") in sq:
                        hit_term = t
                        break
                if hit_term and _wew.claim_trend_term(hit_term, f"{chain}:{p['addr']}"):
                    age_min_nf = max(0.0, (now - (p.get("created") or self.first[k][0])) / 60.0)
                    raw_snapshot_nf = {"buys5": p.get("buys5"), "sells5": p.get("sells5"), "buyers5": p.get("buyers5"),
                                        "vol5": p.get("vol5"), "vol24": p.get("vol24"), "chg5": p.get("chg5"), "chg1h": p.get("chg1h")}
                    return {"chain": p["chain"], "addr": p["addr"], "name": p["name"], "src": p["src"], "mcap": mcap,
                            "liq": liq, "age_min": round(age_min_nf, 1), "confirmations": ["narrative_first"],
                            "lanes": ["dev_list"], "lane": "dev_list", "potential": "narrative_first_signal",
                            "tier": "probe", "raw": raw_snapshot_nf}
        except Exception:  # noqa: BLE001
            pass
        # 28 Oct 2026 (Ali): systematic audit of every confirmation tag, tag combination, and
        # numeric field survivable on closed trade records (mechanical tags turned out to carry
        # almost no edge once isolated per chain -- the earlier 'growth' finding was a base-chain
        # effect, not solana/robinhood_chain; see git history for the full reversal). The one
        # clean, chain-specific, real-sample-size signal that DID hold up: robinhood_chain entry
        # mcap bucketed by size (n=163 all-time):
        #   <5k: 0.0% win  5-10k: 5.6%  10-20k: 14.8%  20-50k: 39.1%  >=50k: 48.3% win, net +$230.87
        # >=50k is the ONLY profitable filtered slice found anywhere in this audit for this chain.
        # Solana showed the opposite shape (worst at both extremes, best at 10-20k, but even
        # that best bucket stays net-negative at -$3,461.63/113 trades) -- no mcap or tag filter
        # found makes solana profitable; this is a genuine negative result, not an oversight.
        #
        # 28 Oct 2026 (Ali, later same day): re-audited after ~50hrs/185 trades/130 unique
        # coins accumulated (vs n=24 when >=50k was first set) because the >=50k slice's win
        # rate was visibly eroding as more trades came in (56.3% -> 54.5% -> 40.0% across three
        # checkpoints). Re-bucketing on the larger sample found the >=50k floor was cutting out
        # a BETTER-performing band: unique-coin win rate by mcap band, no-biglaunch, any age:
        #   $0-30k: 11.2% win (bad, correctly excluded)  $30-50k: 55.0% win  $50-70k: 39.3% win
        # Lowered the floor to $30k. Full comparison at the new floor (n=71 trades/48 coins):
        # 45.1% win trade-level / 45.8% coin-level, net +$220.82 -- vs the old >=50k floor's
        # eroded n=35/21-coin, 40.0% win, +$279.91. Bigger, more trustworthy sample, similar or
        # better rate. Fail side (mcap<30k or biglaunch) stayed at 10.5% win / -$3,109.18 net on
        # n=114/82 coins -- the floor change is a real reclassification, not noise.
        if rhc and mcap < _f("MC_RHC_MIN_MCAP_USD", 30_000):
            return None
        # 28 Oct 2026 (Ali): asked to combine filters on robinhood_chain to push the >=50k
        # win rate higher. Within that >=50k slice (n=29), every mechanical confirmation is
        # universal (need=5 already forces pressure/buyers/velocity/volume/fresh on all of
        # them) so there's no variance left to filter on there -- but the 'big' launch lane
        # (separate code path, bypasses tier_of()) is a clean, consistent loser: ALL of
        # robinhood_chain's biglaunch trades ever recorded (n=2, both historically, not just
        # in this bucket) lost (0% win, -$15.60 combined). Excluding big lifts the >=50k slice
        # from 48.3% win/+$230.87 to 51.9% win/+$246.48 (n=27). n=2 for biglaunch itself is
        # small, but it's the chain's ENTIRE biglaunch history, not a cherry-picked slice, and
        # the lane is already structurally separate (its own tier/sizing), so this is a real
        # exclusion, not overfitting. No historical robinhood_chain trades exist above $100k
        # entry mcap, so a higher floor can't be tested further with this data.
        if rhc and big:
            return None
        # 26 Oct 2026: of tonight's catastrophic (<-75%) losses, 14 were real liquidity
        # drains (rug_liquidity/stop_loss, some within 40s of entry) that got through the
        # old $2500 floor -- too low for how fast a thin Base/BSC/Solana pool can empty.
        # Raised to $4000 AND applied to every src with a reported liq (not just "gt") --
        # stonkfun carries its own real liq field and was skipping this check entirely.
        # liq is None only when the source genuinely doesn't report it; that still passes
        # (fails open), same principle as the layer17 _free liquidity gate from earlier
        # tonight -- missing data never silently blocks a whole source, only a real thin
        # reading does.
        if liq is not None and liq < _f("MC_MIN_LIQ_USD", 4000):
            return None
        created = p.get("created") or self.first[k][0]
        age_min = max(0.0, (now - created) / 60.0)
        if age_min > _f("MC_MAX_AGE_MIN", 60):
            return None
        # 28 Oct 2026 (Ali): asked to find an additional confident signal for robinhood_chain
        # on top of the existing >=$50k mcap floor. Within that bucket (n=27, excluding
        # biglaunch), age_min shows a real, gradual (not cliff-shaped) effect:
        #   age>=1.5min: 53.8% win/+$247.76  age>=1.75min: 55.6%/+$238.29  age>=2.0min: 56.3%/+$238.40
        # vs the 1-2min bucket alone (45.5% win, only +$8.08 net). This is specific to the
        # >=$50k bucket -- across ALL robinhood_chain trades (any mcap), age shows the OPPOSITE
        # shape (1-2min is best, >5min is worst), so this is a real interaction, not a general
        # "wait longer" rule. age_min>=2.25 looked even better (100% win) but n=4 is too thin to
        # trust; 2.0 is the last point before the sample gets unreliably small.
        #
        # 28 Oct 2026 (Ali, later same day): RETIRED after the mcap floor dropped to $30k.
        # Re-tested age thresholds on the new, larger $30k+ bucket (n=48 coins) -- age no
        # longer helps: age>=0min (i.e. no floor) gives 45.8% win/+$220.82 on n=48 coins,
        # age>=2min gives 44.1% win/+$163.06 on a shrunk n=34, age>=3min gives 52.9% on a
        # thin n=17. The age effect found above was specific to the old $50k-only bucket and
        # did not generalize once the mcap floor was corrected -- gating on it now would only
        # throw away sample for no real gain. Left as a disabled env override
        # (MC_RHC_MIN_AGE_MIN defaults to 0 = no gate) rather than deleted, in case a future,
        # larger sample finds a real age interaction again.
        if rhc and age_min < _f("MC_RHC_MIN_AGE_MIN", 0.0):
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
        # 29 Oct 2026: smart_wallet is no longer added to conf here -- any hit_n>=1 coin already
        # returned early above (its own dev_list-routed quality-signal result), so this branch
        # was unreachable dead code. Removed rather than left in place to avoid implying
        # smart_wallet can still reach core1/core2/sprint/compound's conf-based tiering, which
        # it can't anymore by design.
        if p["src"] == "stonkfun" and (p.get("grad") or 0) >= 0.3:
            conf.append("graduating")
        if b is not None and s is not None and s > 1.5 * max(b, 1):
            return None                                   # being dumped
        need = int(_f("MC_MIN_CONF_STONK" if p["src"] == "stonkfun" else "MC_MIN_CONF", 2 if p["src"] == "stonkfun" else 3))
        if len(conf) < need or (momentum and len(conf) < 4) or (big and len(conf) < 5):
            return None
        # --- Per-chain quality gates (Oct 28 2026) ---
        # A blended (all-chain) "growth + conviction tier" gate was tried first and retired the
        # same day: once isolated per chain, the edge turned out to be almost entirely a
        # base-chain effect -- on solana and robinhood_chain specifically, growth showed NO
        # edge or a negative one (solana: 12.5% win WITH growth vs 12.6% WITHOUT, n=8/348;
        # robinhood_chain: 14.8% WITH vs 25.7% WITHOUT, n=27/136 -- actively worse). Replaced
        # with gates fitted to what each chain's own data actually shows:
        #
        # BASE: growth + volume together is a clean, strong, chain-specific signal (n=24
        # all-time): 91.7% win rate, +$493.02 net -- vs everything else on base (n=48): 8.3%
        # win rate, -$1,538.37 net. This is why base can be re-enabled (see DISABLED_CHAINS)
        # with this gate attached, rather than re-enabled blanket.
        if chain == "base" and not ("growth" in conf and "volume" in conf):
            return None
        # 29 Oct 2026 (Ali): "every other coin which does not even fulfill the criteria should be
        # thrown away ... why is it being shipped to any of the modules?" -- audited whether that
        # rule actually holds for every chain reaching this point. base and robinhood_chain do: a
        # non-passing coin on either chain returns None above and never reaches module/lane
        # assignment below (confirmed against live trade data: zero post-restart leaks on either
        # chain). solana does NOT -- this file has never had a chain-level quality gate for solana
        # at all, unlike the separate layers/layer17_early_entry.py pipeline (a different feed,
        # different trades, which DOES gate solana on mcap$5-20k+conf==3+no hot_meta). Every solana
        # coin landing in THIS file's generic len(conf)>=3 mechanical floor was sailing straight
        # into core1/core2/sprint/compound at full stake, filter or no filter. Checked the actual
        # damage: 972 solana multichain-origin trades, all-time, 12.1% win rate, net -$17,404.56 --
        # the single largest money-loser anywhere in the system, confirmed to be the real cause of
        # sprint/core1/core2/compound's chronic busts (14/5/3/20 busts respectively), not stake
        # sizing. No multichain-origin solana pattern has been found and proven profitable (the
        # repeat_grad lead found 28 Oct is on the SEPARATE layer17 pipeline, not this one, and is
        # itself not yet proven -- see GO_LIVE_CHECKLIST.md SQ0/ST0). Until one is found and
        # evidenced the same way base/robinhood were, solana gets the same treatment bsc already
        # has: blocked from the books entirely. The scanner keeps POLLING solana (so a future audit
        # still has data to mine a pattern from) but every solana coin is refused here, before lane
        # assignment, rather than silently trading unfiltered.
        if chain == "solana" and not _f("MC_SOLANA_ENABLED", 0.0):
            return None
        # 28 Oct 2026 (Ali): raw numeric snapshot at decision time, so future pattern audits
        # can test continuous values (actual buy/sell magnitude, actual $ volume, actual %
        # change) instead of only the boolean confirmation tags -- today's audit hit a wall
        # because these never survived onto the closed trade record. Carried through act()
        # into paper_ledger's tags dict below; harmless if unused.
        raw_snapshot = {"buys5": p.get("buys5"), "sells5": p.get("sells5"), "buyers5": p.get("buyers5"),
                        "vol5": p.get("vol5"), "vol24": p.get("vol24"), "chg5": p.get("chg5"), "chg1h": p.get("chg1h")}
        if big:
            return {"chain": p["chain"], "addr": p["addr"], "name": p["name"], "src": p["src"], "mcap": mcap,
                    "liq": liq, "age_min": round(age_min, 1), "confirmations": conf + ["biglaunch"],
                    "lanes": ["core1"], "lane": "core1", "potential": "biglaunch", "tier": "probe", "raw": raw_snapshot}
        if momentum:
            return {"chain": p["chain"], "addr": p["addr"], "name": p["name"], "src": p["src"], "mcap": mcap,
                    "liq": liq, "age_min": round(age_min, 1), "confirmations": conf + ["momentum"],
                    "lanes": ["core1"], "lane": "core1", "potential": "momentum",
                    "tier": "standard" if len(conf) >= 5 else "probe", "raw": raw_snapshot}
        lanes = ["core1"]
        if len(conf) >= 4 and age_min <= 15:
            lanes.append("core2")
            if age_min <= 10:
                lanes.append("sprint")
        if len(conf) >= 3 and "compound" not in lanes:
            lanes.append("compound")
        return {"chain": p["chain"], "addr": p["addr"], "name": p["name"], "src": p["src"], "mcap": mcap,
                "liq": liq, "age_min": round(age_min, 1), "confirmations": conf, "lanes": lanes, "lane": lanes[0],
                "potential": "100x+" if "core2" in lanes else "10x-50x", "tier": tier_of(conf), "raw": raw_snapshot}


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


def tier_of(conf: list) -> str:
    """probe = 3 confirmations; standard = 4-5; conviction = every core signal incl. buyers AND pressure."""
    n = len(conf)
    if n >= 6 and "buyers" in conf and "pressure" in conf:
        return "conviction"
    return "standard" if n >= 4 else "probe"


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
        tier = d.get("tier", "probe")
        can, reason = books.can_open(lane, strong=tier in ("standard", "conviction") and len(d["confirmations"]) >= 5)
        stake = books.tier_stake_usd(lane, tier, d.get("mcap")) if can else 0.0
        if stake and d["chain"] in ("bsc", "robinhood_chain", "robinhood"):
            # Oct 13 2026 first run: bsc 0 wins / 9 (avg -81%), robinhood 0 / 5 (avg -54%): evidence mode.
            mult = _f("MC_RHC_STAKE_MULT", 0.6) if d["chain"] != "bsc" else _f("MC_EVM_STAKE_MULT", 0.3)
            stake = round(stake * (mult or 0.3), 2)
            stake = stake if stake >= 5.0 else 0.0
        if not stake:
            refused.append(f"{lane}: {reason}")
            continue
        pos = paper_ledger.open_paper(d["chain"], d["addr"], f"early_{lane}", f"early_{lane}", stake, d["mcap"],
                                      strategy="early", exit_profile=lane, liquidity_usd=d.get("liq"),
                                      tags={"origin": "multichain", "name": str(d["name"])[:24], "src": d["src"], "age_min": d["age_min"],
                                            "confirmations": d["confirmations"], "tier": d.get("tier", "probe"), "safety": why,
                                            "raw": d.get("raw")}, module=lane)
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
    slugs = {c: s for c, s in GT_SLUGS.items() if c not in DISABLED_CHAINS}
    rhc = None
    cycle = 0
    try:
        from winners_tracker import Book, gt_multi_fetch
        book = Book()
    except Exception:  # noqa: BLE001
        book = None
    active = ", ".join(sorted(slugs)) or "none"
    print(f"[multichain] sniper loop started (active: {active}; disabled: {', '.join(sorted(DISABLED_CHAINS)) or 'none'})", flush=True)
    while not (stop and stop.is_set()):
        t0 = time.time()
        try:
            if rhc is None and cycle % 15 == 0 and "robinhood_chain" not in DISABLED_CHAINS:
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
            try:
                import evm_wallets
                from utils.http import get_json
                from config import CONFIG
                _b = CONFIG.geckoterminal_base_url
                evm_wallets.cycle(state, {c: s for c, s in slugs.items() if c != "solana"}, batches,
                                  lambda sl, pool: (lambda r: r.get("json") if r.get("ok") else None)(get_json(f"{_b}/networks/{sl}/pools/{pool}/trades")),
                                  lambda sl: (lambda r: r.get("json") if r.get("ok") else None)(get_json(f"{_b}/networks/{sl}/trending_pools")), now)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            for p in batches:
                if book is not None:
                    book.record(p, now)
                d = sc.evaluate(p, now)
                if d:
                    res = act(d, now)
                    if book is not None and res.get("opened"):
                        book.mark_entered(d["chain"], d["addr"])
                    try:
                        print("[multichain] ENTRY", d["chain"], str(d["name"]).encode("ascii", "replace").decode(),
                              f"mcap=${d['mcap']:,.0f} age={d['age_min']}m", d["confirmations"], res, flush=True)
                    except Exception:  # noqa: BLE001
                        pass
            if book is not None:
                if cycle % 3 == 2:
                    book.revisit(gt_multi_fetch, slugs, now, max_batches=1)     # 1 API call per 3 cycles
                book.prune(now)
                book.save()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        cycle += 1
        time.sleep(max(2.0, _f("MC_POLL_S", 45) - (time.time() - t0)))


if __name__ == "__main__":
    run_loop()

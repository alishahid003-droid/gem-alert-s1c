"""Fast multi-chain sniper: Solana (non-pump launchpads incl. StonkFun/LaunchLab), BSC, Base, Robinhood Chain.

Same lanes, books, stakes and EXIT ENGINE as the pump.fun sniper (executor.paper_ledger early rules,
books Core 1/2/3 + Compound), but fed by price-action instead of wallet data:
  * GeckoTerminal new_pools per chain (free, keyless, 30 calls/min -> we use ~12/min)
  * StonkFun's own keyless /tokens?sort=newest feed (pre-graduation launches with market data)
Entry = confirmations on live pool data (buy pressure, unique buyers, velocity, volume vs liquidity,
freshness, growth since first seen) + GoPlus honeypot/tax gate on EVM chains.

HONEST LIMITS: GeckoTerminal only lists a coin once it has a DEX pool, so four.meme / launchpad
bonding-curve phase BEFORE the pool exists is not seen; new_pools returns the newest ~20 pools per
call, so on very busy chains some launches are missed. Thresholds are untuned guesses until the
24h data is analysed.

LIVE WIRING (8 Nov 2026, Ali): act() is PAPER ONLY by default -- this has not changed. A real
buy additionally fires ONLY when ALL of these hold at once: (1) the book/lane is listed in the
LIVE_BOOKS env var (e.g. LIVE_BOOKS=compound, or LIVE_BOOKS=compound,sprint,core1,core2), (2)
for sprint/core1/core2 specifically, the Phase-1 $300 cascade live-pause is not set (see
executor.books.is_cascade_live_paused() / clear_cascade_live_pause() -- auto-set the instant the
cascade fires, cleared ONLY by Ali's explicit go, no timeout), (3) the coin's chain is
robinhood_chain or base (the only two chains with a real buy/sell path at all -- solana is
data/pattern-ID only and is NEVER live-armed, bsc has no real adapter either), and (4)
executor.config.EXECUTOR_CONFIG.ready_for_chain(chain) passes (EXECUTION_ENABLED=true + the real
EXECUTION_RHC_PRIVATE_KEY/EXECUTION_BASE_PRIVATE_KEY is set). A real buy's success/failure is
recorded on the paper position (live, live_attempted, live_ok, live_reason, live_tx,
live_amount_tokens) via paper_ledger.update_live_fields(); any failure or exception in the real
path is swallowed and never blocks or breaks the paper trade it mirrors. The matching real sell
lives in executor.paper_ledger._sell().
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
# 9 Nov 2026 (Claude, dead-on-arrival guard -- see act()'s own comment for the full incident
# review): scoped to the chains the real incidents happened on. robinhood_chain already has
# fetch_dexscreener_batch's GT fallback (e55d07a, 27 Oct) -- that fix alone did NOT fully close
# the gap (all 8 real written_off incidents found happened after it landed), so this precheck is
# additive, not redundant. base included for the same reason (same real incident pattern found on
# it too). solana is paper/pattern-only here and already excluded earlier in this file by its own
# gate; bsc stays out since it's disabled by default already (0% win rate, unrelated reason).
DEAD_ON_ARRIVAL_PRECHECK_CHAINS = {"robinhood_chain", "base"}
DEAD_ON_ARRIVAL_CACHE_TTL_SEC = 30 * 60  # matches paper_ledger.py's write-off cache-set TTL expectation


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
        # 31 Oct 2026 (Ali): combined the $30k floor above (still correct, re-confirmed on the
        # newest data too) with a NEW $50k ceiling -- Ali's ask was "a combination... a slab...
        # from your finding to the previous finding" rather than picking one over the other.
        # Fine-grained mcap slab on robinhood_chain's most recent real trades (n=24, both the
        # current state and the Oct 26 pre-reset snapshot combined):
        #   $30-40k: 72.7% win, EV/$1=+0.68 (n=11)   $40-50k: 50.0% win, EV/$1=+0.11 (n=6)
        #   $50-60k: 0.0% win, EV/$1=-0.64 (n=7, 6 unique coins, every one a loss)
        # A clean, monotonic decay, not noise -- and it reconciles with the 28 Oct history
        # above, not contradicts it: that audit already showed the old >=$50k floor's win rate
        # eroding over time (56.3% -> 54.5% -> 40.0% across its own checkpoints) before the
        # floor was dropped to $30k with no ceiling. This is that same erosion continuing past
        # where that audit left off, now crossing into a real negative band. $30-50k (where
        # both the old and new data agree) stays open; only $50k+ is newly excluded.
        if rhc and mcap >= _f("MC_RHC_MAX_MCAP_USD_FLOOR", 50_000):
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
        # 11 Nov 2026 (Ali): compound's win rate had dropped from its genuine 61.3%-era
        # performance; investigation found the real cause was NOT the trim/exit logic
        # (ruled out: matched compound-vs-sprint pairs on the SAME coin closed at the
        # SAME final price regardless of differing stop levels -- the coin crashes through
        # both stops in a single price-check tick, so no stop-level tweak can catch it).
        # The real, chronologically-validated signal: entry_mcap / liquidity_usd ratio.
        # Checked on 254 real historical trades (Nov 6-9, base+robinhood_chain, compound
        # ~48% of sample), proper train/test split (train on older 60%, test only on
        # later unseen 40%): rejecting ratio>=1.7 on the TEST set alone (genuinely
        # out-of-sample) gave a kept bucket outperforming baseline win rate while the
        # rejected bucket was a real net loser (~-$166 to -$338 depending on exact
        # snapshot, 22-25% win rate vs kept's 43-44%). A tighter threshold (1.3x) looked
        # even better on the training slice alone but failed the same out-of-sample test
        # (would have thrown away real profit) -- 1.7x is the threshold that actually
        # survived validation, not the one that looked best on data it had already seen.
        # liq==0 or missing mcap/liq fails open (same principle as the floor check above):
        # missing data never silently blocks an entry, only a real thin-relative-to-mcap
        # reading does.
        # 12 Nov 2026 (Claude, Ali's liquidity-rug loss review): real-data finding -- of
        # compound/sprint/core1's 130 trades since the 1.7x ceiling went live (Nov 11),
        # the entries landing in the 1.4-1.7x band (still allowed through) were a genuine
        # net loser: 10 trades, 20% win rate, -$727.38 combined -- including all 6 of
        # compound's "liquidity $0"/"liquidity $1" rug-pull closes, which looked perfectly
        # healthy at entry (liq $22k-30k, well above the $4k floor) but all shared this
        # same elevated mcap/liq ratio right before the pool got drained live. Chronologically
        # validated the SAME way the original 1.7x threshold was (60/40 train/test split,
        # tested only on genuinely unseen data): tightening to 1.4x holds up out-of-sample
        # -- test-set cut bucket was -$537.58 at 15.4% win rate, test-set kept bucket's pnl
        # was HIGHER than the current 1.7x baseline, not lower. Cuts ~27% of these 3
        # modules' entry volume (35 of 130 trades in the full dataset), all from the
        # already-proven-worse 1.4-1.7x band -- the profitable 1.0-1.2x core of the signal
        # (82.5% win rate, +$4,957.98 in compound alone) is completely untouched.
        if liq is not None and liq > 0 and mcap is not None:
            if (mcap / liq) >= _f("MC_MAX_MCAP_LIQ_RATIO", 1.4):
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
        # 8 Nov 2026 (Ali, backtest-confirmed): real closed-trade history showed sprint/core1/
        # core2 letting in entries at as few as 3 confirmations (core1) or 4 (sprint/core2), and
        # those low-confirmation entries were a real, measured drag -- backtesting a 5+
        # confirmation floor against the real 35-trade sprint/core1/core2 history recovered
        # +$60.27 on its own (net -$306.75 vs the real -$367.03), and +$239.51 combined with the
        # cross-book exposure cap in books.stake_usd() (net -$127.52). compound is untouched --
        # its own entry path below (the `if len(conf) >= 3 and "compound" not in lanes` line)
        # keeps its existing 3+ floor, since compound already has a separately-proven positive
        # edge and was not part of what this backtest fixed.
        EARLY_LANES_MIN_CONF = int(_f("EARLY_LANES_MIN_CONF", 5))
        lanes = ["core1"] if len(conf) >= EARLY_LANES_MIN_CONF else []
        if len(conf) >= max(4, EARLY_LANES_MIN_CONF) and age_min <= 15:
            lanes.append("core2")
            if age_min <= 10:
                lanes.append("sprint")
        if len(conf) >= 3 and "compound" not in lanes:
            lanes.append("compound")
        if not lanes:
            return None
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
    # 9 Nov 2026 (Claude, dead-on-arrival pre-entry check -- Ali: "it should have never been
    # able to come into our trading when the price is not readable through DEX"): real incident
    # review found 8 historical "never priceable since entry" write-offs, -$1,090.59 total,
    # several hitting 2-3 books on the SAME dead coin minutes apart (one: core1 then core2 94s
    # later then compound 230s later, $40+$24.45+$287.75 lost on one unindexed pool). Root cause:
    # the entry signal's own mcap/liquidity numbers (GeckoTerminal, "src":"gt" on both real
    # incidents) were trusted without ever confirming the SAME pool is actually priceable on
    # DexScreener -- the real pricing/management-side source -- before committing a stake.
    # This is the PRIMARY defense: one real DexScreener lookup (with robinhood_chain's existing
    # GT fallback already applied inside fetch_dexscreener_batch -- same resilience the ongoing
    # management loop gets) for THIS coin, before ANY lane opens a position, not per-book and not
    # after the fact. Refuses entry for every lane at once if the pool genuinely can't be priced
    # right now -- cheap (one extra batch call per new coin, not per book) and only ever prevents
    # an entry; never touches exit/scoring logic for a coin that DOES price successfully.
    # DEAD_ON_ARRIVAL_PRECHECK_CHAINS scopes this to the chains the real incidents happened on
    # (robinhood_chain, base) -- solana is paper/pattern-only here and already blocked earlier in
    # this file; bsc is disabled by default already.
    if d["chain"] in DEAD_ON_ARRIVAL_PRECHECK_CHAINS:
        try:
            from layers.layer14_revival import fetch_dexscreener_batch
            _pairs = fetch_dexscreener_batch(d["chain"], [d["addr"]])
            _pair = _pairs.get(d["addr"]) or _pairs.get(d["addr"].lower())
            _priceable = bool(_pair and ((_pair.get("marketCap") or _pair.get("fdv"))))
        except Exception:
            # best-effort: a lookup failure (network hiccup, etc) must never itself become the
            # reason a genuinely good coin is refused -- fail OPEN, not closed, on infra errors.
            _priceable = True
        if not _priceable:
            state.cache_set(key, True)
            return {"skipped": "dead on arrival: pool not priceable on DexScreener pre-entry"}
    # 9 Nov 2026 (Claude): BACKSTOP for the rare case a coin still slips past the precheck above
    # (a brand-new pool that indexes moments later, say) -- if ANY book already wrote this exact
    # coin off as never-priceable-since-entry (paper_ledger.py sets this flag at that write-off),
    # refuse every other book too rather than let each one separately discover the same dead pool.
    if state.cache_get(f"dead_on_arrival:{d['chain']}:{d['addr']}", DEAD_ON_ARRIVAL_CACHE_TTL_SEC):
        state.cache_set(key, True)
        return {"skipped": "dead on arrival: another book already found this coin unpriceable"}
    # 11 Nov 2026 (Claude, Ali's "cut the 28 losses down" overnight review): real-data finding --
    # 65% of one overnight window's no-pop losses (-$237.10 of -$328.59) were the SAME coin
    # re-entered by a different module within 1-5 minutes of a prior module watching it fail to
    # pop (ZOLVA/WETH alone: 6 separate entries across all 4 modules, every re-entry under 5 min
    # after the previous close). This coin WAS priceable (the dead-on-arrival check above correctly
    # let it through) -- it just never moved after entry, which only becomes known once a module
    # actually trades it and watches it sit flat. Mirrors the dead-on-arrival backstop's pattern
    # exactly: paper_ledger.py tags a coin the moment ANY module closes it as no-pop, and every
    # other module refuses re-entry on that exact coin for NO_POP_REENTRY_BLOCK_MIN minutes
    # (default 15 -- comfortably longer than the 1-5 min real re-entry gaps found in the data).
    if d["chain"] in DEAD_ON_ARRIVAL_PRECHECK_CHAINS:
        _no_pop_block_min = float(os.environ.get("NO_POP_REENTRY_BLOCK_MIN", "15"))
        if state.cache_get(f"no_pop_recent:{d['chain']}:{d['addr']}", _no_pop_block_min * 60):
            state.cache_set(key, True)
            return {"skipped": "recently no-popped: another book already watched this coin fail to move"}
    state.cache_set(key, True)
    opened, refused = [], []
    for lane in d["lanes"]:
        tier = d.get("tier", "probe")
        # 7 Nov 2026 (Ali): pass chain through so robinhood_chain and base each get their own
        # position-count slot allowance in this lane, instead of competing for one shared count
        # (see books.can_open()'s own docstring for the full reasoning and capital-gating note).
        can, reason = books.can_open(lane, strong=tier in ("standard", "conviction") and len(d["confirmations"]) >= 5, chain=d["chain"])
        # 7 Nov 2026 (Ali): pass liq through so compound's stake can't land in its own cost
        # model's expensive thin-liquidity penalty bracket (see books.stake_usd()'s docstring
        # for the real incident -- a $90.34 compound stake lost 43.6% to pure slippage/impact
        # on a coin that never even moved). No-op for sprint/core1/core2, which size through
        # tier_stake_usd()'s own mcap-fraction cap already.
        # 8 Nov 2026 (Ali, fix #2 backtest-confirmed): pass chain/addr through so the cross-book
        # exposure cap in books.tier_stake_usd() can actually see which coin this is and cap
        # sprint/core1/core2's stake when another of those 3 books already has capital on the
        # same coin. No-op for compound (books.EXPOSURE_CAP_BOOKS deliberately excludes it).
        stake = books.tier_stake_usd(lane, tier, d.get("mcap"), liquidity_usd=d.get("liq"),
                                      chain=d["chain"], addr=d["addr"]) if can else 0.0
        # 31 Oct 2026 (Ali): removed robinhood_chain from this Oct-13-evidence downweight.
        # That 0.6x was based on a 5-trade, 0-win sample from BEFORE the 28 Oct fix to
        # layer14_revival's dead-on-arrival handling (robinhood_chain's actual problem back
        # then -- a feed/revival bug, not a strategy flaw). Current real evidence (cited below,
        # 30 Oct comment) is 52.6% win / +$938.63 net on n=38, and a fresher 83.3% on n=6 --
        # this old block was silently halving that weighting underneath the newer one
        # (0.6 x 1.3 = 0.78x actual, not the 1.3x the newer comment describes), which Ali
        # asked to fix when moving robinhood_chain + base to full weight. bsc stays
        # downweighted here -- it's also in DISABLED_CHAINS by default, so this mostly guards
        # the case it's manually re-enabled without fresh evidence.
        if stake and d["chain"] == "bsc":
            mult = _f("MC_EVM_STAKE_MULT", 0.3)
            stake = round(stake * (mult or 0.3), 2)
            stake = stake if stake >= 5.0 else 0.0
        # 30 Oct 2026 (Ali): per-chain sprint stake-weighting for the 72h/$20k flip attempt --
        # weight stakes toward chains with a REAL, currently-proven edge rather than treat every
        # qualifying chain equally. Checked live post-restart data before setting these (not a
        # guess): robinhood_chain 52.6% win/+$938.63 net (n=38) and separately 83.3%/+$432.99
        # (n=6) on a fresher slice -- genuinely strong, real money-like performance; base 50-100%
        # win on small samples (n=4-13), real and positive but thinner. Solana via THIS file
        # stays blocked entirely already (MC_SOLANA_ENABLED, see the gate above) -- this weighting
        # only ever applies to base/robinhood_chain/bsc, chains that already passed their own
        # quality gate. Only affects sprint (the book driving this specific 72h attempt); other
        # books keep their existing stake sizing unchanged.
        if stake and lane == "sprint":
            sprint_w = {"robinhood_chain": _f("SPRINT_WEIGHT_ROBINHOOD", 1.3), "robinhood": _f("SPRINT_WEIGHT_ROBINHOOD", 1.3),
                        "base": _f("SPRINT_WEIGHT_BASE", 1.0), "bsc": _f("SPRINT_WEIGHT_BSC", 0.3),
                        "solana": _f("SPRINT_WEIGHT_SOLANA", 0.3)}.get(d["chain"], 1.0)
            stake = round(stake * sprint_w, 2)
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
        # 8 Nov 2026 (Ali, live-wiring build): mirror this paper entry with a REAL buy, only if
        # ALL of the following hold -- books.is_live_armed() alone is never sufficient:
        #   1. pos was actually opened (never fire a real buy for a paper entry that was refused)
        #   2. this lane/book is listed in LIVE_BOOKS and (for sprint/core1/core2) the Phase-1
        #      cascade live-pause is not set (books.is_live_armed(lane))
        #   3. the chain is one swap_executor actually has a real buy function for -- only
        #      robinhood_chain and base, per Ali's explicit correction (Solana/bsc are not
        #      traded for real at all; solana is data/pattern-ID only, bsc has no live adapter
        #      and is 0% win rate anyway per the docstring above)
        #   4. executor.config.ready_for_chain(chain) passes -- EXECUTION_ENABLED=true AND the
        #      real per-chain key (EXECUTION_RHC_PRIVATE_KEY / EXECUTION_BASE_PRIVATE_KEY) is
        #      set. This is the SAME gate swap_executor's own real buy functions check
        #      internally via _refuse_unless_ready() -- checked again here, before spending any
        #      effort building a request, purely so a disarmed system logs nothing extra; the
        #      real functions still refuse-closed on their own if this check is ever bypassed.
        # The real buy is best-effort and never allowed to break the paper system: any failure
        # (including an exception) is recorded on the position and swallowed, never raised --
        # paper trading must keep working exactly as it does tonight even if live wiring breaks.
        if pos and d["chain"] in ("robinhood_chain", "base") and books.is_live_armed(lane):
            class _RealSizeSkip(Exception):
                """Internal sentinel: real-balance sizing already recorded its own
                live_attempted/live_ok/live_reason and called update_live_fields()
                before raising this -- the except block below must not overwrite
                that with a generic 'live buy hook raised' message."""
                pass
            try:
                from executor import config as _execcfg, swap_executor as _swap
                if _execcfg.EXECUTOR_CONFIG.execution_enabled and _execcfg.EXECUTOR_CONFIG.ready_for_chain(d["chain"]):
                    _buy_fn = (_swap.execute_buy_robinhood_chain if d["chain"] == "robinhood_chain"
                               else _swap.execute_buy_base)
                    # 14 Nov 2026 (Ali, explicit instruction): the real buy's dollar amount must
                    # come from the REAL wallet balance on this chain, never from `stake` (which
                    # is paper_ledger/books.equity()-derived -- pure paper P&L bookkeeping, zero
                    # connection to real capital). Ali's exact words: "It should be based on my
                    # real USDT amount size in my actual real wallet instead of the paper book's
                    # equity... it should track down real wallet balance and trade accordingly
                    # as per the logic which is defined for that module." `stake` and the paper
                    # open_paper() call above are deliberately left untouched -- paper trading's
                    # own bookkeeping, and the entry/qualification logic that decided `pos`
                    # should fire at all, are NOT part of this change.
                    #
                    # "the logic which is defined for that module" = the SAME bankroll_tier()
                    # percentage-of-wallet sizing executor/config.py already defines (seed tier:
                    # 30% of wallet per shot, Ali Sept 23 2026), just pointed at a live, real,
                    # per-chain on-chain balance read instead of the static TOTAL_WALLET_USD
                    # placeholder that logic used to run on.
                    _real_wallet_usd = _swap.get_real_wallet_usd(d["chain"])
                    if _real_wallet_usd is None or _real_wallet_usd < 5.0:
                        pos["live_attempted"] = True
                        pos["live_ok"] = False
                        pos["live_reason"] = (
                            f"refusing real buy: could not read a real {d['chain']} wallet balance "
                            f"(or it's below $5) -- never guessing or falling back to paper stake "
                            f"for a real buy amount"
                        )
                        paper_ledger.update_live_fields(pos)
                        raise _RealSizeSkip()
                    _real_tier = _execcfg.bankroll_tier(_real_wallet_usd)
                    _real_stake = round(_real_wallet_usd * _real_tier["position_pct"], 2)
                    _real_stake = _real_stake if _real_stake >= 5.0 else 0.0
                    if not _real_stake:
                        pos["live_attempted"] = True
                        pos["live_ok"] = False
                        pos["live_reason"] = (
                            f"refusing real buy: real {d['chain']} wallet is ${_real_wallet_usd:.2f}, "
                            f"sized real stake of ${_real_stake:.2f} is below the $5 floor"
                        )
                        paper_ledger.update_live_fields(pos)
                        raise _RealSizeSkip()
                    _result = _buy_fn(d["addr"], _real_stake)
                    pos["live_attempted"] = True
                    pos["live_ok"] = bool(_result.ok)
                    pos["live_reason"] = _result.reason
                    pos["live_tx"] = _result.tx_signature
                    if _result.ok:
                        pos["live"] = True
                        pos["live_amount_tokens"] = _result.filled_amount_tokens
                        # Fixed, NEVER mutated after this -- see paper_ledger._sell()'s own
                        # comment on why the real-sell size calculation needs an immutable
                        # original base, separate from live_amount_tokens (which _sell() keeps
                        # decrementing as a running "what's left" figure for reporting).
                        pos["live_amount_tokens_original"] = _result.filled_amount_tokens
                    paper_ledger.update_live_fields(pos)
            except _RealSizeSkip:
                # Already recorded (live_attempted/live_ok/live_reason + update_live_fields())
                # by the real-balance sizing checks above -- nothing more to do here.
                pass
            except Exception as _live_exc:  # noqa: BLE001
                try:
                    pos["live_attempted"] = True
                    pos["live_ok"] = False
                    pos["live_reason"] = f"live buy hook raised: {_live_exc}"
                    paper_ledger.update_live_fields(pos)
                except Exception:  # noqa: BLE001
                    pass
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
                              d["addr"], f"mcap=${d['mcap']:,.0f} age={d['age_min']}m", d["confirmations"], res, flush=True)
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

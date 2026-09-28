"""
Layer 0/0b -- Structural scoring engine.

Polls MadeOnSol (Solana + Robinhood Chain) and Mobula Pulse (Base/BSC/TON/ETH)
for new + trending tokens and scores each 0-100 (+ A/B/C/D band) on:

  - top10/20 holder concentration
  - LP lock status (post-graduation) / bonding-curve fill velocity (pre-graduation
    Solana, where LP lock is not yet a meaningful concept)
  - mint/freeze/update authority state
  - volume-to-liquidity trend
  - holder growth rate
  - bundler/sniper % at launch
  - liquidity depth relative to a typical position size (thin/deep flag)

Pre-graduation Solana tokens use a STRICTER, separate threshold set from
graduated/other-chain tokens, because pump.fun's pre-graduation rug/failure
rate (~98.6% per the spec) means a generic threshold would flood the feed.

MadeOnSol field source: /tokens/{mint}/risk, /tokens/{mint}/holders,
/tokens/{mint}/bundle (see README for confirmed endpoint list).
Mobula field source: /api/2/pulse (top10Holdings, snipersCount,
bundlersCount, noMintAuthority, balanceMutable, etc. -- confirmed against
docs.mobula.io as of Sep 2026).
"""
from dataclasses import dataclass
from typing import List, Optional, Literal, Tuple
import time

from config import CONFIG
from utils.http import get_json, ApiUnreachable, describe_fetch_failure
import state
from links import DEXSCREENER_CHAIN_SLUG
from layers.layer0d_point_in_time import fetch_birdeye_ohlcv, summarize_launch_window
from executor.rpc_pool import rpc_call

Chain = Literal["solana", "robinhood_chain", "base", "bsc", "ton", "ethereum"]


@dataclass
class RawSignals:
    """Normalized 0-1 (or None if unknown) signals, regardless of source API."""
    top10_holder_pct: Optional[float] = None          # 0-1, lower is better
    lp_locked_or_curve_healthy: Optional[bool] = None  # True is better
    mint_authority_revoked: Optional[bool] = None
    freeze_authority_revoked: Optional[bool] = None
    vol_to_liq_ratio: Optional[float] = None           # unitless, moderate is better
    holder_growth_rate_per_hr: Optional[float] = None  # new holders/hr, higher (to a point) better
    bundler_sniper_pct: Optional[float] = None         # 0-1, lower is better
    price_drawdown_from_peak_pct: Optional[float] = None  # Birdeye real launch-window shape, e.g. -80.0 = -80% off peak; None if unavailable/RHC
    liquidity_usd: Optional[float] = None
    is_pregraduation_solana: bool = False


@dataclass
class ScoreResult:
    score: int            # 0-100, higher = structurally safer
    band: str             # A/B/C/D
    liquidity_flag: str   # "thin" | "moderate" | "deep" | "unknown"
    reasons: list


# A typical position size Ali would actually try to enter with -- used only
# to flag thin-vs-deep liquidity, tune via TYPICAL_POSITION_USD env var later
# if needed.
TYPICAL_POSITION_USD = 500.0

# Thresholds are separate for pre-graduation Solana (stricter) vs everything
# else, per spec.
PREGRAD_SOL_BANDS = {"A": 80, "B": 65, "C": 45}   # else D
GRADUATED_OR_OTHER_BANDS = {"A": 70, "B": 50, "C": 30}  # else D


def score_token(sig: RawSignals) -> ScoreResult:
    reasons = []
    points = 0
    max_points = 0

    def add(weight, condition_points, reason=None):
        nonlocal points, max_points
        max_points += weight
        points += condition_points
        if reason:
            reasons.append(reason)

    # Reweighted Sept 25 2026 -- caught live: once bug #5's field-name fix
    # (and its own correction) started reading REAL renounced/isProxy data,
    # both real winners from Ali's Fomo screenshot scored band=C (46/100)
    # DESPITE having strong momentum (huge vol/liq, near-zero bundler/sniper
    # %) -- because renounced=False and isProxy=True were each zeroing out
    # a 20-point bucket. That data is honest, not a bug: these coins really
    # do have an active owner and a mutable contract. But the old weights
    # (40/100 combined on LP-mutability + mint/freeze authority) assume
    # you're evaluating a long-term hold. Ali's system exists to catch a
    # coin WHILE it's pumping and get in/out fast (S1c Fomo copy-trading,
    # manual buy/sell) -- almost no coin has renounced ownership in that
    # exact early window; renouncing (if it happens) comes later. Scoring
    # that near-universal state as a full zero on 40% of the total was
    # punishing the exact coins this system exists to catch.
    # Rebalanced: security flags (concentration/mutability/authority) still
    # count -- a coin can still lose real points here -- but momentum and
    # organic-growth signals (vol/liq, holder growth, bundler/sniper %,
    # i.e. "is this actually catching fire right now") now carry the
    # majority of the score, since that's what a sniper needs to weight
    # most for a fast in/out play. Old -> new weights: top10 20->15,
    # LP/curve 20->10, mint/freeze 20->10, vol/liq 15->25, holder growth
    # 10->20, bundler/sniper 15->20 (unchanged total: 100). Unknown-signal
    # fallback credit kept at the same FRACTION of its weight as before.

    # Holder concentration (weight 15)
    if sig.top10_holder_pct is not None:
        w = 15
        earned = w * max(0.0, 1.0 - sig.top10_holder_pct / 0.6)  # 0 pts if top10 >= 60%
        add(w, earned, f"top10 holds {sig.top10_holder_pct*100:.1f}%")
    else:
        add(15, 7.5, "top10 concentration unknown -- scored neutral")

    # LP lock / bonding curve (contract mutability) health (weight 10)
    if sig.lp_locked_or_curve_healthy is not None:
        w = 10
        add(w, w if sig.lp_locked_or_curve_healthy else 0,
            "LP locked / curve healthy" if sig.lp_locked_or_curve_healthy else "LP unlocked / curve unhealthy")
    else:
        add(10, 4, "LP/curve status unknown -- scored low-neutral")

    # Mint/freeze/update authority (weight 10)
    auth_bits = [b for b in (sig.mint_authority_revoked, sig.freeze_authority_revoked) if b is not None]
    if auth_bits:
        w = 10
        earned = w * (sum(1 for b in auth_bits if b) / len(auth_bits))
        add(w, earned, f"authority revoked: {sum(1 for b in auth_bits if b)}/{len(auth_bits)}")
    else:
        add(10, 3, "mint/freeze authority unknown -- scored low-neutral")

    # Volume-to-liquidity trend (weight 15) -- extreme ratios (wash trading /
    # about to rug) score low; moderate healthy ratio scores high.
    #
    # Recalibrated Sept 25 2026: caught live off Ali's named-coin backtest --
    # once bugs #1-#5 were fixed and this ratio started reading real data,
    # BOTH real BSC winners from Ali's own Fomo screenshot (和平熊猫: 89.35,
    # 友谊使者: 33.93) scored 0/15 here. The old thresholds (healthy <=3.0,
    # decayed to 0 by ~13) assume a high vol/liq ratio means wash trading --
    # but for a coin in its first hours of going viral (exactly what this
    # FOMO-copy-trading system is built to catch), a 30-90x ratio is what a
    # real pump looks like, not a red flag. The old curve was zeroing out
    # the exact signal the strategy is supposed to reward. Widened the
    # healthy plateau and flattened the decay so a real early pump keeps
    # real credit; only true extremes (~150x+) still trend toward the floor.
    # Ali: these numbers came from 2 real data points, not a large backtest
    # -- worth revisiting once more named coins can be scored (MadeOnSol
    # rate limit resets, more BSC/Base winners run through this).
    # Reweighted 25->15 Sept 27 2026 -- Ali's real rug-score diagnostic
    # (10 real labeled rugs/pump_dumps, live MadeOnSol+DexScreener data)
    # showed this ratio can't tell a real early pump from a post-rug quiet
    # token -- both land in the same "healthy" 0.2-15 plateau. Freed 10
    # points go to the new Birdeye launch-window-shape check below, which
    # the same diagnostic run showed is the signal that actually
    # distinguishes them (77% real hit rate vs this ratio's ~0%). Kept
    # (not zeroed) because it's still real, free signal on its own terms
    # (dead/wash-trading detection) -- just no longer over-weighted as if
    # it alone proved organic momentum.
    if sig.vol_to_liq_ratio is not None:
        w = 15
        r = sig.vol_to_liq_ratio
        if r < 0.2:
            earned = w * 0.3   # dead / no real trading
        elif r <= 15.0:
            earned = w * 1.0   # healthy range, incl. early-pump velocity
        else:
            earned = w * max(0.3, 1.0 - (r - 15.0) / 150.0)  # gentle decay, floors at 0.3 not 0
        add(w, earned, f"vol/liq ratio {r:.2f}")
    else:
        add(15, 6, "vol/liq trend unknown -- scored low-neutral")

    # Launch price/volume shape (weight 10) -- NEW Sept 27 2026, wired live
    # off the same real diagnostic. Uses Birdeye's real OHLCV candles from
    # the token's actual launch time (DexScreener's pairCreatedAt) to now,
    # via fetch_birdeye_ohlcv + summarize_launch_window (already built and
    # tested for backtest_point_in_time.py -- same functions, now also
    # called live from score_solana_mint below). Solana-chain only --
    # Robinhood Chain has no Birdeye mapping, see fetch_birdeye_ohlcv.
    # Severe drawdown-from-peak (the real "pumped then got dumped on"
    # signature every one of the 10 real labeled rugs/pump_dumps showed)
    # scores low; a token still near its peak scores high. This is the
    # single check in this file that reads actual price ACTION rather than
    # a structural snapshot -- everything else here can look "healthy" on
    # a token that has already collapsed, this can't.
    if sig.price_drawdown_from_peak_pct is not None:
        w = 10
        dd = sig.price_drawdown_from_peak_pct  # e.g. -80.0 = -80% off peak, 0 = still at peak
        if dd >= -10.0:
            earned = w * 1.0    # still near peak, healthy
        elif dd >= -60.0:
            earned = w * max(0.0, 1.0 - (-10.0 - dd) / 50.0)  # linear decay 10%->60% off peak
        else:
            earned = 0.0        # severe collapse -- the rug/dump signature
        add(w, earned, f"launch-window drawdown {dd:.1f}% from peak")
    else:
        add(10, 4, "launch-window price shape unknown -- scored low-neutral")

    # Holder growth rate (weight 20)
    if sig.holder_growth_rate_per_hr is not None:
        w = 20
        earned = w * min(1.0, sig.holder_growth_rate_per_hr / 30.0)
        add(w, earned, f"+{sig.holder_growth_rate_per_hr:.0f} holders/hr")
    else:
        add(20, 8, "holder growth unknown -- scored low-neutral")

    # Bundler/sniper % at launch (weight 20)
    if sig.bundler_sniper_pct is not None:
        w = 20
        earned = w * max(0.0, 1.0 - sig.bundler_sniper_pct / 0.5)  # 0 pts if >=50%
        add(w, earned, f"bundler/sniper {sig.bundler_sniper_pct*100:.1f}%")
    else:
        add(20, 8, "bundler/sniper % unknown -- scored low-neutral")

    raw_score = (points / max_points) * 100 if max_points else 0
    score = int(round(raw_score))

    bands = PREGRAD_SOL_BANDS if sig.is_pregraduation_solana else GRADUATED_OR_OTHER_BANDS
    if score >= bands["A"]:
        band = "A"
    elif score >= bands["B"]:
        band = "B"
    elif score >= bands["C"]:
        band = "C"
    else:
        band = "D"

    if sig.liquidity_usd is None:
        liq_flag = "unknown"
    elif sig.liquidity_usd < TYPICAL_POSITION_USD * 3:
        liq_flag = "thin"
    elif sig.liquidity_usd < TYPICAL_POSITION_USD * 20:
        liq_flag = "moderate"
    else:
        liq_flag = "deep"

    return ScoreResult(score=score, band=band, liquidity_flag=liq_flag, reasons=reasons)


# ---------------------------------------------------------------------------
# Source adapters -- map each API's raw JSON into RawSignals
# ---------------------------------------------------------------------------

def signals_from_madeonsol_risk(risk_json: dict, holders_json: dict, bundle_json: dict,
                                 is_pregraduation: bool, vol_to_liq_ratio: Optional[float] = None,
                                 liquidity_usd: Optional[float] = None,
                                 holder_growth_rate_per_hr: Optional[float] = None,
                                 goplus_mint_authority_revoked: Optional[bool] = None,
                                 goplus_freeze_authority_revoked: Optional[bool] = None,
                                 goplus_lp_locked: Optional[bool] = None,
                                 price_drawdown_from_peak_pct: Optional[float] = None,
                                 rpc_top10_holder_pct: Optional[float] = None) -> RawSignals:
    """risk_json from GET /tokens/{mint}/risk, holders_json from
    /tokens/{mint}/holders, bundle_json from /tokens/{mint}/bundle.

    vol_to_liq_ratio/liquidity_usd/holder_growth_rate_per_hr: real values the
    CALLER computes and passes in (this function stays pure/network-free,
    same convention as signals_from_mobula_pulse) -- see
    fetch_dexscreener_vol_liq and compute_holder_growth_rate_per_hr, wired
    in by score_solana_mint below. Fixed Sept 25 2026: these 3 used to be
    hardcoded None here always, with a comment claiming they were "wired in
    scheduler" -- they never were (confirmed by grep before this fix). That
    meant vol/liq (25pts) and holder growth (20pts) -- 45% of every Solana
    score's total weight -- were permanently defaulted to fixed neutral
    values regardless of any real per-token data. holder_growth_rate_per_hr
    stays None here for now specifically -- MadeOnSol's /tokens/{mint}/holders
    response only has a confirmed field for top10_share, not a total holder
    count, and guessing an unconfirmed field name is exactly the class of
    bug that caused Mobula bug #5 (see that function's docstring) -- so this
    is left honestly unwired pending a real sample response, rather than
    guessed.

    goplus_mint_authority_revoked/goplus_freeze_authority_revoked/
    goplus_lp_locked: real GoPlus-derived fallback values (see
    parse_goplus_solana_security), added Sept 25 2026. MadeOnSol's own
    /risk endpoint -- the only source for these 3 factors -- is gated
    behind MadeOnSol's PRO tier; Ali's key is BASIC, so risk_json's
    "factors" are empty for every Solana token, every time, until/unless
    he upgrades. These 3 params are used ONLY when the MadeOnSol
    factor-based value came back None (unknown) -- MadeOnSol's own data
    always wins when it's actually present, this only fills a real gap,
    never overrides real data."""
    factors = {f["key"]: f for f in risk_json.get("factors", [])} if risk_json else {}
    top10 = None
    if holders_json and "top10_share" in holders_json:
        top10 = holders_json["top10_share"] / 100.0
    if top10 is None:
        # Free RPC fallback (Sept 27 2026, see fetch_solana_top10_holder_pct's
        # docstring) -- only reached when MadeOnSol's own /holders data was
        # missing/paywalled. Real MadeOnSol data always wins when present,
        # same convention as the GoPlus authority fallbacks below.
        top10 = rpc_top10_holder_pct

    lp_locked_or_curve_healthy = None if is_pregraduation else _factor_ok(factors, "lp_lock")
    if lp_locked_or_curve_healthy is None and not is_pregraduation:
        lp_locked_or_curve_healthy = goplus_lp_locked

    mint_authority_revoked = _factor_ok(factors, "mint_authority")
    if mint_authority_revoked is None:
        mint_authority_revoked = goplus_mint_authority_revoked

    freeze_authority_revoked = _factor_ok(factors, "freeze_authority")
    if freeze_authority_revoked is None:
        freeze_authority_revoked = goplus_freeze_authority_revoked

    return RawSignals(
        top10_holder_pct=top10,
        lp_locked_or_curve_healthy=lp_locked_or_curve_healthy,
        mint_authority_revoked=mint_authority_revoked,
        freeze_authority_revoked=freeze_authority_revoked,
        vol_to_liq_ratio=vol_to_liq_ratio,
        holder_growth_rate_per_hr=holder_growth_rate_per_hr,
        # Fixed Sept 27 2026 -- caught live via Ali's rug-score diagnostic:
        # the real MadeOnSol /bundle response nests this under a "bundle"
        # object (confirmed live: {"bundle": {"held_pct_of_supply": 0.1027,
        # ...}, "wallets": [...]}), not as a flat top-level key. Reading
        # bundle_json.get("held_pct_of_supply", 0) at the top level always
        # missed, silently defaulting to 0 (= "verified zero bundler
        # share", the single largest false-safe signal in the score, worth
        # 20/100 points) for every token, even ones with real, sizeable
        # bundler holdings (ELONCOIN, a labeled rug, actually had 10.27%
        # held by same-slot bundler wallets). Also fixed a second bug in
        # the same line: held_pct_of_supply is already a 0-1 fraction per
        # MadeOnSol's own docs and the confirmed live value above -- the
        # old code then divided by 100 again, which (even after the path
        # fix) would have shrunk a real 10.27% down to 0.1027%. Missing
        # data now correctly falls through to None (scored low-neutral by
        # score_token), never a false 0.
        bundler_sniper_pct=(bundle_json.get("bundle", {}) or {}).get("held_pct_of_supply") if bundle_json else None,
        price_drawdown_from_peak_pct=price_drawdown_from_peak_pct,
        liquidity_usd=liquidity_usd,
        is_pregraduation_solana=is_pregraduation,
    )


def _factor_ok(factors: dict, key: str) -> Optional[bool]:
    f = factors.get(key)
    if not f:
        return None
    return f.get("status") == "ok"


# ---------------------------------------------------------------------------
# Holder growth rate -- real wiring, Sept 25 2026 (previously hardcoded to
# None everywhere in this file -- 20% of every score's weight was a fixed
# placeholder on every chain, every time, regardless of API access; see
# README's "Event-triggered re-scoring" section, which already flagged this
# as a known, honestly-scoped-out gap and named it "the natural next step").
# Uses state.py's holder_history (same append-only capped-list pattern as
# its existing mc_history) -- the caller is responsible for calling
# state.record_holder_point once per poll cycle with the current holder
# count, then state.get_holder_history + this function to turn that history
# into a rate. Kept here as a pure function (no state/network access) so it
# stays trivially testable, same as score_token.
# ---------------------------------------------------------------------------

# A single poll cycle's gap between two points is too short to trust as an
# hourly rate (extrapolating a 30-second gap out to "per hour" massively
# amplifies noise) -- require at least this much real elapsed time between
# the oldest and newest retained point before computing a rate at all.
HOLDER_GROWTH_MIN_ELAPSED_SECONDS = 5 * 60


def compute_holder_growth_rate_per_hr(history: List[Tuple[float, float]]) -> Optional[float]:
    """history: list of (ts, holder_count) tuples, any order, as returned by
    state.get_holder_history. Returns net new holders/hr between the oldest
    and newest retained point, or None if there's under 2 points yet, or the
    real time span between them is too short to trust (see
    HOLDER_GROWTH_MIN_ELAPSED_SECONDS) -- never fabricates a rate from too
    little data."""
    if len(history) < 2:
        return None
    points = sorted(history, key=lambda p: p[0])
    oldest_ts, oldest_count = points[0]
    newest_ts, newest_count = points[-1]
    elapsed_seconds = newest_ts - oldest_ts
    if elapsed_seconds < HOLDER_GROWTH_MIN_ELAPSED_SECONDS:
        return None
    return (newest_count - oldest_count) / (elapsed_seconds / 3600.0)


# ---------------------------------------------------------------------------
# Volume/liquidity ratio for Solana/RHC -- real wiring, Sept 25 2026.
# signals_from_madeonsol_risk used to hardcode vol_to_liq_ratio=None with a
# comment claiming it was "wired in scheduler" -- it never was anywhere in
# this codebase (confirmed by grep before writing this). MadeOnSol's own
# 3 endpoints this system calls (risk/holders/bundle) carry no volume or
# liquidity figure at all, so this uses DexScreener's real, free, keyless
# token-pairs endpoint instead -- confirmed against
# docs.dexscreener.com/api/reference Sept 25 2026:
#   GET https://api.dexscreener.com/token-pairs/v1/{chainId}/{tokenAddress}
#   -> pairs[].volume.h24, pairs[].liquidity.usd
# Reuses links.DEXSCREENER_CHAIN_SLUG, the same chain-slug mapping already
# verified live for this codebase's DexScreener links (links.py), rather
# than a second, separate guess at DexScreener's chain-id strings. This is
# the same DexScreener API family already used elsewhere in this codebase
# (layers/layer11_social_buzz.py's boost feed), just a different endpoint.
# ---------------------------------------------------------------------------

def fetch_dexscreener_vol_liq(chain: str, address: str) -> dict:
    """Picks the pair with the highest liquidity when a token has multiple
    pools -- the deepest pool is the most representative of real trading,
    not the first one returned. Never a hard dependency: any failure here
    (unsupported chain, no pairs, network error) just leaves the caller's
    vol_to_liq_ratio at None exactly as before this existed -- same
    degrade-gracefully convention as fetch_goplus_security.

    Also returns launch_ts_ms (DexScreener's real pairCreatedAt field,
    confirmed present on the pairs endpoint response per
    docs.dexscreener.com/api/reference Sept 26 2026) -- the actual
    real-world creation time of the deepest pool, added for
    backtest_point_in_time.py so a point-in-time backtest has a real
    anchor date instead of guessing. None if DexScreener didn't return it
    for this pair (nullable per their own schema) -- never a hard
    dependency, same as volume_24h/liquidity_usd above."""
    slug = DEXSCREENER_CHAIN_SLUG.get(chain)
    if not slug or not address:
        return {"ok": False, "reason": f"no DexScreener chain slug for chain={chain!r}"}
    result = get_json(f"https://api.dexscreener.com/token-pairs/v1/{slug}/{address}")
    if not result.get("ok"):
        return {"ok": False, "reason": describe_fetch_failure({"raw": result})}
    pairs = result.get("json")
    if not isinstance(pairs, list) or not pairs:
        return {"ok": False, "reason": "no pairs returned"}
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    volume_24h = (best.get("volume") or {}).get("h24")
    liquidity_usd = (best.get("liquidity") or {}).get("usd")
    launch_ts_ms = best.get("pairCreatedAt")
    if volume_24h is None or liquidity_usd is None:
        return {"ok": False, "reason": "pair missing volume/liquidity fields"}
    return {"ok": True, "volume_24h": volume_24h, "liquidity_usd": liquidity_usd,
            "launch_ts_ms": launch_ts_ms}


GOPLUS_CHAIN_IDS = {"bsc": "56", "base": "8453", "ethereum": "1"}  # GoPlus's numeric chain ids

# GoPlus's Solana token_security endpoint is a DIFFERENT URL shape from the
# EVM one above -- confirmed live Sept 25 2026 via a real diagnostic run
# (Ali's own terminal; the Cowork device bridge's network allowlist blocks
# gopluslabs.io outright, so this had to be confirmed outside the bridge):
#   GET https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses={mint}
# (no /{chain_id} path segment -- "solana" is baked into the path itself),
# and the result dict is keyed by the address EXACTLY as sent, not
# lowercased -- unlike the EVM endpoint's hex addresses, a Solana base58
# mint address is case-sensitive, so lowercasing it would silently break
# the result lookup.


def fetch_goplus_security(chain: str, address: str) -> dict:
    """Fallback security scan, added Sept 25 2026. Caught live: in Ali's real
    named-coin backtest, Mobula's Pulse response had NO "security" object at
    all for either real BSC winner scored -- lp_locked_or_curve_healthy,
    mint_authority_revoked and freeze_authority_revoked all came back
    "unknown" for both, costing ~26/100 points combined for reasons that
    have nothing to do with the coin actually being risky (Mobula just
    hadn't run/returned a security scan for them). This is a genuine data
    gap, not something the Mobula field-name fixes (bug #5) could close.

    GoPlus (docs.gopluslabs.io/reference/tokensecurityusingget_1) covers the
    same ground independently -- is_mintable, is_blacklisted/is_honeypot,
    lp_holders (lock detail), holder_count -- and is only called as a
    fallback, one extra request per token, and only when Mobula's own
    security data is genuinely missing (see signals_from_mobula_pulse
    below), to stay inside this codebase's per-cycle call budget. Works
    with or without CONFIG.goplus_api_key -- unauthenticated calls may
    still succeed at a lower rate limit per GoPlus's own historical public
    access; unconfirmed until run live. Never a hard dependency: any
    failure here just leaves the caller's signals at None/unknown exactly
    as before this fallback existed."""
    if not address:
        return {"ok": False, "reason": "no address given"}
    headers = {}
    if CONFIG.goplus_api_key:
        headers["Authorization"] = f"Bearer {CONFIG.goplus_api_key}"

    if chain == "solana":
        url = f"{CONFIG.goplus_base_url}/solana/token_security"
        result_key = address  # case-sensitive, see module note above
    else:
        goplus_chain = GOPLUS_CHAIN_IDS.get(chain)
        if not goplus_chain:
            return {"ok": False, "reason": f"no GoPlus chain mapping for chain={chain!r}"}
        url = f"{CONFIG.goplus_base_url}/token_security/{goplus_chain}"
        result_key = address.lower()

    result = get_json(url, headers=headers, params={"contract_addresses": address})
    if not result.get("ok"):
        return {"ok": False, "reason": describe_fetch_failure({"raw": result})}
    body = result.get("json") or {}
    token = (body.get("result") or {}).get(result_key)
    if not token:
        return {"ok": False, "reason": "address not present in GoPlus result"}
    return {"ok": True, "data": token}


# ---------------------------------------------------------------------------
# Solana GoPlus signal parsing -- real wiring, Sept 25 2026. Real field
# names confirmed via a live diagnostic run against
# https://api.gopluslabs.io/api/v1/solana/token_security (a real USDC mint,
# EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v, run from Ali's own terminal
# since the Cowork device bridge can't reach gopluslabs.io at all). NOT
# guessed -- a sibling project (unrelated, found via web search) hit this
# exact trap assuming mintable/freezable were nested as {"status": ...}
# sub-objects with a different shape than what the live API actually
# returns, and silently mis-parsed every token as a result. The real,
# confirmed shape from the live sample:
#   mintable:  {"authority": [...], "status": "1"}   -- "1" = CAN mint (authority live, i.e. NOT revoked), "0" = revoked
#   freezable: {"authority": [...], "status": "1"}   -- same "1"/"0" convention
#   lp_holders: [{"balance": "...", "is_locked": 0|1, ...}, ...] -- a list of
#     LP-token holders, each either locked or not. NOTE: this sample's
#     lp_holders[].percent field was NOT a normal 0-100 percentage (values
#     were in the hundreds of millions for this particular mint) -- rather
#     than guess what unit that's actually in, LP-lock health here is
#     computed from the "balance" field instead (a real token-amount figure,
#     unambiguous), as the fraction of total LP balance held by locked
#     holders.
# ---------------------------------------------------------------------------

def parse_goplus_solana_security(data: dict) -> dict:
    """data is the per-mint dict from fetch_goplus_security("solana", mint)'s
    "data" key. Returns {"mint_authority_revoked": Optional[bool],
    "freeze_authority_revoked": Optional[bool], "lp_locked": Optional[bool]}
    -- any field GoPlus didn't return, or whose shape doesn't match what was
    confirmed live, stays None (unknown) rather than being guessed."""
    out = {"mint_authority_revoked": None, "freeze_authority_revoked": None, "lp_locked": None}

    mintable = data.get("mintable")
    if isinstance(mintable, dict) and "status" in mintable:
        out["mint_authority_revoked"] = mintable["status"] == "0"

    freezable = data.get("freezable")
    if isinstance(freezable, dict) and "status" in freezable:
        out["freeze_authority_revoked"] = freezable["status"] == "0"

    lp_holders = data.get("lp_holders")
    if isinstance(lp_holders, list) and lp_holders:
        try:
            total = sum(float(h.get("balance", 0) or 0) for h in lp_holders)
            locked = sum(float(h.get("balance", 0) or 0) for h in lp_holders if h.get("is_locked"))
            if total > 0:
                out["lp_locked"] = (locked / total) >= 0.5
        except (TypeError, ValueError):
            pass  # malformed balance field -- stay None rather than guess

    return out


def signals_from_mobula_pulse(pulse_item: dict, chain: str = None,
                               holder_growth_rate_per_hr: Optional[float] = None) -> RawSignals:
    """pulse_item is one token object from GET /api/2/pulse (Mobula), used for
    Base/BSC/TON/Ethereum. `chain` (e.g. "bsc"/"base") enables the GoPlus
    fallback below -- optional/backward-compatible, omit it to skip the
    fallback (e.g. in isolated unit tests).

    Bug #5 fixed Sept 24 2026: caught live when Ali flagged that real winning
    coins (from tonight's named-coin backtest, after bugs #1-#4 were fixed
    and Mobula finally returned real data) were scoring band=C, ~42-44/100 --
    every signal except top10_holder_pct was coming back "unknown" and
    getting scored low-neutral, not because the coins were actually risky,
    but because this function was reading the WRONG field names/nesting for
    Mobula's real Pulse schema (confirmed against
    docs.mobula.io/guides/query-newly-listed-tokens-onchain):
      - holder count is `holdersCount`, not `holderCount`/`holders`
      - 24h volume is `volume_24h`, not `volume24h`
      - balanceMutable/noMintAuthority/isBlacklisted live INSIDE a nested
        `security` object, not at the top level of the pulse item -- they
        were always absent at pulse_item.get(...), so these 3 signals were
        unconditionally None/unknown for every token, every time.
    This was silently deflating every BSC/Base score since Layer 0b Mobula
    scoring was written -- not just tonight's backtest.

    GoPlus fallback added Sept 25 2026, CORRECTED same day: the first version
    of this fix (bug #5) used field names for Mobula's nested "security"
    object -- balanceMutable/noMintAuthority/isBlacklisted -- that were
    hallucinated from a doc summary and DO NOT EXIST in Mobula's real
    response. Confirmed against 4 real live tokens (Ali's diag_goplus.py
    run, Sept 25 2026): the actual keys are `renounced` (bool -- owner gave
    up contract control) and `isProxy` (bool -- upgradeable/mutable
    contract logic), plus tax fields. There is no blacklist/honeypot
    equivalent in Mobula's own data at all -- confirmed absent in all 4
    samples. This means the OLD code's `security.get(...)` lookups always
    returned None for all 3 signals, on every token, every time, silently
    -- it was ALWAYS falling through to GoPlus, never actually using
    Mobula's own (free, already-fetched) security data. Also: the fallback
    used to require ALL THREE signals to be None before calling GoPlus; now
    that real Mobula fields are read correctly, freeze_authority_revoked
    will almost always still be None (Mobula has nothing for it) while
    lp/mint may already be filled from Mobula -- fallback is now per-field
    so a GoPlus call still happens whenever freeze_authority_revoked is
    missing, not only when everything is missing."""
    security = pulse_item.get("security") or {}
    top10 = pulse_item.get("top10Holdings")
    snipers = pulse_item.get("snipersCount", 0) or 0
    bundlers = pulse_item.get("bundlersCount", 0) or 0
    holder_count = pulse_item.get("holdersCount")
    bundler_sniper_pct = None
    if holder_count:
        bundler_sniper_pct = min(1.0, (snipers + bundlers) / holder_count)

    # Mobula's real security fields (see docstring): isProxy true means the
    # contract logic can still be changed post-deploy -- treated as the
    # LP/curve-health-equivalent risk signal. renounced true means the
    # owner gave up contract control -- treated as the mint-authority
    # signal. Neither is a perfect 1:1 match for the original concept, but
    # both are real, present fields, unlike the old made-up ones.
    lp_locked = (not security["isProxy"]) if "isProxy" in security else None
    mint_revoked = security.get("renounced") if "renounced" in security else None
    freeze_revoked = None  # Mobula's real schema has no blacklist/honeypot equivalent -- always from GoPlus below

    if lp_locked is None or mint_revoked is None or freeze_revoked is None:
        address = pulse_item.get("address")
        if chain and address:
            gp = fetch_goplus_security(chain, address)
            if gp.get("ok"):
                d = gp["data"]
                if lp_locked is None:
                    lp_holders = d.get("lp_holders") or []
                    if lp_holders:
                        locked_pct = sum(
                            float(h.get("percent", 0) or 0) for h in lp_holders if h.get("is_locked")
                        )
                        lp_locked = locked_pct >= 0.5  # majority of LP locked/burned counts as healthy
                if mint_revoked is None:
                    is_mintable = d.get("is_mintable")
                    if is_mintable is not None:
                        mint_revoked = is_mintable == "0"  # GoPlus returns "0"/"1" strings
                if freeze_revoked is None:
                    is_blacklisted = d.get("is_blacklisted")
                    is_honeypot = d.get("is_honeypot")
                    if is_blacklisted is not None or is_honeypot is not None:
                        freeze_revoked = (is_blacklisted != "1") and (is_honeypot != "1")

    return RawSignals(
        top10_holder_pct=(top10 / 100.0) if top10 is not None else None,
        lp_locked_or_curve_healthy=lp_locked,
        mint_authority_revoked=mint_revoked,
        freeze_authority_revoked=freeze_revoked,
        vol_to_liq_ratio=_safe_div(pulse_item.get("volume_24h"), pulse_item.get("liquidity")),
        # Real wiring Sept 25 2026 -- caller (score_mobula_pulse_items) computes
        # this from state.py's holder_history (holdersCount is a confirmed
        # real Mobula field, already used above for bundler_sniper_pct) and
        # passes it in. Previously hardcoded None here always, with a comment
        # claiming it was "wired in scheduler" -- it never was anywhere in
        # this codebase (confirmed by grep before this fix).
        holder_growth_rate_per_hr=holder_growth_rate_per_hr,
        bundler_sniper_pct=bundler_sniper_pct,
        liquidity_usd=pulse_item.get("liquidity"),
        is_pregraduation_solana=False,
    )


def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


# ---------------------------------------------------------------------------
# Live fetchers
# ---------------------------------------------------------------------------

SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def fetch_solana_top10_holder_pct(mint: str) -> Optional[float]:
    """Free alternative to MadeOnSol's PRO-gated /holders endpoint (403
    tier_required on Ali's BASIC key, confirmed live Sept 27 2026, no free
    fix on MadeOnSol's own side). Solana's own getTokenLargestAccounts
    RPC method returns the top 20 holder token accounts for any mint,
    no signup, no key -- via the same free RPC pool already used
    elsewhere in this repo (executor/rpc_pool.py, live-tested by Ali Sept
    23 2026). Combined with getTokenSupply for the real total, this
    reproduces top10 holder concentration -- the exact signal the PRO
    paywall blocks -- for $0/month instead of $43-49/month.

    Returns None (never a fabricated 0%) on any failure: RPC pool
    exhausted/rate-limited, mint not found, or zero/unknown supply --
    same "missing data stays unknown, never fakes safe" convention as
    every other optional signal in this module."""
    largest = rpc_call("solana", "getTokenLargestAccounts", [mint])
    if not largest.get("ok"):
        return None
    accounts = (largest.get("result") or {}).get("value") or []
    if not accounts:
        return None
    supply_resp = rpc_call("solana", "getTokenSupply", [mint])
    if not supply_resp.get("ok"):
        return None
    supply_value = (supply_resp.get("result") or {}).get("value") or {}
    total_supply = supply_value.get("uiAmount")
    if not total_supply:
        return None
    top10_amount = sum(float(a.get("uiAmount") or 0) for a in accounts[:10])
    return min(1.0, top10_amount / total_supply)


def fetch_solana_holder_count(mint: str) -> Optional[int]:
    """Free real per-cycle holder-count signal (Sept 27 2026) -- feeds
    state.record_holder_point/get_holder_history so
    compute_holder_growth_rate_per_hr (above) finally gets real Solana
    data. Previously this was permanently None for Solana: MadeOnSol's
    own /holders response has no confirmed total-holder-count field (see
    signals_from_madeonsol_risk's docstring), so 20 of every score's 100
    points -- holder growth, arguably the single most direct "real humans
    are buying this right now" moonshot-early signal -- sat unused for
    every Solana token, every time.

    Uses getProgramAccounts on the SPL Token program, filtered to this
    mint's token accounts (dataSize 165 = a standard SPL token account;
    memcmp at offset 0 matches the mint pubkey), with dataSlice length 0
    so only account metadata (not each account's data payload) comes
    back -- keeps the response small even for a token with many holders.
    One token account is a close proxy for one holder on a fresh launch
    (the case this system scores), not a perfect count (a wallet can hold
    more than one token account for the same mint in rare setups) -- good
    enough for a growth RATE, which only needs relative change over time,
    not an exact headcount. Returns None on any RPC failure."""
    params = [
        SPL_TOKEN_PROGRAM_ID,
        {
            "encoding": "base64",
            "dataSlice": {"offset": 0, "length": 0},
            "filters": [
                {"dataSize": 165},
                {"memcmp": {"offset": 0, "bytes": mint}},
            ],
        },
    ]
    result = rpc_call("solana", "getProgramAccounts", params)
    if not result.get("ok"):
        return None
    accounts = result.get("result")
    if accounts is None:
        return None
    return len(accounts)



def fetch_solana_token_deployer(mint: str) -> Optional[str]:
    """Finds a Solana mint's real deployer wallet from its own oldest
    on-chain transaction -- unblocks the deployer rug-history and
    dev-holding-% signals (checklist Sept 27 2026), since MadeOnSol's own
    /deployer-hunter/alerts response carries only a tier label + SOL
    balance, never the deployer's actual wallet address (confirmed by
    reading the real fixture,
    tests/fixtures/madeonsol_deployer_alerts_sample.json -- no
    deployer_address/deployer_wallet field exists on that endpoint).

    Uses getSignaturesForAddress against the MINT account itself (not the
    pump.fun program -- a mint's own transaction history is short and
    specific to it), taking the OLDEST signature in the returned page as
    the mint-creation transaction, then getTransaction (jsonParsed) on it
    -- the transaction's first account (accountKeys[0]) is the fee payer,
    which for a token-creation transaction is the deployer.

    Real limitation, stated plainly: getSignaturesForAddress returns
    newest-first, and this fetches ONE page (limit=1000, the RPC max) --
    if a mint somehow already has more than 1000 transactions by the time
    this runs (very unlikely for a fresh Layer 1 alert, the only real
    caller), the true oldest signature would be missed and this would
    return a wrong wallet. Fine for its actual use case (brand-new tokens
    from Layer 1's real-time deployer alerts), not safe to reuse for an
    old, already-active token without adding real pagination first.

    Returns None (never a guessed wallet) on any RPC failure, an empty
    signature list, or a malformed transaction."""
    sigs_resp = rpc_call("solana", "getSignaturesForAddress", [mint, {"limit": 1000}])
    if not sigs_resp.get("ok"):
        return None
    sigs = sigs_resp.get("result") or []
    if not sigs:
        return None
    oldest = sigs[-1].get("signature") if isinstance(sigs[-1], dict) else None
    if not oldest:
        return None
    tx_resp = rpc_call("solana", "getTransaction",
                        [oldest, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    if not tx_resp.get("ok"):
        return None
    tx = tx_resp.get("result")
    if not tx:
        return None
    try:
        account_keys = tx["transaction"]["message"]["accountKeys"]
        first = account_keys[0]
        return first.get("pubkey") if isinstance(first, dict) else first
    except (KeyError, TypeError, IndexError):
        return None


def fetch_solana_dev_holding_pct(mint: str, deployer_wallet: Optional[str]) -> Optional[float]:
    """Real dev-wallet current-holding-% (checklist Sept 27 2026 -- GMGN,
    BullX, Photon, Trojan, Axiom all publish "dev holdings" as a tracked
    signal; this repo didn't have it before tonight). NOT the same lookup
    as fetch_solana_top10_holder_pct's getTokenLargestAccounts -- that
    returns top TOKEN ACCOUNTS, not owner wallets, so it can't directly
    answer "does the deployer hold X%" without an extra getAccountInfo
    per account. This instead calls getTokenAccountsByOwner directly for
    the deployer's own wallet + this mint -- the precise, correct RPC
    method for "how much of this mint does this wallet hold," one call,
    no extra lookups needed.

    Needs a real deployer wallet address -- see
    fetch_solana_token_deployer's docstring for why MadeOnSol's own alert
    data doesn't carry one; pass None (e.g. deployer lookup itself
    failed) and this returns None rather than guessing. NOT a flat 20%
    cutoff when this gets wired into scoring -- on pump.fun's bonding-
    curve launch there's no team pre-mine by default, so a "normal" dev
    holding is usually low single digits or 0%; graduate any future
    scoring the same way top10_holder_pct already is, not one arbitrary
    threshold (see FINAL_CHECKLIST_2026-09-27.md).

    Returns None (never a guessed %) on any RPC failure, missing supply,
    or zero total supply."""
    if not mint or not deployer_wallet:
        return None
    accounts_resp = rpc_call("solana", "getTokenAccountsByOwner",
                              [deployer_wallet, {"mint": mint}, {"encoding": "jsonParsed"}])
    if not accounts_resp.get("ok"):
        return None
    accounts = (accounts_resp.get("result") or {}).get("value") or []
    held = 0.0
    for acc in accounts:
        try:
            ui_amount = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"]
            held += float(ui_amount or 0)
        except (KeyError, TypeError):
            continue
    supply_resp = rpc_call("solana", "getTokenSupply", [mint])
    if not supply_resp.get("ok"):
        return None
    supply_value = (supply_resp.get("result") or {}).get("value") or {}
    total_supply = supply_value.get("uiAmount")
    if not total_supply:
        return None
    return min(1.0, held / total_supply)



def classify_dev_holding_pct(pct: Optional[float]) -> str:
    """Graduated classification for fetch_solana_dev_holding_pct's result
    -- deliberately NOT a flat 20% cutoff (Ali asked whether 20% was
    normal Sept 27 2026; it isn't). On pump.fun's bonding-curve launch
    there's no team pre-mine by default, so a "normal" dev holding is
    usually low single digits or 0%. Returns "none" (no tag warranted),
    "notable" (worth surfacing, not alarming), or "risk" (real
    concentration concern) -- mirrors the same tag convention as
    Layer 3's backing check and Layer 11's buzz check (scheduler.py:
    alert.set_tag(...), only when tag != "none"). Returns "unknown" if
    pct itself is None (RPC failure upstream -- never silently treated
    as "none/safe")."""
    if pct is None:
        return "unknown"
    if pct < 0.05:
        return "none"
    if pct <= 0.10:
        return "notable"
    return "risk"



def fetch_solana_wallet_first_seen_ts(wallet: str) -> Optional[int]:
    """Real deployer wallet-age signal (checklist Sept 27/28 2026 --
    Ali's original ask was literal deployer RUG-HISTORY, i.e. "how did
    this wallet's past token launches perform," but that needs decoding
    pump.fun's own "create" instruction to find a wallet's past launches
    at all, and this codebase has a deliberate, existing rule against
    guessing an instruction discriminator without real confirmed traffic
    (see pumpfun_trades.py's docstring -- built that way on purpose after
    getting burned by exactly that mistake before). Not breaking that
    discipline solo overnight with no way to verify against real data.

    Wallet AGE is the safe, verifiable, well-established adjacent signal
    instead: a wallet funded and used for the first time minutes before
    deploying a token is a classic burner-wallet pattern real rug-
    checking tools (Rugcheck, Bubblemaps and others) already track. Uses
    the SAME technique as fetch_solana_token_deployer -- oldest
    getSignaturesForAddress entry -- but on the wallet itself, and reads
    blockTime directly off that entry (no second getTransaction call
    needed, getSignaturesForAddress already returns it).

    Same limitation as fetch_solana_token_deployer: single page (limit
    1000), so a wallet with 1000+ transactions already would have its
    true first-seen time missed. Fine for a genuinely fresh burner wallet
    (the case this signal exists to catch), not reliable for an old,
    highly active wallet -- which is a real caller-worth-knowing gap
    when reading a "safe/no signal" result from this signal on a wallet
    with heavy prior activity.

    Returns None (never a guessed timestamp) on any RPC failure, empty
    history, or missing blockTime."""
    sigs_resp = rpc_call("solana", "getSignaturesForAddress", [wallet, {"limit": 1000}])
    if not sigs_resp.get("ok"):
        return None
    sigs = sigs_resp.get("result") or []
    if not sigs:
        return None
    oldest = sigs[-1]
    if not isinstance(oldest, dict):
        return None
    return oldest.get("blockTime")


def classify_deployer_wallet_age(first_seen_ts: Optional[int], now_ts: Optional[float] = None) -> str:
    """Graduated classification of fetch_solana_wallet_first_seen_ts's
    result. now_ts defaults to real wall-clock time (module-level `time`)
    -- overridable in tests for a deterministic clock, same pattern as
    compute_holder_growth_rate_per_hr elsewhere in this file.

    Returns "unknown" if first_seen_ts is None (RPC failure upstream,
    never silently treated as safe), "fresh" (<1 hour old -- classic
    burner-wallet red flag), "new" (1-24 hours -- worth noting, not
    alarming), or "established" (>24 hours -- no signal either way, most
    real wallets)."""
    if first_seen_ts is None:
        return "unknown"
    if now_ts is None:
        now_ts = time.time()
    age_hours = (now_ts - first_seen_ts) / 3600.0
    if age_hours < 1:
        return "fresh"
    if age_hours < 24:
        return "new"
    return "established"


def fetch_madeonsol_token_risk(mint: str, chain: Chain = "solana") -> dict:
    """Real bug caught live Sept 24 2026: this used to always return
    ok=True no matter what, even when all 3 sub-calls failed (e.g.
    MadeOnSol's free-key rate limit) -- signals_from_madeonsol_risk then
    silently treated the empty/missing data as "no risk flags found" and
    score_token handed back a fabricated neutral score. Caught because 6
    completely different real coins all scored an identical 49/100
    "everything unknown" in the same run -- that's not a real score, that's
    every signal defaulting because there was no data at all. Now: if
    every one of the 3 sub-calls failed, this is a real fetch failure, not
    a token with no risk flags -- surfaced as ok=False so the caller skips
    it instead of silently mis-scoring it. A PARTIAL failure (1-2 of 3)
    still degrades gracefully -- score_token already handles individual
    unknown signals fine, no reason to throw away 2 good calls over 1 bad
    one."""
    if not CONFIG.madeonsol_api_key:
        return {"ok": False, "reason": "MADEONSOL_API_KEY not configured"}
    # Real daily-budget gate added Sept 25 2026 -- this call spends 3 real
    # MadeOnSol calls at once (risk+holders+bundle), so it needs 3 full
    # slots of headroom, not just 1 -- see state.py's
    # madeonsol_budget_remaining() docstring for the confirmed real 200/day
    # BASIC-tier cap this protects against. Fails closed rather than
    # partially spending the day's remaining budget on a call that would
    # get rejected anyway.
    if state.madeonsol_budget_remaining() < 3:
        return {"ok": False, "reason": "MadeOnSol daily call budget exhausted "
                                        f"({state.madeonsol_calls_today()}/{state.MADEONSOL_DAILY_BUDGET})"}
    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    out = {}
    failures = []
    for name, path in [
        ("risk", f"{prefix}/tokens/{mint}/risk"),
        ("holders", f"{prefix}/tokens/{mint}/holders"),
        ("bundle", f"{prefix}/tokens/{mint}/bundle"),
    ]:
        result = get_json(f"{CONFIG.madeonsol_base_url}{path}", headers=headers)
        state.record_madeonsol_calls(1)
        out[name] = result
        if not result.get("ok"):
            failures.append(f"{name}: {describe_fetch_failure({'raw': result})}")
    if len(failures) == 3:
        return {"ok": False, "reason": "; ".join(failures)}
    return {"ok": True, "data": out}


def fetch_mobula_pulse(chain_id: str) -> dict:
    """chain_id examples: 'evm:8453' (Base), 'evm:56' (BSC), 'evm:1' (Ethereum).
    TON coverage is unconfirmed in Mobula's public docs as of this build --
    flagged in README, code path left in place for when Ali's key can confirm it.

    Bug #1 fixed Sept 24 2026: this was sending a bare `Authorization:
    <key>` header with no "Bearer " prefix -- every OTHER Mobula call in
    this codebase (layer10_insider_cluster.py, layer6_exit_realizable.py,
    wallet_balance.py) correctly uses "Bearer <key>". Caught live: Ali's
    BSC named-coin backtest failed tonight while Solana/RHC calls at
    least reached MadeOnSol -- this was the actual reason, not a MadeOnSol
    rate-limit spillover. Almost certainly means every Layer 0b BSC/Base
    Pulse call in production has been silently failing (401) since this
    was written -- not a today-only issue.

    Bug #2 fixed Sept 24 2026 (same night, re-run after fixing #1 surfaced
    a NEW failure -- HTTP 500 straight from Mobula): this call was missing
    the `assetMode` and `model` query params. Mobula's own docs example
    (docs.mobula.io/guides/query-newly-listed-tokens-onchain) is
    `GET /api/2/pulse?assetMode=false&chainId=solana:solana&model=default`
    -- omitting assetMode/model was apparently enough to make Mobula's
    backend throw a 500 instead of a clean 400. Added both with the
    documented defaults."""
    headers = {}
    if CONFIG.mobula_api_key:
        headers["Authorization"] = f"Bearer {CONFIG.mobula_api_key}"
    result = get_json(
        f"{CONFIG.mobula_base_url}/api/2/pulse",
        headers=headers,
        params={"chainId": chain_id, "assetMode": "false", "model": "default"},
    )
    return result
# Bug #4 fixed Sept 24 2026 (found after #1-#3 still left a 500): the chain_id
# VALUES every caller passed in were wrong. Per Mobula's own REST reference
# (docs.mobula.io/rest-api-reference/endpoint/pulse-get), EVM chains are
# addressed as `evm:<numeric chainId>` (their own example: evm:8453 for
# Base) -- NOT `base:base` / `bnb:bnb` / `ethereum:ethereum`, which were
# never valid Mobula chain identifiers. Mobula's backend throws an
# unhandled 500 on an unrecognized chain id instead of a clean 400 -- which
# is exactly what made this look like a server-side outage for 3 fix
# attempts. The rest of this codebase already knew the right format (see
# the evm:4663 / Robinhood Chain convention in utils/swap_quotes.py /
# README) -- this call site just never matched it. Fixed at every caller:
# scan_stage1 below, scheduler.py's MOBULA_PULSE_CHAINS, and
# backtest_named_coins.py.


def flatten_mobula_pulse_response(pulse_json) -> list:
    """Bug #3 fixed Sept 24 2026: every call site in this codebase was
    reading `pulse_json["data"]` as if the Pulse response were a flat list.
    It isn't -- confirmed against Mobula's own docs
    (docs.mobula.io/guides/query-newly-listed-tokens-onchain): the real
    shape is {"new": {"data": [...]}, "bonding": {"data": [...]}, "bonded":
    {"data": [...]}} -- three lifecycle buckets, each with its own nested
    "data" array, no top-level "data" key at all. So even once bugs #1 and
    #2 above are fixed and Mobula returns 200, the old parsing silently
    returned an empty list every time (a false "not present in current
    pulse snapshot" / empty-scan result, not a fetch error -- meaning this
    was invisible unless someone checked the actual item count). This
    flattens all three buckets into one list, same shape callers already
    expect (list of raw pulse token dicts)."""
    if not isinstance(pulse_json, dict):
        return []
    out = []
    for bucket in ("new", "bonding", "bonded"):
        section = pulse_json.get(bucket)
        if isinstance(section, dict):
            items = section.get("data")
            if isinstance(items, list):
                out.extend(items)
    return out


def score_mobula_pulse_items(chain: str, items: list) -> list:
    """Scores every item already present in ONE Mobula Pulse response -- no
    per-token re-fetch. (scan_stage1 below calls fetch_mobula_pulse again for
    every mint even though one call already returns the whole list -- fine
    for its original small-scale use, but scheduler.py uses THIS function
    instead for the live Layer 0b discovery loop so the Pulse fetch stays at
    one call per chain per cycle, not one call per token -- see README.)

    Real holder-growth-rate wiring, Sept 25 2026 (see
    compute_holder_growth_rate_per_hr's docstring): every call here records
    the CURRENT holder count (Mobula's confirmed-real `holdersCount` field,
    already used for bundler_sniper_pct above) into state.py's per-token
    holder_history, then reads back whatever history has accumulated across
    past cycles to compute a real rate. First time a token is seen there's
    only 1 point yet, so the rate stays None (same "not enough data" path
    compute_holder_growth_rate_per_hr already has) -- it fills in and starts
    contributing real points once a token has been polled across at least
    HOLDER_GROWTH_MIN_ELAPSED_SECONDS of wall-clock time, same natural
    warm-up as Layer 8's mc_history momentum tracking."""
    results = []
    for item in items:
        address = item.get("address")
        holder_count = item.get("holdersCount")
        holder_growth_rate_per_hr = None
        if address and holder_count is not None:
            state.record_holder_point(address, holder_count)
            holder_growth_rate_per_hr = compute_holder_growth_rate_per_hr(state.get_holder_history(address))
        sig = signals_from_mobula_pulse(item, chain, holder_growth_rate_per_hr=holder_growth_rate_per_hr)
        results.append({
            "chain": chain,
            "address": address,
            "score": score_token(sig),
            "raw": item,
        })
    return results


def score_solana_mint(mint: str, chain: str, is_pregraduation: bool) -> dict:
    """Single-mint MadeOnSol scoring -- 3 calls (risk/holders/bundle) plus
    one DexScreener call for real vol/liq data (see fetch_dexscreener_vol_liq
    -- added Sept 25 2026, MadeOnSol's own 3 endpoints carry no volume or
    liquidity figure), plus (Solana only, added Sept 27 2026) one Birdeye
    OHLCV call for real launch-window price shape (see fetch_birdeye_ohlcv
    -- the signal Ali's real rug-score diagnostic showed actually catches a
    post-pump collapse, unlike anything else in this function). Costly
    enough per token that scheduler.py only calls this for a bounded subset
    of Layer 1's deployer alerts (elite tier only, capped per cycle), not
    every discovered mint -- see README's call-budget section for why. The
    Birdeye call adds no MadeOnSol budget cost (separate free-tier account,
    30k compute units/month, 1 req/sec) but does add one more real HTTP
    call per scored mint -- worth knowing if that budget ever needs
    tightening too."""
    raw = fetch_madeonsol_token_risk(mint, chain)
    if not raw.get("ok"):
        return {"chain": chain, "address": mint, "error": raw.get("reason", "fetch failed")}
    d = raw["data"]

    # Real vol/liq data (Sept 25 2026 fix, see fetch_dexscreener_vol_liq's
    # docstring) -- never a hard dependency, any failure here just leaves
    # vol_to_liq_ratio/liquidity_usd at None exactly as before this existed.
    vol_to_liq_ratio = None
    liquidity_usd = None
    dex = fetch_dexscreener_vol_liq(chain, mint)
    if dex.get("ok"):
        liquidity_usd = dex["liquidity_usd"]
        if liquidity_usd:
            vol_to_liq_ratio = dex["volume_24h"] / liquidity_usd

    # GoPlus fallback (Sept 25 2026, see parse_goplus_solana_security's
    # docstring) -- only called when MadeOnSol's own /risk call actually
    # failed (the real, live symptom of Ali's BASIC-tier key hitting
    # MadeOnSol's PRO gate). Never called when /risk succeeded, so real
    # MadeOnSol data is never second-guessed by a fallback source -- this
    # only fills the gap PRO-gating leaves, for Solana only (chain=="solana"
    # -- GoPlus's Solana endpoint doesn't cover Robinhood Chain, and RHC's
    # MadeOnSol tier coverage is unconfirmed anyway, see module docstring).
    goplus_mint_authority_revoked = None
    goplus_freeze_authority_revoked = None
    goplus_lp_locked = None
    if chain == "solana" and not d["risk"].get("ok"):
        gp = fetch_goplus_security("solana", mint)
        if gp.get("ok"):
            parsed = parse_goplus_solana_security(gp["data"])
            goplus_mint_authority_revoked = parsed["mint_authority_revoked"]
            goplus_freeze_authority_revoked = parsed["freeze_authority_revoked"]
            goplus_lp_locked = parsed["lp_locked"]

    # Real launch-window price shape (Birdeye), wired live Sept 27 2026 --
    # same fetch_birdeye_ohlcv/summarize_launch_window functions already
    # built and tested for backtest_point_in_time.py, now also called here
    # so the LIVE score sees them, not just the backtest. Never a hard
    # dependency -- any failure (no key, RHC unsupported, token too new for
    # a candle yet) just leaves price_drawdown_from_peak_pct at None,
    # exactly like every other optional signal in this function. Chain
    # gate mirrors fetch_birdeye_ohlcv's own (RHC has no Birdeye mapping).
    price_drawdown_from_peak_pct = None
    if chain == "solana" and dex.get("ok") and dex.get("launch_ts_ms"):
        window_start = int(dex["launch_ts_ms"] / 1000)
        window_end = int(time.time())
        if window_end > window_start:
            ohlcv = fetch_birdeye_ohlcv(chain, mint, window_start, window_end, interval="15m")
            if ohlcv.get("ok"):
                summary = summarize_launch_window(ohlcv.get("candles") or [])
                if summary.get("ok"):
                    price_drawdown_from_peak_pct = summary["drawdown_from_peak_pct"]

    # Free RPC fallback for top10 holder concentration (Sept 27 2026, see
    # fetch_solana_top10_holder_pct's docstring) -- only spent when
    # MadeOnSol's own /holders call actually failed (real symptom of the
    # PRO-tier paywall on Ali's BASIC key), so real MadeOnSol data is never
    # second-guessed, same convention as the GoPlus fallback above.
    rpc_top10_holder_pct = None
    if chain == "solana" and not d["holders"].get("ok"):
        rpc_top10_holder_pct = fetch_solana_top10_holder_pct(mint)

    # Free real holder-count -> real holder_growth_rate_per_hr (Sept 27
    # 2026, see fetch_solana_holder_count's docstring) -- previously always
    # None for Solana. Records this cycle's real count, then computes the
    # rate from whatever history has accumulated across previous cycles
    # (needs >=2 points spaced far enough apart -- see
    # compute_holder_growth_rate_per_hr -- so this signal fills in over the
    # first couple of poll cycles a token is tracked, not instantly).
    holder_growth_rate_per_hr = None
    if chain == "solana":
        holder_count = fetch_solana_holder_count(mint)
        if holder_count is not None:
            state.record_holder_point(mint, holder_count)
        holder_growth_rate_per_hr = compute_holder_growth_rate_per_hr(state.get_holder_history(mint))

    sig = signals_from_madeonsol_risk(
        d["risk"].get("json") or {}, d["holders"].get("json") or {}, d["bundle"].get("json") or {},
        is_pregraduation, vol_to_liq_ratio=vol_to_liq_ratio, liquidity_usd=liquidity_usd,
        holder_growth_rate_per_hr=holder_growth_rate_per_hr,
        goplus_mint_authority_revoked=goplus_mint_authority_revoked,
        goplus_freeze_authority_revoked=goplus_freeze_authority_revoked,
        goplus_lp_locked=goplus_lp_locked,
        price_drawdown_from_peak_pct=price_drawdown_from_peak_pct,
        rpc_top10_holder_pct=rpc_top10_holder_pct,
    )
    return {"chain": chain, "address": mint, "score": score_token(sig)}


def free_recheck_solana_signals(mint: str) -> dict:
    """Cheap, keyless re-check of a Solana mint's two free RPC signals (top10
    holder concentration, holder growth rate) -- ZERO MadeOnSol budget spent.

    Built Sept 28 2026 for state.py's soft-fail watch list (see
    watch_add/get_soft_fail_watch's docstrings), which exists to close a real
    gap Ali flagged live: _maybe_queue_rescan only re-queues a D-band token
    for a real re-score when a fresh market-cap point comes in for it, and
    the ONLY source of fresh MC points today is Layer 2's tracked-KOL-wallet
    feed. A token no tracked wallet ever trades never gets a second MC point,
    so it never re-queues -- even if it quietly becomes structurally clean
    (liquidity locked, concentration drops, organic holder growth starts)
    within its first hours, exactly the scenario Ali described. This lets the
    scheduler check for that kind of real improvement on its own cadence,
    independent of KOL activity, without spending real (budget-capped) money
    on a full MadeOnSol re-score for every watched token every cycle.

    Returns {"top10_holder_pct": Optional[float],
    "holder_growth_rate_per_hr": Optional[float]} -- either can be None on an
    RPC failure, same as their underlying fetchers; never fabricates a value.
    Also records the holder point via state.record_holder_point (same as
    score_solana_mint's real wiring) so a later real re-score, if this
    triggers one, sees continuous history instead of a cold start."""
    top10 = fetch_solana_top10_holder_pct(mint)
    holder_count = fetch_solana_holder_count(mint)
    if holder_count is not None:
        state.record_holder_point(mint, holder_count)
    growth = compute_holder_growth_rate_per_hr(state.get_holder_history(mint))
    return {"top10_holder_pct": top10, "holder_growth_rate_per_hr": growth}


def scan_stage1(mints_by_chain: dict) -> list:
    """mints_by_chain: {"solana": [(mint, is_pregrad), ...], "base": [mint,...], ...}
    Returns list of dicts: {chain, address, score_result}."""
    results = []

    for mint, is_pregrad in mints_by_chain.get("solana", []):
        raw = fetch_madeonsol_token_risk(mint, "solana")
        if not raw.get("ok"):
            results.append({"chain": "solana", "address": mint, "error": raw.get("reason", "fetch failed")})
            continue
        d = raw["data"]
        sig = signals_from_madeonsol_risk(
            d["risk"].get("json") or {}, d["holders"].get("json") or {}, d["bundle"].get("json") or {},
            is_pregrad,
        )
        results.append({"chain": "solana", "address": mint, "score": score_token(sig)})

    for chain, chain_id in [("bsc", "evm:56"), ("base", "evm:8453")]:  # Base re-enabled Sept 24 2026 (Ali: Fomo trades Base too) -- ethereum/ton still dropped. evm:<numeric chainId> is Mobula's real format (bug #4, fixed Sept 24 2026) -- "bnb:bnb"/"base:base" were never valid.
        for mint in mints_by_chain.get(chain, []):
            raw = fetch_mobula_pulse(chain_id)
            if not raw.get("ok"):
                results.append({"chain": chain, "address": mint, "error": "mobula pulse fetch failed"})
                continue
            items = flatten_mobula_pulse_response(raw.get("json"))
            match = next((i for i in items if i.get("address", "").lower() == mint.lower()), None)
            if not match:
                results.append({"chain": chain, "address": mint, "error": "not present in current pulse snapshot"})
                continue
            sig = signals_from_mobula_pulse(match, chain)
            results.append({"chain": chain, "address": mint, "score": score_token(sig)})

    return results

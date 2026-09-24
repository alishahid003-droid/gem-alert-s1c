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
from typing import Optional, Literal

from config import CONFIG
from utils.http import get_json, ApiUnreachable, describe_fetch_failure

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

    # Holder concentration (weight 20)
    if sig.top10_holder_pct is not None:
        w = 20
        earned = w * max(0.0, 1.0 - sig.top10_holder_pct / 0.6)  # 0 pts if top10 >= 60%
        add(w, earned, f"top10 holds {sig.top10_holder_pct*100:.1f}%")
    else:
        add(20, 10, "top10 concentration unknown -- scored neutral")

    # LP lock / bonding curve health (weight 20)
    if sig.lp_locked_or_curve_healthy is not None:
        w = 20
        add(w, w if sig.lp_locked_or_curve_healthy else 0,
            "LP locked / curve healthy" if sig.lp_locked_or_curve_healthy else "LP unlocked / curve unhealthy")
    else:
        add(20, 8, "LP/curve status unknown -- scored low-neutral")

    # Mint/freeze/update authority (weight 20)
    auth_bits = [b for b in (sig.mint_authority_revoked, sig.freeze_authority_revoked) if b is not None]
    if auth_bits:
        w = 20
        earned = w * (sum(1 for b in auth_bits if b) / len(auth_bits))
        add(w, earned, f"authority revoked: {sum(1 for b in auth_bits if b)}/{len(auth_bits)}")
    else:
        add(20, 6, "mint/freeze authority unknown -- scored low-neutral")

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

    # Holder growth rate (weight 10)
    if sig.holder_growth_rate_per_hr is not None:
        w = 10
        earned = w * min(1.0, sig.holder_growth_rate_per_hr / 30.0)
        add(w, earned, f"+{sig.holder_growth_rate_per_hr:.0f} holders/hr")
    else:
        add(10, 4, "holder growth unknown -- scored low-neutral")

    # Bundler/sniper % at launch (weight 15)
    if sig.bundler_sniper_pct is not None:
        w = 15
        earned = w * max(0.0, 1.0 - sig.bundler_sniper_pct / 0.5)  # 0 pts if >=50%
        add(w, earned, f"bundler/sniper {sig.bundler_sniper_pct*100:.1f}%")
    else:
        add(15, 6, "bundler/sniper % unknown -- scored low-neutral")

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
                                 is_pregraduation: bool) -> RawSignals:
    """risk_json from GET /tokens/{mint}/risk, holders_json from
    /tokens/{mint}/holders, bundle_json from /tokens/{mint}/bundle."""
    factors = {f["key"]: f for f in risk_json.get("factors", [])} if risk_json else {}
    top10 = None
    if holders_json and "top10_share" in holders_json:
        top10 = holders_json["top10_share"] / 100.0
    return RawSignals(
        top10_holder_pct=top10,
        lp_locked_or_curve_healthy=None if is_pregraduation else _factor_ok(factors, "lp_lock"),
        mint_authority_revoked=_factor_ok(factors, "mint_authority"),
        freeze_authority_revoked=_factor_ok(factors, "freeze_authority"),
        vol_to_liq_ratio=None,  # requires a volume+liquidity time series call, wired in scheduler
        holder_growth_rate_per_hr=None,
        bundler_sniper_pct=(bundle_json.get("held_pct_of_supply", 0) / 100.0) if bundle_json else None,
        liquidity_usd=None,
        is_pregraduation_solana=is_pregraduation,
    )


def _factor_ok(factors: dict, key: str) -> Optional[bool]:
    f = factors.get(key)
    if not f:
        return None
    return f.get("status") == "ok"


GOPLUS_CHAIN_IDS = {"bsc": "56", "base": "8453", "ethereum": "1"}  # GoPlus's numeric chain ids


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
    goplus_chain = GOPLUS_CHAIN_IDS.get(chain)
    if not goplus_chain or not address:
        return {"ok": False, "reason": f"no GoPlus chain mapping for chain={chain!r}"}
    headers = {}
    if CONFIG.goplus_api_key:
        headers["Authorization"] = f"Bearer {CONFIG.goplus_api_key}"
    result = get_json(
        f"{CONFIG.goplus_base_url}/token_security/{goplus_chain}",
        headers=headers,
        params={"contract_addresses": address},
    )
    if not result.get("ok"):
        return {"ok": False, "reason": describe_fetch_failure({"raw": result})}
    body = result.get("json") or {}
    token = (body.get("result") or {}).get(address.lower())
    if not token:
        return {"ok": False, "reason": "address not present in GoPlus result"}
    return {"ok": True, "data": token}


def signals_from_mobula_pulse(pulse_item: dict, chain: str = None) -> RawSignals:
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

    GoPlus fallback added Sept 25 2026: even after the bug #5 field-name fix,
    Mobula's own "security" object was still absent entirely for both real
    coins in tonight's backtest -- a real data gap, not a parsing bug. If
    lp_locked_or_curve_healthy/mint_authority_revoked/freeze_authority_revoked
    are ALL still None after reading Mobula's own data, and a chain+address
    are available, this now tries fetch_goplus_security as a fallback before
    giving up and returning None (== "unknown -- scored low-neutral" in
    score_token)."""
    security = pulse_item.get("security") or {}
    top10 = pulse_item.get("top10Holdings")
    snipers = pulse_item.get("snipersCount", 0) or 0
    bundlers = pulse_item.get("bundlersCount", 0) or 0
    holder_count = pulse_item.get("holdersCount")
    bundler_sniper_pct = None
    if holder_count:
        bundler_sniper_pct = min(1.0, (snipers + bundlers) / holder_count)

    lp_locked = (not security.get("balanceMutable", False)) if "balanceMutable" in security else None
    mint_revoked = security.get("noMintAuthority")
    freeze_revoked = (not security.get("isBlacklisted")) if "isBlacklisted" in security else None

    if lp_locked is None and mint_revoked is None and freeze_revoked is None:
        address = pulse_item.get("address")
        if chain and address:
            gp = fetch_goplus_security(chain, address)
            if gp.get("ok"):
                d = gp["data"]
                lp_holders = d.get("lp_holders") or []
                if lp_holders:
                    locked_pct = sum(
                        float(h.get("percent", 0) or 0) for h in lp_holders if h.get("is_locked")
                    )
                    lp_locked = locked_pct >= 0.5  # majority of LP locked/burned counts as healthy
                is_mintable = d.get("is_mintable")
                if is_mintable is not None:
                    mint_revoked = is_mintable == "0"  # GoPlus returns "0"/"1" strings
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
        holder_growth_rate_per_hr=None,  # needs a snapshot diff, wired in scheduler (holder census over time)
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
    one call per chain per cycle, not one call per token -- see README.)"""
    results = []
    for item in items:
        sig = signals_from_mobula_pulse(item, chain)
        results.append({
            "chain": chain,
            "address": item.get("address"),
            "score": score_token(sig),
            "raw": item,
        })
    return results


def score_solana_mint(mint: str, chain: str, is_pregraduation: bool) -> dict:
    """Single-mint MadeOnSol scoring -- 3 calls (risk/holders/bundle). Costly
    enough per token that scheduler.py only calls this for a bounded subset
    of Layer 1's deployer alerts (elite tier only, capped per cycle), not
    every discovered mint -- see README's call-budget section for why."""
    raw = fetch_madeonsol_token_risk(mint, chain)
    if not raw.get("ok"):
        return {"chain": chain, "address": mint, "error": raw.get("reason", "fetch failed")}
    d = raw["data"]
    sig = signals_from_madeonsol_risk(
        d["risk"].get("json") or {}, d["holders"].get("json") or {}, d["bundle"].get("json") or {},
        is_pregraduation,
    )
    return {"chain": chain, "address": mint, "score": score_token(sig)}


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

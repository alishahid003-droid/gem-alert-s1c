"""Diagnostic: raw per-signal breakdown for every rug/pump_dump labeled coin,
to confirm exactly why the multi-signal score cleared them into A/B band
despite the coin having already crashed. Mirrors score_solana_mint's own
data flow exactly (same d["risk"].get("json") access pattern) so this is
a faithful reproduction of what the live scorer actually saw, not a guess.

Usage: python diag_rug_signals.py
"""
from backtest_categorized import SOLANA_LABELED
from layers.layer0_scoring import (
    fetch_madeonsol_token_risk, fetch_dexscreener_vol_liq,
    fetch_goplus_security, parse_goplus_solana_security,
    signals_from_madeonsol_risk, score_token,
)

TARGET_CATEGORIES = {"rug", "pump_dump"}

def diag_one(name, chain, address, category, is_pregrad):
    print(f"\n{'='*70}\n[{category}] {name} / {chain} / {address}")

    raw = fetch_madeonsol_token_risk(address, chain)
    if not raw.get("ok"):
        print(f"  MadeOnSol risk/holders/bundle fetch FAILED: {raw.get('reason')}")
        return
    d = raw["data"]

    risk_call_ok = d["risk"].get("ok")
    risk_json = d["risk"].get("json") or {}
    factors = risk_json.get("factors", [])
    print(f"  MadeOnSol /risk HTTP call ok={risk_call_ok} -- factors returned: {len(factors)} "
          f"{'(EMPTY -- PRO-gated on BASIC tier)' if risk_call_ok and not factors else ''}")

    vol_to_liq_ratio = None
    liquidity_usd = None
    dex = fetch_dexscreener_vol_liq(chain, address)
    if dex.get("ok"):
        liquidity_usd = dex["liquidity_usd"]
        vol24 = dex["volume_24h"]
        if liquidity_usd:
            vol_to_liq_ratio = vol24 / liquidity_usd
        print(f"  DexScreener: liquidity_usd={liquidity_usd} volume_24h={vol24} "
              f"vol/liq_ratio={round(vol_to_liq_ratio,2) if vol_to_liq_ratio else None}")
    else:
        print(f"  DexScreener FAILED: {dex.get('reason')}")

    goplus_mint_rev = goplus_freeze_rev = goplus_lp_locked = None
    fallback_would_fire_today = (chain == "solana" and not risk_call_ok)
    print(f"  GoPlus fallback fires today only if risk_call_ok=False -- here risk_call_ok={risk_call_ok}, "
          f"so fallback {'FIRES' if fallback_would_fire_today else 'DOES NOT FIRE'} "
          f"(bug candidate: should probably fire when factors is empty too, not just when the call fails)")
    if fallback_would_fire_today:
        gp = fetch_goplus_security("solana", address)
        if gp.get("ok"):
            parsed = parse_goplus_solana_security(gp["data"])
            goplus_mint_rev = parsed["mint_authority_revoked"]
            goplus_freeze_rev = parsed["freeze_authority_revoked"]
            goplus_lp_locked = parsed["lp_locked"]
            print(f"    -> GoPlus returned: mint_rev={goplus_mint_rev} freeze_rev={goplus_freeze_rev} lp_locked={goplus_lp_locked}")

    sig = signals_from_madeonsol_risk(
        risk_json, d["holders"].get("json") or {}, d["bundle"].get("json") or {},
        is_pregrad, vol_to_liq_ratio=vol_to_liq_ratio, liquidity_usd=liquidity_usd,
        goplus_mint_authority_revoked=goplus_mint_rev,
        goplus_freeze_authority_revoked=goplus_freeze_rev,
        goplus_lp_locked=goplus_lp_locked,
    )
    print(f"  RawSignals -> top10={sig.top10_holder_pct} lp_ok={sig.lp_locked_or_curve_healthy} "
          f"mint_rev={sig.mint_authority_revoked} freeze_rev={sig.freeze_authority_revoked} "
          f"vol_liq={round(sig.vol_to_liq_ratio,2) if sig.vol_to_liq_ratio else None} "
          f"holder_growth={sig.holder_growth_rate_per_hr} bundler_sniper={sig.bundler_sniper_pct}")

    result = score_token(sig)
    print(f"  SCORE: band={result.band} score={result.score}/100")
    for r in result.reasons:
        print(f"    - {r}")


def main():
    targets = [t for t in SOLANA_LABELED if t[3] in TARGET_CATEGORIES]
    print(f"Diagnosing {len(targets)} rug/pump_dump labeled coins (real MadeOnSol/DexScreener calls)...")
    for name, chain, address, category, is_pregrad in targets:
        diag_one(name, chain, address, category, is_pregrad)


if __name__ == "__main__":
    main()

"""
Layer 6 -- Exit/rug-in-progress alert + realizable-gain milestones.

Two independent halves, per spec:

  A) Exit-risk (defensive): for any coin in Ali's actual on-chain balance,
     watch Layer 0/0b's own signals for the REVERSE of what made it look
     safe -- sudden LP withdrawal, top-holder dump, mint/freeze authority
     silently re-enabled. Needs two snapshots (previous poll vs current) to
     detect a *change*, not just a bad reading.

  B) Realizable-gain milestones: on any market-cap milestone for a held
     coin, get a REAL swap quote for selling Ali's exact current balance
     and report theoretical (price-implied) value alongside the actual
     quoted (realizable) value, with slippage %, side by side.

Per the spec's own override: once Layer 8 has tagged a coin HIGH-RISK
MOMENTUM, this layer's exit-risk thresholds run TIGHTER (more sensitive) on
that specific coin -- see get_exit_thresholds().
"""
from dataclasses import dataclass
from typing import Optional

from config import CONFIG
from utils.http import get_json
from utils.swap_quotes import get_best_quote


@dataclass
class ExitThresholds:
    liquidity_drop_pct: float       # LP withdrawal trigger: liquidity fell by more than this %
    top_holder_jump_pct: float      # top-holder dump trigger: top10 share rose by more than this (absolute pct points)


NORMAL_THRESHOLDS = ExitThresholds(liquidity_drop_pct=30.0, top_holder_jump_pct=10.0)
TIGHTENED_THRESHOLDS = ExitThresholds(liquidity_drop_pct=15.0, top_holder_jump_pct=5.0)


def get_exit_thresholds(is_high_risk_momentum: bool) -> ExitThresholds:
    return TIGHTENED_THRESHOLDS if is_high_risk_momentum else NORMAL_THRESHOLDS


# Our internal chain keys (config.wallet_addresses()' dict keys, matching
# WALLET_ADDRESSES' chain:address format) don't all match Mobula's own
# blockchain-name vocabulary for the wallet-portfolio endpoint's
# "blockchains" filter param (confirmed live, Sept 24 2026 -- passing a
# name Mobula doesn't recognize returns a hard 400, not a partial result,
# so ALL chains fail together, not just the bad one):
#   - "bsc" -> Mobula calls it "bnb"
#   - "robinhood_chain" -> not a recognized value for this endpoint at all
#     (RHC is too new / not in Mobula's premium-chain list yet). Omitted
#     from the explicit filter below; fetchAllChains=true still scans the
#     wallet across every chain Mobula indexes, RHC included if/when they
#     add support, so this isn't silently dropping RHC coverage -- it's
#     just not naming a chain Mobula would reject outright.
MOBULA_BLOCKCHAIN_NAME = {
    "solana": "solana",
    "bsc": "bnb",
}


def fetch_wallet_portfolio() -> dict:
    """Uses Mobula's unified wallet-portfolio endpoint across every chain
    Ali gave an address for (config.wallet_addresses())."""
    if not CONFIG.mobula_api_key:
        return {"ok": False, "reason": "MOBULA_API_KEY not configured"}
    addrs = CONFIG.wallet_addresses()
    if not addrs:
        return {"ok": False, "reason": "WALLET_ADDRESSES not configured (Part A item 7)"}
    headers = {"Authorization": f"Bearer {CONFIG.mobula_api_key}"}
    mobula_chains = [MOBULA_BLOCKCHAIN_NAME[c] for c in addrs if c in MOBULA_BLOCKCHAIN_NAME]
    params = {
        "wallets": ",".join(addrs.values()),
        "fetchAllChains": "true",
    }
    if mobula_chains:
        params["blockchains"] = ",".join(mobula_chains)
    result = get_json(f"{CONFIG.mobula_base_url}/api/1/wallet/portfolio", headers=headers, params=params)
    return {"ok": result["ok"], "raw": result}


def detect_exit_risk(prev, curr, is_high_risk_momentum: bool = False) -> list:
    """prev/curr are layers.layer0_scoring.RawSignals for the SAME token at
    two different points in time. Returns a list of triggered reasons
    (empty list = no exit risk detected). Fails closed: a signal that's
    missing on either side is skipped, never guessed."""
    thresholds = get_exit_thresholds(is_high_risk_momentum)
    reasons = []

    if prev.liquidity_usd is not None and curr.liquidity_usd is not None and prev.liquidity_usd > 0:
        drop_pct = 100.0 * (prev.liquidity_usd - curr.liquidity_usd) / prev.liquidity_usd
        if drop_pct >= thresholds.liquidity_drop_pct:
            reasons.append(f"LP withdrawal: liquidity dropped {drop_pct:.1f}% "
                            f"(${prev.liquidity_usd:,.0f} -> ${curr.liquidity_usd:,.0f})")

    if prev.top10_holder_pct is not None and curr.top10_holder_pct is not None:
        jump_pts = (curr.top10_holder_pct - prev.top10_holder_pct) * 100
        if jump_pts >= thresholds.top_holder_jump_pct:
            reasons.append(f"Top-holder dump risk: top10 share rose {jump_pts:.1f} pts "
                            f"({prev.top10_holder_pct*100:.1f}% -> {curr.top10_holder_pct*100:.1f}%)")

    if prev.mint_authority_revoked is True and curr.mint_authority_revoked is False:
        reasons.append("Mint authority silently RE-ENABLED (was revoked)")
    if prev.freeze_authority_revoked is True and curr.freeze_authority_revoked is False:
        reasons.append("Freeze authority silently RE-ENABLED (was revoked)")

    return reasons


def compute_realizable_gain(chain: str, chain_id_mobula: str, token_address: str,
                             balance_tokens: float, token_price_usd: Optional[float],
                             wallet_address: str, native_token_address: str) -> dict:
    """Returns {theoretical_usd, realizable_usd, slippage_pct, quote_source}
    or {ok: False, reason} if no quote could be obtained -- fails closed,
    never fabricates a number."""
    if token_price_usd is None:
        return {"ok": False, "reason": "no current token price available for theoretical value"}
    theoretical_usd = token_price_usd * balance_tokens

    quote = get_best_quote(
        chain=chain, chain_id_mobula=chain_id_mobula,
        token_in=token_address, token_out=native_token_address,
        amount=str(balance_tokens), wallet_address=wallet_address,
    )
    if not quote.get("ok"):
        return {"ok": False, "reason": "no live swap quote available (all sources failed/blocked)",
                "theoretical_usd": theoretical_usd}

    realizable_usd = quote.get("amount_out_usd")
    if realizable_usd is None:
        return {"ok": False, "reason": "quote source returned no USD value", "theoretical_usd": theoretical_usd}

    slippage_pct = 100.0 * (theoretical_usd - realizable_usd) / theoretical_usd if theoretical_usd else None
    return {
        "ok": True,
        "theoretical_usd": theoretical_usd,
        "realizable_usd": realizable_usd,
        "slippage_pct": slippage_pct,
        "quote_source": quote.get("source"),
    }

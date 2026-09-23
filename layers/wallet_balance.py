"""
Batched wallet-balance snapshots for Layer 9's "% of position sold" fix.

Layer 6 already reads Ali's OWN wallet balance via Mobula's unified
wallet-portfolio endpoint (GET /api/1/wallet/portfolio). That same endpoint
works for any address, Ali's or not -- this module reuses it for tracked
Tier 1-4 / watchlist wallets (roster.SELL_WATCH_ROSTER) so Layer 9 can know
what a wallet held *before* a sell, not just what it just sold.

Design (per Ali's explicit direction, "not retroactive" -- accepted):
  - First time a tracked wallet is observed BUYING a token (via the shared
    KOL feed, layers.kol_feed), snapshot + store its balance in that token.
  - Refresh that snapshot on every SUBSEQUENT buy sighting too, not just the
    first -- otherwise the stored balance goes stale the moment the wallet
    accumulates more before eventually selling, and % sold would be wrong
    (too high) against an outdated, smaller "before" number.
  - On a SELL event, use whatever was last stored to compute % sold (see
    layers.layer9_sell_mirror.pct_of_position), then update the stored
    balance ARITHMETICALLY (prior - amount_sold) -- no extra Mobula call at
    sell time. See layers.layer9_sell_mirror.update_balance_after_sell.

Batching (the actual answer to "is there a rate-limit blocker"): every
wallet needing a balance refresh THIS CYCLE goes into ONE Mobula call --
the `wallets` param takes a comma-separated list, and the response covers
every address in one shot. So the marginal cost of this whole feature is
ONE extra Mobula call per chain per poll cycle, not one call per wallet and
not one call per sell event. See README's call-budget section for the
numbers.
"""
from typing import Optional

from config import CONFIG
from utils.http import get_json


def fetch_wallets_portfolio(addresses: list, blockchain: str) -> dict:
    """One batched Mobula wallet-portfolio call covering every address in
    `addresses`. blockchain is whatever slug Mobula expects for this chain
    (same slugs Layer 6 already uses via config.wallet_addresses() keys,
    e.g. "solana", "base", "ethereum") -- passed with fetchAllChains=true as
    a safety net in case an address's exact chain slug is wrong or the
    Robinhood Chain slug isn't what we think (unconfirmed, flagged in
    README), so the lookup still has a chance to succeed."""
    addresses = [a for a in dict.fromkeys(addresses) if a]  # de-dupe, preserve order, drop falsy
    if not CONFIG.mobula_api_key or not addresses:
        return {"ok": False, "reason": "MOBULA_API_KEY not configured or no addresses to check"}
    headers = {"Authorization": f"Bearer {CONFIG.mobula_api_key}"}
    params = {
        "wallets": ",".join(addresses),
        "blockchains": blockchain,
        "fetchAllChains": "true",
    }
    result = get_json(f"{CONFIG.mobula_base_url}/api/1/wallet/portfolio", headers=headers, params=params)
    return {"ok": result["ok"], "raw": result}


def _assets_by_wallet(payload: dict, addresses: list) -> dict:
    """Normalizes a batched portfolio response into {wallet_address: [assets]},
    regardless of which of the two plausible shapes Mobula actually returns
    for a multi-wallet query (unconfirmed in public docs -- only single-wallet
    examples are shown there). Fails closed: a wallet this can't confidently
    match gets no entry, never a guessed one."""
    if not payload:
        return {}
    data = payload.get("data")
    out = {}

    if isinstance(data, dict) and any(k in data for k in addresses):
        # Shape A: {"<wallet>": {"assets": [...]}, ...}
        for addr in addresses:
            wallet_data = data.get(addr)
            if isinstance(wallet_data, dict):
                out[addr] = wallet_data.get("assets", [])
    elif isinstance(data, list):
        if len(addresses) == 1 and data and "assets" not in data[0]:
            # Shape B (single wallet, same flat-assets shape Layer 6 uses).
            out[addresses[0]] = data
        else:
            # Shape C: [{"wallet": "<addr>", "assets": [...]}, ...]
            for entry in data:
                addr = entry.get("wallet") or entry.get("wallet_address") or entry.get("address")
                if addr in addresses:
                    out[addr] = entry.get("assets", [])
    return out


def extract_balances_for_pairs(payload: dict, pairs: list) -> dict:
    """pairs: [(wallet_address, token_address), ...]. Returns
    {(wallet, token): balance} for every pair it could confidently extract
    from ONE batched portfolio response -- a pair missing from the result
    means "not computable from this response", never a guessed 0 or None
    silently treated as zero."""
    addresses = list({w for w, _ in pairs})
    by_wallet = _assets_by_wallet(payload, addresses)
    out = {}
    for wallet, token in pairs:
        assets = by_wallet.get(wallet)
        if assets is None:
            continue
        bal = _extract_token_balance(assets, token)
        if bal is not None:
            out[(wallet, token)] = bal
    return out


def _extract_token_balance(assets: list, token_address: str) -> Optional[float]:
    for a in assets or []:
        asset = a.get("asset", {}) or {}
        addr = asset.get("contracts_balances") or asset.get("address") or asset.get("symbol")
        if addr and str(addr).lower() == str(token_address).lower():
            bal = a.get("token_balance") or a.get("balance")
            if bal is not None:
                try:
                    return float(bal)
                except (TypeError, ValueError):
                    return None
    return None

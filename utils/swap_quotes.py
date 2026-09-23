"""
Real swap-quote sources for Layer 6's realizable-gain calc.

Primary: Mobula's unified Swap Quoting API (GET /api/2/swap/quoting) --
confirmed via docs.mobula.io to cover EVM chains (Base, BSC, Ethereum, AND
Robinhood Chain as evm:4663, since RHC is EVM-compatible and Uniswap v4 is
live there), plus Solana (solana:solana) and TON (ton:mainnet) through the
same endpoint and auth Ali already has a key for. This is a simplification
over the original per-chain plan (Jupiter / Codex-Defined.fi / 1inch) --
one endpoint instead of three -- confirmed against real docs, not assumed.

Fallbacks, kept in per the spec's own risk-mitigation intent:
  - Jupiter (Solana-specific, in case Mobula's Solana leg misbehaves)
  - 1inch Swap API (EVM-specific, chain id 4663 = Robinhood Chain confirmed
    supported in 1inch's own chain table -- also covers Base/BSC/ETH)

Both fallbacks need their own API keys/behavior confirmed live -- Jupiter's
public quote host was confirmed BLOCKED from this sandbox (see Stage 1
README) so its exact auth requirement is unverified; 1inch needs its own key
(not in Part A -- flagged, not yet requested from Ali since Mobula alone may
be sufficient).
"""
from config import CONFIG
from utils.http import get_json


def mobula_swap_quote(chain_id: str, token_in: str, token_out: str,
                       amount: str, wallet_address: str, slippage: str = "auto") -> dict:
    if not CONFIG.mobula_api_key:
        return {"ok": False, "reason": "MOBULA_API_KEY not configured"}
    headers = {"Authorization": f"Bearer {CONFIG.mobula_api_key}"}
    params = {
        "chainId": chain_id,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amount": amount,
        "walletAddress": wallet_address,
        "slippage": slippage,
    }
    result = get_json(f"{CONFIG.mobula_base_url}/api/2/swap/quoting", headers=headers, params=params)
    return {"ok": result["ok"], "raw": result}


def parse_mobula_quote(payload: dict) -> dict:
    data = (payload or {}).get("data", {})
    return {
        "amount_out_tokens": data.get("amountOutTokens"),
        "amount_out_usd": data.get("amountOutUSD"),
        "market_impact_pct": data.get("marketImpactPercentage"),
        "slippage_pct": data.get("slippagePercentage"),
        "route_aggregator": (data.get("details", {}) or {}).get("route", {}).get("aggregator"),
    }


def jupiter_quote(input_mint: str, output_mint: str, amount_lamports: str, slippage_bps: int = 50) -> dict:
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount_lamports,
        "slippageBps": slippage_bps,
    }
    result = get_json(f"{CONFIG.jupiter_quote_base_url}/quote", params=params)
    return {"ok": result["ok"], "raw": result}


ONEINCH_CHAIN_IDS = {"bsc": 56, "robinhood_chain": 4663}  # scope cut Sept 22 -- base/ethereum dropped


def oneinch_quote(chain: str, src_token: str, dst_token: str, amount: str) -> dict:
    if not CONFIG.birdeye_api_key:  # placeholder guard removed below; 1inch needs its own key
        pass
    oneinch_key = None  # not in Part A -- see README gap note
    if not oneinch_key:
        return {"ok": False, "reason": "1inch API key not configured (fallback only, not requested from Ali yet)"}
    chain_id = ONEINCH_CHAIN_IDS.get(chain)
    if not chain_id:
        return {"ok": False, "reason": f"no 1inch chain id mapping for {chain}"}
    headers = {"Authorization": f"Bearer {oneinch_key}"}
    params = {"src": src_token, "dst": dst_token, "amount": amount}
    result = get_json(f"https://api.1inch.dev/swap/v6.0/{chain_id}/quote", headers=headers, params=params)
    return {"ok": result["ok"], "raw": result}


def get_best_quote(chain: str, chain_id_mobula: str, token_in: str, token_out: str,
                    amount: str, wallet_address: str) -> dict:
    """Tries Mobula first (works for all chains per docs), falls back to the
    chain-specific alternative Ali specified if Mobula fails."""
    mobula = mobula_swap_quote(chain_id_mobula, token_in, token_out, amount, wallet_address)
    if mobula.get("ok"):
        parsed = parse_mobula_quote(mobula["raw"].get("json") or {})
        if parsed.get("amount_out_usd") is not None:
            return {"ok": True, "source": "mobula", **parsed}

    if chain == "solana":
        fb = jupiter_quote(token_in, token_out, amount)
        return {"ok": fb.get("ok", False), "source": "jupiter_fallback", "raw": fb}

    fb = oneinch_quote(chain, token_in, token_out, amount)
    return {"ok": fb.get("ok", False), "source": "1inch_fallback", "raw": fb}

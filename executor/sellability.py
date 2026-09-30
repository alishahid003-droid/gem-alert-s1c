"""
Pre-buy sell-ability check -- GO_LIVE_CHECKLIST 3.2 (Sept 30 2026).

The worst memecoin loss isn't a -55% stop, it's a coin that can be bought
but never sold (honeypot, blocked transfers, a 90% sell tax). Every real
buy path calls check_sellable() first and refuses on any hard red flag.

  Solana: the SELL leg is quoted on Jupiter for the tokens the buy would
          return. No route back, or a round trip losing more than
          MAX_ROUND_TRIP_LOSS, refuses. GoPlus Solana's Token-2022 flags
          (transfer hook, non-transferable, balance-mutable authority,
          transfer fee above MAX_TAX) also refuse.
  BSC:    GoPlus token security -- honeypot, cannot_sell_all, buy or sell
          tax above MAX_TAX -- refuses.
  Other chains: no independent check exists (Robinhood Chain has no
          scanner); allowed, since the real-data buy gate already keeps
          unscanned coins away from money.

Unknown/unreachable data never refuses on its own (a GoPlus outage must not
freeze trading), but a confirmed red flag always does.
"""
import os
from typing import Optional, Tuple

WSOL = "So11111111111111111111111111111111111111112"


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


def max_round_trip_loss() -> float:
    return _f("SELLABILITY_MAX_ROUND_TRIP_LOSS", 0.25)


def max_tax() -> float:
    return _f("SELLABILITY_MAX_TAX", 0.10)


def _flag(v) -> bool:
    """GoPlus flags come as "1"/"0" strings or {"status": "1"} objects."""
    if isinstance(v, dict):
        v = v.get("status")
    return str(v).strip() == "1"


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def solana_goplus_red_flags(data: dict) -> list:
    flags = []
    if not isinstance(data, dict):
        return flags
    for key, label in (("transfer_hook", "Token-2022 transfer hook"),
                       ("non_transferable", "non-transferable token"),
                       ("balance_mutable_authority", "balance can be changed by an authority")):
        v = data.get(key)
        if isinstance(v, list):
            # transfer_hook: a non-empty list IS the red flag (hook programs
            # exist); other list-shaped fields carry per-entry status flags
            if (key == "transfer_hook" and len(v) > 0) or any(_flag(x) for x in v):
                flags.append(label)
        elif _flag(v):
            flags.append(label)
    fee = data.get("transfer_fee")
    if isinstance(fee, dict):
        rate = _num(fee.get("fee_rate")) or _num(fee.get("rate"))
        if rate is not None:
            rate = rate / 100.0 if rate > 1 else rate
            if rate > max_tax():
                flags.append(f"transfer fee {rate * 100:.0f}%")
    return flags


def evm_goplus_red_flags(data: dict) -> list:
    flags = []
    if not isinstance(data, dict):
        return flags
    if _flag(data.get("is_honeypot")):
        flags.append("honeypot (GoPlus)")
    if _flag(data.get("cannot_sell_all")):
        flags.append("cannot sell all tokens")
    for key in ("sell_tax", "buy_tax"):
        t = _num(data.get(key))
        if t is not None and t > max_tax():
            flags.append(f"{key.replace('_', ' ')} {t * 100:.0f}%")
    return flags


def check_solana_round_trip(token_mint: str, lamports_in: int, buy_quote: Optional[dict],
                            quote_fn=None) -> Tuple[bool, str]:
    """Quotes selling the buy's expected output back to SOL."""
    from utils.http import get_json
    from config import CONFIG
    out_amount = (buy_quote or {}).get("outAmount")
    try:
        out_amount = int(out_amount)
    except (TypeError, ValueError):
        return True, "no buy-quote output to test the sell leg with (not blocking)"
    if out_amount <= 0:
        return False, "buy quote returns 0 tokens"
    quote_fn = quote_fn or (lambda params: get_json(f"{CONFIG.jupiter_quote_base_url}/quote", params=params))
    q = quote_fn({"inputMint": token_mint, "outputMint": WSOL, "amount": out_amount, "slippageBps": 300})
    if not q.get("ok"):
        status = q.get("status_code")
        if status in (400, 404):
            return False, f"no sell route back to SOL on Jupiter (HTTP {status}) -- likely unsellable"
        return True, f"sell-leg quote unavailable (HTTP {status}), not blocking"
    back = _num((q.get("json") or {}).get("outAmount"))
    if not back:
        return False, "sell leg quotes 0 SOL back -- unsellable"
    loss = 1.0 - back / float(lamports_in)
    if loss > max_round_trip_loss():
        return False, f"round trip would lose {loss * 100:.0f}% (tax/illiquid; cap {max_round_trip_loss() * 100:.0f}%)"
    return True, f"sell route OK, round-trip cost {loss * 100:.1f}%"


def check_sellable(chain: str, token: str, lamports_in: Optional[int] = None,
                   buy_quote: Optional[dict] = None, goplus_fn=None, quote_fn=None) -> Tuple[bool, str]:
    """(ok, reason). Called by every real buy before anything is signed."""
    if goplus_fn is None:
        from layers.layer0_scoring import fetch_goplus_security as goplus_fn  # noqa: N811
    if chain in ("solana", "bsc"):
        try:
            gp = goplus_fn(chain, token) or {}
        except Exception:
            gp = {}
        data = gp.get("data") if gp.get("ok") else None
        flags = (solana_goplus_red_flags(data) if chain == "solana" else evm_goplus_red_flags(data)) if data else []
        if flags:
            return False, "unsellable/unsafe: " + "; ".join(flags)
    if chain == "solana" and lamports_in:
        return check_solana_round_trip(token, lamports_in, buy_quote, quote_fn=quote_fn)
    return True, "no red flags"

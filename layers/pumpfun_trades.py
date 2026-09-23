"""
Raw pump.fun trade feed -- decodes real buy/sell trades directly off
pump.fun's own on-chain program, via the Solana RPC pool this project
already has (executor/rpc_pool.py, free, no-signup, confirmed healthy).

WHY THIS EXISTS (Ali, Sept 23 2026): asked for a convergence layer built on
real top pump.fun traders, not just his manually-tracked Fomo roster. No
free, ready-made "top pump.fun trader" API exists (see
layers/layer2b_pumpfun_smart_money.py's docstring for that research) -- so
this module is the free alternative: watch pump.fun's program directly and
decode trades ourselves, real transactions, no third party's opinion.

SOURCES, NOT GUESSED:
  - Program ID (6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P), confirmed
    against pump.fun's own official docs repo
    (github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_
    README.md), Sept 23 2026.
  - Buy/sell instruction discriminators and account ordering, confirmed
    against Solana Tracker's pump.fun program guide
    (docs.solanatracker.io/guides/pumpfun-program), Sept 23 2026.

REAL, ACKNOWLEDGED GAP -- NOT HIDDEN: this decodes the CLASSIC `buy`/`sell`
instructions only. Solana Tracker's own guide states mainnet increasingly
uses newer variants (`buy_v2`, `buy_exact_sol_in`, `buy_exact_quote_in_v2`,
and presumably sell equivalents) -- confirmed to exist in pump.fun's IDL
instruction list (via a separate IDL mirror), but their exact discriminator
bytes were NOT independently confirmed from a primary source, so they are
deliberately NOT included here. Guessing a discriminator wrong would
silently misclassify trades instead of failing loudly -- worse than the
honest gap. Net effect: this module WILL under-count real pump.fun trades
until those variants are added. The first real live run (this sandbox
can't reach Solana RPC hosts at all, same standing limit as every other
network-dependent module here) is what tells us how big that gap actually
is -- add the missing discriminators once real traffic shows how often
they're hit.

Also flagged, same source: "live mainnet instructions often append
remaining accounts after the IDL list" -- the account-index assumptions
below (TRADER_ACCOUNT_INDEX / MINT_ACCOUNT_INDEX) are a first-pass reading
of the documented account order, not a guarantee, same honesty standard as
every other "not yet live-tested" assumption in this repo.
"""
from typing import Optional

import base58

from executor.rpc_pool import rpc_call

PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Classic buy/sell instruction discriminators (first 8 bytes of instruction
# data) -- see module docstring for source and the known-variants gap.
BUY_DISCRIMINATOR = bytes([102, 6, 61, 18, 1, 218, 235, 234])
SELL_DISCRIMINATOR = bytes([51, 230, 133, 164, 1, 127, 131, 173])

# Account order for the classic buy/sell instruction -- see docstring's
# "live mainnet often appends accounts" caveat.
TRADER_ACCOUNT_INDEX = 6
MINT_ACCOUNT_INDEX = 2

SIGNATURES_PER_FETCH = 30


def fetch_recent_signatures(before: Optional[str] = None, limit: int = SIGNATURES_PER_FETCH) -> dict:
    """One call: getSignaturesForAddress against pump.fun's program itself
    -- covers every trade on every token, not one call per token. `before`
    pages backwards from a prior cursor (see scheduler's dedup handling)."""
    params_obj = {"limit": limit}
    if before:
        params_obj["before"] = before
    result = rpc_call("solana", "getSignaturesForAddress", [PUMPFUN_PROGRAM_ID, params_obj])
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason")}
    return {"ok": True, "signatures": [s["signature"] for s in (result.get("result") or [])]}


def fetch_transaction(signature: str) -> dict:
    params_obj = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    result = rpc_call("solana", "getTransaction", [signature, params_obj])
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason")}
    return {"ok": True, "tx": result.get("result")}


def _account_index_in_keys(account_keys: list, pubkey: str) -> Optional[int]:
    for i, k in enumerate(account_keys or []):
        key = k.get("pubkey") if isinstance(k, dict) else k
        if key == pubkey:
            return i
    return None


def decode_trade(tx_result: dict) -> Optional[dict]:
    """tx_result: the "result" object of a getTransaction (jsonParsed) call.
    Returns {"wallet", "mint", "direction", "sol_delta", "block_time"} for
    the first recognized pump.fun buy/sell instruction found, or None if
    this transaction doesn't contain one this module recognizes (an
    unrelated instruction, an unrecognized variant -- see docstring gap --
    or malformed data). Fails closed on every parse problem rather than
    raising, since one bad transaction must never stop the batch."""
    if not tx_result:
        return None
    try:
        message = tx_result["transaction"]["message"]
        account_keys = message.get("accountKeys", [])
        instructions = message.get("instructions", [])
        meta = tx_result.get("meta") or {}
        pre_balances = meta.get("preBalances") or []
        post_balances = meta.get("postBalances") or []
        block_time = tx_result.get("blockTime")
    except (KeyError, TypeError):
        return None

    for ix in instructions:
        if ix.get("programId") != PUMPFUN_PROGRAM_ID:
            continue
        data_b58 = ix.get("data")
        ix_accounts = ix.get("accounts") or []
        if not data_b58 or len(ix_accounts) <= max(TRADER_ACCOUNT_INDEX, MINT_ACCOUNT_INDEX):
            continue
        try:
            raw = base58.b58decode(data_b58)
        except Exception:
            continue
        if len(raw) < 8:
            continue
        discriminator = raw[:8]
        if discriminator == BUY_DISCRIMINATOR:
            direction = "buy"
        elif discriminator == SELL_DISCRIMINATOR:
            direction = "sell"
        else:
            continue  # unrecognized instruction variant -- see docstring gap

        wallet = ix_accounts[TRADER_ACCOUNT_INDEX]
        mint = ix_accounts[MINT_ACCOUNT_INDEX]

        sol_delta = None
        idx = _account_index_in_keys(account_keys, wallet)
        if idx is not None and idx < len(pre_balances) and idx < len(post_balances):
            sol_delta = (post_balances[idx] - pre_balances[idx]) / 1_000_000_000  # lamports -> SOL

        return {"wallet": wallet, "mint": mint, "direction": direction,
                "sol_delta": sol_delta, "block_time": block_time}
    return None

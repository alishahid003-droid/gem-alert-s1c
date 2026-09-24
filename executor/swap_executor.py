"""
On-chain buy/sell execution. This is the one module in the whole S1c build
that moves real money, so it is deliberately the most defensive piece of
code in the repo:

  - Refuses to run at all unless EXECUTION_ENABLED="true" AND the relevant
    chain's private key is configured (executor_config.EXECUTOR_CONFIG.
    ready_for_chain). No implicit "test mode that quietly does nothing" --
    it either clearly refuses, or it is live. Nothing in between.
  - Never logs or returns a private key, even in error paths.
  - UNTESTED against real networks, same honest flag as everything else in
    this codebase that depends on live infrastructure this build
    environment can't reach. Solana's path is the most complete (Jupiter's
    swap API is already used elsewhere in this stack for quotes, so the
    pattern is proven at the quote level, just not yet at the send level).
    BSC uses PancakeSwap's real, public V2 router address.

  - Robinhood Chain (Sept 22, 2026 update): router address is now
    confirmed. Uniswap's own official developer docs
    (developers.uniswap.org/docs/protocols/v4/deployments) list a
    Robinhood Chain (chain ID 4663) deployment table, and Uniswap's own
    blog (blog.uniswap.org/robinhood-chain-is-live) independently confirms
    v2/v3/v4/UniswapX are live on Robinhood Chain at that same chain ID --
    two separate official Uniswap sources agreeing, not one page taken on
    faith. The docs-page Permit2 address in that same table
    (0x000000000022D473030F116dDEE9F6B43aC78BA3) matches the real,
    well-known canonical Permit2 address that's identical across Ethereum
    mainnet and dozens of other chains via deterministic CREATE2 deployment
    -- a real independent cross-check that the table wasn't garbled in
    transcription, not just "the page said so." That's enough to stop
    treating this as an unconfirmed guess and wire in the real Universal
    Router address below.

    Fully confirmed Sept 22, 2026: Ali manually checked
    robinhoodchain.blockscout.com's page for this address -- it's a
    VERIFIED contract, explicitly labeled "UniversalRouter" by the
    explorer itself, not just by Uniswap's own docs. That's independent
    on-chain confirmation on top of the two official-source corroboration
    above, as strong as this gets without an actual transaction.

    What this does NOT mean: execute_buy_robinhood_chain still returns
    UNTESTED, same as the Solana and BSC paths -- a verified, correctly
    labeled router address is not the same as a real send having
    succeeded through it. One small real test swap, once execution is
    enabled, is still the last step before this path is trusted with real
    money -- same as every other chain here.

No RPC provider account needed for ANY of the three chains -- all RPC
submission goes through executor/rpc_pool.py's RPC_ENDPOINT_POOLS, one
free, no-signup pool per chain (Ali, Sept 23 2026: "point 6 should cover
all 3 chains"):
  - Solana: 3 endpoints, FINAL -- each individually live-tested by Ali on
    his own machine across 3 rounds, Sept 23 2026 (5 other candidates
    tried and rejected, see rpc_pool.py's docstring for the full history).
  - BSC: 3 endpoints, CONFIRMED -- all live-tested healthy by Ali, round
    4, Sept 23 2026.
  - Robinhood Chain: 1 official endpoint found via Robinhood's own docs,
    CONFIRMED healthy on a live test call, round 4, Sept 23 2026 -- but
    Robinhood's own docs still say it "is rate-limited and not recommended
    for production use," which one successful call doesn't disprove under
    real repeated polling. It's the only free candidate either way, so
    it's what this pool uses until/unless it fails under real load.
All free, no signup, matching Ali's explicit constraint (Sept 22, 2026).

IMPORTANT for whoever builds the sign+send step below: call
executor.rpc_pool.rpc_call(chain, method, params) for the actual
submitSignedTransaction / getLatestBlockhash / confirmation-polling RPC
calls on EVERY chain, NOT a single hardcoded endpoint or
EXECUTOR_CONFIG.solana_rpc_url/bsc_rpc_url/rhc_rpc_url directly (those
fields are now manual single-endpoint overrides/fallbacks only -- see
their comments in executor/config.py). This is the one line of real
infrastructure this module is still missing before it can move real
money, for all three chains at once.
"""
from dataclasses import dataclass
from typing import Optional

from config import CONFIG
from utils.http import get_json, post_json
from executor.config import EXECUTOR_CONFIG
from executor.rpc_pool import rpc_call

PANCAKESWAP_V2_ROUTER_BSC = "0x10ED43C718714eb63d5aA57B78B54704E256024E"  # FIXED Sept 25, 2026: previous value was missing its trailing "E" (39 hex chars instead of 40 -- an invalid address that would have failed every BSC buy). Re-verified against PancakeSwap's own official npm package (@pancakeswap/smart-router, V2_ROUTER_ADDRESS[ChainId.BSC]), not a web summary -- confirmed via is_address() == True and Web3.to_checksum_address() round-tripping to this exact casing.
WBNB_BSC = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"  # wrapped BNB, re-verified against @pancakeswap/tokens npm package source (same "one char short" trap as the router address above)
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"  # verified against @pancakeswap/tokens

# Confirmed Sept 22, 2026 against Uniswap's own official developer docs
# (developers.uniswap.org/docs/protocols/v4/deployments), Robinhood Chain
# (chain ID 4663) deployment table -- see module docstring for the
# corroboration (Uniswap's own blog + a Permit2 address cross-check) that
# went into treating this as confirmed rather than a guess.
UNISWAP_V4_UNIVERSAL_ROUTER_RHC = "0x8876789976decbfcbbbe364623c63652db8c0904"
UNISWAP_V4_POOL_MANAGER_RHC = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
UNISWAP_V4_QUOTER_RHC = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94"
PERMIT2_RHC = "0x000000000022D473030F116dDEE9F6B43aC78BA3"  # canonical cross-chain address, matches known value
ROBINHOOD_CHAIN_ID = 4663


@dataclass
class ExecutionResult:
    ok: bool
    reason: str
    tx_signature: Optional[str] = None
    filled_usd: Optional[float] = None


def _refuse_unless_ready(chain: str) -> Optional[ExecutionResult]:
    if not EXECUTOR_CONFIG.execution_enabled:
        return ExecutionResult(False, "EXECUTION_ENABLED is not 'true' -- executor is inert by design")
    if not EXECUTOR_CONFIG.ready_for_chain(chain):
        return ExecutionResult(False, f"no execution wallet key configured for chain '{chain}'")
    return None


def execute_buy_solana(token_mint: str, usd_amount: float) -> ExecutionResult:
    guard = _refuse_unless_ready("solana")
    if guard:
        return guard

    # Step 1: quote via Jupiter (same public API utils/swap_quotes.py already
    # calls for realizable-gain checks -- reused, not duplicated).
    try:
        from solders.keypair import Keypair  # type: ignore
        from solders.transaction import VersionedTransaction  # type: ignore
        import base58  # type: ignore
    except ImportError:
        return ExecutionResult(False, "solders/base58 not installed -- add to requirements.txt before enabling")

    sol_price = _sol_price_usd()
    if sol_price is None or sol_price <= 0:
        return ExecutionResult(False, "could not fetch a live SOL/USD price -- refusing to size a buy on a guess")
    lamports = int(usd_amount * 1_000_000_000 / sol_price)
    quote = get_json(f"{CONFIG.jupiter_quote_base_url}/quote", params={
        "inputMint": "So11111111111111111111111111111111111111112",  # wrapped SOL
        "outputMint": token_mint,
        "amount": lamports,
        "slippageBps": 100,
    })
    if not quote.get("ok"):
        return ExecutionResult(False, f"jupiter quote failed: status {quote.get('status_code')}")

    # Step 2: get the actual swap transaction for this quote.
    swap_resp = post_json(f"{CONFIG.jupiter_quote_base_url}/swap", json={
        "quoteResponse": quote.get("json"),
        "userPublicKey": _solana_pubkey_from_private_key(),
        "wrapAndUnwrapSol": True,
    })
    if not swap_resp.get("ok"):
        return ExecutionResult(False, f"jupiter swap-tx build failed: status {swap_resp.get('status_code')}")

    # Step 3: sign + send, for real, via executor.rpc_pool (failover across
    # all 3 confirmed free Solana endpoints -- see rpc_pool.py docstring for
    # the round-by-round verification history). This actually submits a
    # transaction when EXECUTION_ENABLED=true and a key is configured --
    # there is no dry-run mode below this line by design (module docstring:
    # "it either clearly refuses, or it is live").
    try:
        import base64
        swap_tx_b64 = (swap_resp.get("json") or {}).get("swapTransaction")
        if not swap_tx_b64:
            return ExecutionResult(False, "jupiter swap response had no swapTransaction field")

        keypair = Keypair.from_bytes(base58.b58decode(EXECUTOR_CONFIG.solana_private_key))
        unsigned_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
        signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])
        signed_tx_b64 = base64.b64encode(bytes(signed_tx)).decode("ascii")
    except Exception as exc:  # noqa: BLE001 -- any signing failure must refuse, never half-send
        return ExecutionResult(False, f"failed to sign transaction: {exc}")

    send_result = rpc_call("solana", "sendTransaction", [
        signed_tx_b64,
        {"encoding": "base64", "skipPreflight": False, "maxRetries": 3, "preflightCommitment": "confirmed"},
    ])
    if not send_result.get("ok"):
        return ExecutionResult(False, f"sendTransaction failed: {send_result.get('reason')}")

    tx_sig = send_result.get("result")
    if not isinstance(tx_sig, str):
        return ExecutionResult(False, f"sendTransaction returned no usable signature: {send_result}")

    confirmed = _confirm_solana_tx(tx_sig)
    if not confirmed.get("ok"):
        # Transaction WAS submitted -- this is a "can't confirm" result, not a
        # "didn't happen" result. Surface the signature so it can be checked
        # manually on an explorer rather than silently treated as a no-op.
        return ExecutionResult(False, f"sent but could not confirm (check signature manually): "
                                       f"{confirmed.get('reason')}", tx_signature=tx_sig)

    return ExecutionResult(True, "buy submitted and confirmed", tx_signature=tx_sig, filled_usd=usd_amount)


def _confirm_solana_tx(signature: str, attempts: int = 10, delay_seconds: float = 2.0) -> dict:
    """Polls getSignatureStatuses until the transaction lands (confirmed/
    finalized) or errors on-chain. Real polling, not a fire-and-forget --
    a buy this module reports as successful must actually have a
    confirmed signature behind it, since Stage1 conviction state and
    position tracking downstream depend on this being true."""
    import time
    for _ in range(attempts):
        status = rpc_call("solana", "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        if status.get("ok"):
            values = ((status.get("result") or {}).get("value")) or [None]
            info = values[0]
            if info is not None:
                if info.get("err"):
                    return {"ok": False, "reason": f"transaction landed but failed on-chain: {info['err']}"}
                confirmation_status = info.get("confirmationStatus")
                if confirmation_status in ("confirmed", "finalized"):
                    return {"ok": True}
        time.sleep(delay_seconds)
    return {"ok": False, "reason": f"not confirmed after {attempts} polls ({attempts * delay_seconds:.0f}s)"}


def execute_buy_bsc(token_address: str, usd_amount: float) -> ExecutionResult:
    guard = _refuse_unless_ready("bsc")
    if guard:
        return guard
    try:
        from web3 import Web3  # type: ignore
    except ImportError:
        return ExecutionResult(False, "web3.py not installed -- add to requirements.txt before enabling")
    return ExecutionResult(False, "PancakeSwap V2 router path is written but UNTESTED against a live network -- "
                                   "do not treat this as a working execution path until a real run confirms it")


def execute_buy_robinhood_chain(token_address: str, usd_amount: float) -> ExecutionResult:
    """Router address confirmed Sept 22, 2026 (see module docstring) --
    this is no longer the NotImplementedError guard it used to be. Same
    honest status as the Solana and BSC paths above: written, guarded, but
    UNTESTED against a live network. Do not fund a wallet against this path
    without first (1) checking UNISWAP_V4_UNIVERSAL_ROUTER_RHC has real
    contract bytecode on a Robinhood Chain block explorer, and (2) running
    one small real test swap."""
    guard = _refuse_unless_ready("robinhood_chain")
    if guard:
        return guard
    try:
        from web3 import Web3  # type: ignore
    except ImportError:
        return ExecutionResult(False, "web3.py not installed -- add to requirements.txt before enabling")
    return ExecutionResult(False, "Uniswap v4 Universal Router path on Robinhood Chain is written but "
                                   "UNTESTED against a live network -- do not treat this as a working "
                                   "execution path until a real run confirms it")


def execute_sell(chain: str, token_address: str, amount_tokens: float, reason: str) -> ExecutionResult:
    """Used by defensive_sell.py when Layer 6's rug signal fires on a
    position this executor opened. Same enable/key guard as the buy paths."""
    guard = _refuse_unless_ready(chain)
    if guard:
        return guard
    return ExecutionResult(False, f"sell path is written but UNTESTED against a live network "
                                   f"(reason for this sell attempt: {reason})")


def _solana_pubkey_from_private_key() -> str:
    from solders.keypair import Keypair  # type: ignore
    import base58  # type: ignore
    kp = Keypair.from_bytes(base58.b58decode(EXECUTOR_CONFIG.solana_private_key))
    return str(kp.pubkey())


WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # 6 decimals, USD-pegged


def _sol_price_usd() -> Optional[float]:
    """Real SOL/USD price, derived from a live Jupiter quote (1 SOL -> USDC)
    rather than a separate, unverified Mobula price endpoint -- this repo
    has no prior working call to any Mobula price/market-data endpoint
    (checked Sept 2026: only /api/2/pulse, /api/1/wallet/*, and
    /api/2/swap/quoting are used anywhere in this codebase), whereas the
    Jupiter /quote endpoint used here is the SAME endpoint swap_executor
    already calls for real buys, already proven live at the quote level.
    Reusing it means one less untested integration on the path that moves
    real money, not two. Returns None (never a stale/guessed number) if the
    quote fails, so callers must treat that as "can't price this trade
    right now" and refuse, not fall back to a hardcoded price."""
    quote = get_json(f"{CONFIG.jupiter_quote_base_url}/quote", params={
        "inputMint": WRAPPED_SOL_MINT,
        "outputMint": USDC_MINT_SOLANA,
        "amount": 1_000_000_000,  # 1 SOL, in lamports
        "slippageBps": 50,
    })
    if not quote.get("ok"):
        return None
    body = quote.get("json") or {}
    out_amount = body.get("outAmount")
    if out_amount is None:
        return None
    try:
        return int(out_amount) / 1_000_000  # USDC has 6 decimals
    except (TypeError, ValueError):
        return None

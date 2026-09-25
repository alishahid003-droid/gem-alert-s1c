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

import state
from config import CONFIG
from utils.http import get_json, post_json
from executor.config import EXECUTOR_CONFIG
from executor.rpc_pool import rpc_call
from executor.rhc_pool_discovery import find_v4_pool, NATIVE_CURRENCY as RHC_NATIVE_CURRENCY
from executor.rhc_v4_swap import build_v4_exact_in_single_calldata
from links import DEXSCREENER_CHAIN_SLUG

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
    filled_amount_tokens: Optional[float] = None  # REAL received quantity, parsed from the
                                                    # confirmed tx -- not the requested/estimated
                                                    # size. This is what moonbag.py's trim ladder
                                                    # needs to compute real sell amounts; without
                                                    # it, check_and_trim() reads amount_tokens=0
                                                    # off the position and every trim is a no-op.


def _log_trade(side: str, chain: str, token: str, result: "ExecutionResult", reason: Optional[str] = None):
    """Feeds the dashboard's Trade History table (Tasks Left #3/#6, Sept 25
    2026) -- called once per real attempted buy/sell right where the
    ExecutionResult is final, success or failure, so the dashboard shows
    what was actually tried, not just what worked. Best-effort: a logging
    failure must never take down a real trade in flight, so this is
    deliberately wrapped and swallows its own errors."""
    try:
        state.log_trade_event(
            side=side, chain=chain, token=token, ok=result.ok, reason=reason or result.reason,
            amount_tokens=result.filled_amount_tokens, usd_amount=result.filled_usd,
            tx_signature=result.tx_signature,
        )
    except Exception:  # noqa: BLE001 -- logging must never break a real trade
        pass


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

    filled_tokens = _get_solana_fill_amount(tx_sig, token_mint, pubkey_from_key=EXECUTOR_CONFIG.solana_private_key)
    result = ExecutionResult(True, "buy submitted and confirmed", tx_signature=tx_sig,
                              filled_usd=usd_amount, filled_amount_tokens=filled_tokens)
    _log_trade("buy", "solana", token_mint, result)
    return result


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

def _get_solana_fill_amount(tx_sig: str, token_mint: str, pubkey_from_key: str) -> Optional[float]:
    """Reads the REAL number of tokens received in a confirmed buy, by
    diffing postTokenBalances against preTokenBalances for our wallet's
    entry for this specific mint -- not the quote's estimated outAmount,
    which is a pre-trade estimate and can differ from the real fill under
    slippage. Returns None (never a guess) if the transaction can't be
    fetched or parsed; the caller then has a real tx_signature to check
    manually rather than a silently wrong number feeding moonbag math."""
    try:
        wallet_pubkey = str(Keypair.from_bytes(base58.b58decode(pubkey_from_key)).pubkey())
    except Exception:  # noqa: BLE001
        return None

    tx_result = rpc_call("solana", "getTransaction", [
        tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
    ])
    if not tx_result.get("ok"):
        return None
    meta = ((tx_result.get("result") or {}).get("meta")) or {}
    pre_balances = meta.get("preTokenBalances") or []
    post_balances = meta.get("postTokenBalances") or []

    def _amount_for_owner(balances):
        for b in balances:
            if b.get("owner") == wallet_pubkey and b.get("mint") == token_mint:
                ui = (b.get("uiTokenAmount") or {}).get("uiAmount")
                if ui is not None:
                    return float(ui)
        return 0.0

    pre_amount = _amount_for_owner(pre_balances)
    post_amount = _amount_for_owner(post_balances)
    delta = post_amount - pre_amount
    return delta if delta > 0 else None


PANCAKE_ROUTER_ABI = [
    {
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
]


def _encode_router_call(router, fn_name: str, args: list) -> str:
    """web3.py renamed Contract.encodeABI -> Contract.encode_abi between
    v6 and v7 (requirements.txt pins web3>=6.15.0 with no upper bound, so
    either method name may be the real one depending on what's installed
    at deploy time -- confirmed the hard way: this repo's own local
    install is 8.0.0, where only encode_abi exists). Try the current name
    first, fall back to the old one, so this doesn't silently break on a
    pinned-older environment."""
    if hasattr(router, "encode_abi"):
        return router.encode_abi(abi_element_identifier=fn_name, args=args)
    return router.encodeABI(fn_name=fn_name, args=args)  # web3 < 7 fallback


def _bnb_price_usd() -> Optional[float]:
    """Live BNB/USD via PancakeSwap's own on-chain getAmountsOut (1 WBNB ->
    USDT), read through the RPC failover pool via eth_call -- same
    "reuse what's already trusted on this path" reasoning as Solana's
    _sol_price_usd: this hits the exact router/pool this module already
    depends on for the real swap, not a separate price API."""
    from web3 import Web3  # type: ignore
    w3 = Web3()
    router = w3.eth.contract(address=Web3.to_checksum_address(PANCAKESWAP_V2_ROUTER_BSC), abi=PANCAKE_ROUTER_ABI)
    calldata = _encode_router_call(router, "getAmountsOut", [
        10**18, [Web3.to_checksum_address(WBNB_BSC), Web3.to_checksum_address(USDT_BSC)],
    ])
    result = rpc_call("bsc", "eth_call", [{"to": PANCAKESWAP_V2_ROUTER_BSC, "data": calldata}, "latest"])
    if not result.get("ok"):
        return None
    raw = result.get("result")
    if not isinstance(raw, str) or not raw.startswith("0x"):
        return None
    try:
        decoded = w3.codec.decode(["uint256[]"], bytes.fromhex(raw[2:]))
        usdt_out_wei = decoded[0][1]  # amounts[1] = USDT received for 1 WBNB in
        return usdt_out_wei / 10**18  # USDT is 18 decimals on BSC (unlike Ethereum's 6)
    except Exception:  # noqa: BLE001
        return None


def execute_buy_bsc(token_address: str, usd_amount: float) -> ExecutionResult:
    guard = _refuse_unless_ready("bsc")
    if guard:
        return guard
    try:
        from web3 import Web3  # type: ignore
        from eth_account import Account  # type: ignore
    except ImportError:
        return ExecutionResult(False, "web3.py not installed -- add to requirements.txt before enabling")

    bnb_price = _bnb_price_usd()
    if bnb_price is None or bnb_price <= 0:
        return ExecutionResult(False, "could not fetch a live BNB/USD price -- refusing to size a buy on a guess")
    bnb_amount_wei = int((usd_amount / bnb_price) * 10**18)

    w3 = Web3()
    router_addr = Web3.to_checksum_address(PANCAKESWAP_V2_ROUTER_BSC)
    account = Account.from_key(EXECUTOR_CONFIG.bsc_private_key)

    try:
        # .lower() first: to_checksum_address strictly validates EIP-55 casing on
        # mixed-case input rather than normalizing it (caught by a local test run
        # here -- a real address from Mobula in arbitrary case would otherwise be
        # wrongly refused as "invalid" even though it's a perfectly real address).
        token_checksum = Web3.to_checksum_address(token_address.lower())
    except ValueError:
        return ExecutionResult(False, f"'{token_address}' is not a valid BSC address -- refusing to build a tx to it")

    router = w3.eth.contract(address=router_addr, abi=PANCAKE_ROUTER_ABI)
    deadline = int(__import__("time").time()) + 300
    calldata = _encode_router_call(
        router, "swapExactETHForTokensSupportingFeeOnTransferTokens",
        [0, [Web3.to_checksum_address(WBNB_BSC), token_checksum], account.address, deadline],
    )

    nonce_result = rpc_call("bsc", "eth_getTransactionCount", [account.address, "pending"])
    if not nonce_result.get("ok"):
        return ExecutionResult(False, f"could not fetch nonce: {nonce_result.get('reason')}")
    gas_price_result = rpc_call("bsc", "eth_gasPrice", [])
    if not gas_price_result.get("ok"):
        return ExecutionResult(False, f"could not fetch gas price: {gas_price_result.get('reason')}")

    tx = {
        "to": router_addr,
        "value": bnb_amount_wei,
        "gas": 400_000,  # conservative fixed limit -- deliberately NOT estimate_gas (that needs a live call
                          # this module doesn't make against an untrusted new token contract pre-buy)
        "gasPrice": int(gas_price_result["result"], 16),
        "nonce": int(nonce_result["result"], 16),
        "chainId": 56,
        "data": calldata,
    }
    signed = account.sign_transaction(tx)
    raw_hex = "0x" + signed.raw_transaction.hex() if not signed.raw_transaction.hex().startswith("0x") else signed.raw_transaction.hex()

    send_result = rpc_call("bsc", "eth_sendRawTransaction", [raw_hex])
    if not send_result.get("ok"):
        return ExecutionResult(False, f"eth_sendRawTransaction failed: {send_result.get('reason')}")
    tx_hash = send_result.get("result")
    if not isinstance(tx_hash, str):
        return ExecutionResult(False, f"eth_sendRawTransaction returned no usable hash: {send_result}")

    confirmed = _confirm_evm_tx("bsc", tx_hash)
    if not confirmed.get("ok"):
        return ExecutionResult(False, f"sent but could not confirm (check hash manually): "
                                       f"{confirmed.get('reason')}", tx_signature=tx_hash)
    filled_tokens = _get_evm_fill_amount("bsc", confirmed.get("receipt") or {}, token_checksum, account.address)
    result = ExecutionResult(True, "buy submitted and confirmed", tx_signature=tx_hash,
                              filled_usd=usd_amount, filled_amount_tokens=filled_tokens)
    _log_trade("buy", "bsc", token_checksum, result)
    return result


TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # keccak256("Transfer(address,address,uint256)"), verified via Web3.keccak


def _get_evm_fill_amount(chain: str, receipt: dict, token_address: str, wallet_address: str) -> Optional[float]:
    """Reads the REAL number of tokens received in a confirmed buy, by
    scanning the receipt's Transfer event logs for one emitted BY the
    token contract, TO our wallet -- not amountOutMin (which is
    deliberately 0, since neither router call here has a slippage floor
    of its own) and not a pre-trade estimate. Needs real on-chain decimals
    to convert the raw log value, so this queries them fresh rather than
    assuming 18 (most BEP-20s are 18, but the field itself is per-token
    and assuming wrong here would corrupt every downstream trim calc).

    chain: which RPC pool to query for decimals -- FIXED Sept 25 2026,
    this used to be hardcoded to "bsc" (as the old _get_bsc_fill_amount) even
    though it was about to be reused for Robinhood Chain buys, which
    would have queried the wrong chain's RPC pool entirely for an RHC
    token's decimals and silently returned None (or, worse, another
    chain's coincidentally-valid-looking decimals value) forever."""
    from web3 import Web3  # type: ignore
    logs = receipt.get("logs") or []
    token_lower = token_address.lower()
    wallet_padded = "0x" + wallet_address.lower().replace("0x", "").rjust(64, "0")
    raw_value = None
    for log in logs:
        if (log.get("address") or "").lower() != token_lower:
            continue
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER_EVENT_TOPIC:
            continue
        if len(topics) < 3 or topics[2].lower() != wallet_padded:
            continue  # not a transfer TO our wallet
        try:
            raw_value = int(log.get("data"), 16)
        except (TypeError, ValueError):
            continue
    if raw_value is None:
        return None

    token = Web3().eth.contract(address=Web3.to_checksum_address(token_lower), abi=ERC20_ABI)
    decimals_calldata = _encode_router_call(token, "decimals", [])
    decimals_result = rpc_call(chain, "eth_call", [{"to": token_address, "data": decimals_calldata}, "latest"])
    if not decimals_result.get("ok"):
        return None
    try:
        decimals = int(decimals_result["result"], 16)
    except (TypeError, ValueError):
        return None
    return raw_value / (10 ** decimals)


def _confirm_evm_tx(chain: str, tx_hash: str, attempts: int = 10, delay_seconds: float = 3.0) -> dict:
    """Polls eth_getTransactionReceipt until the tx is mined and checks
    status == 1 (success) vs 0 (reverted on-chain -- e.g. a honeypot
    sell-blocking transfer, or slippage). A revert must never be reported
    as a successful buy even though it was successfully SUBMITTED. On
    success, also returns the full receipt (under "receipt") so callers
    that need the real logs (e.g. _get_evm_fill_amount) don't have to
    re-fetch it."""
    import time
    for _ in range(attempts):
        receipt = rpc_call(chain, "eth_getTransactionReceipt", [tx_hash])
        if receipt.get("ok") and receipt.get("result") is not None:
            status = receipt["result"].get("status")
            if status == "0x1":
                return {"ok": True, "receipt": receipt["result"]}
            if status == "0x0":
                return {"ok": False, "reason": "transaction was mined but reverted on-chain (status 0x0)"}
        time.sleep(delay_seconds)
    return {"ok": False, "reason": f"not confirmed after {attempts} polls ({attempts * delay_seconds:.0f}s)"}


def _rhc_native_price_usd(token_address: str) -> Optional[float]:
    """Derives native RHC ether's real USD price from DexScreener's own
    pair data for token_address -- same public endpoint layer0_scoring.py's
    fetch_dexscreener_vol_liq already uses (links.DEXSCREENER_CHAIN_SLUG's
    "robinhood" slug, confirmed live earlier this session via a real
    diagnostic run). DexScreener reports both priceUsd (the token's price
    in USD) and priceNative (the token's price in units of the pair's
    quote currency) for a pair -- when that quote currency IS native RHC
    ether (quoteToken.address == address(0), confirmed live Sept 25 2026
    via a real ROBINHOOD/ETH sample pair), priceUsd / priceNative is
    exactly native RHC ether's own USD price, derived from a real trading
    pair rather than a separate, unconfirmed price API. Picks the
    highest-liquidity native-quoted pair when several exist, for the same
    "deepest pool is most representative" reasoning fetch_dexscreener_vol_liq
    already uses. Returns None (never a guess) if no native-quoted pair
    exists for this token or the fields can't be parsed."""
    slug = DEXSCREENER_CHAIN_SLUG.get("robinhood_chain")
    if not slug:
        return None
    result = get_json(f"https://api.dexscreener.com/token-pairs/v1/{slug}/{token_address}")
    if not result.get("ok"):
        return None
    pairs = result.get("json")
    if not isinstance(pairs, list) or not pairs:
        return None
    native_quoted = [
        p for p in pairs
        if (p.get("quoteToken") or {}).get("address", "").lower() == RHC_NATIVE_CURRENCY.lower()
    ]
    if not native_quoted:
        return None
    best = max(native_quoted, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    try:
        price_usd = float(best["priceUsd"])
        price_native = float(best["priceNative"])
    except (KeyError, TypeError, ValueError):
        return None
    if price_native <= 0:
        return None
    return price_usd / price_native


def execute_buy_robinhood_chain(token_address: str, usd_amount: float) -> ExecutionResult:
    """Real Uniswap v4 buy via Robinhood Chain's Universal Router, built
    Sept 25 2026 on top of rhc_pool_discovery.find_v4_pool (real on-chain
    pool lookup) and rhc_v4_swap.build_v4_exact_in_single_calldata (real
    ABI-encoded swap calldata, confirmed byte-for-byte against official
    Uniswap sources -- see that module's docstring). Router address
    confirmed Sept 22, 2026 (see module docstring above). Pays with native
    RHC ether via the transaction's own msg.value, so no Permit2/approve
    step is needed here (that's only required for an ERC20 input, i.e.
    the sell side -- not yet built).

    Same honest status as every other chain here until a real send
    confirms it: written, guarded, but UNTESTED against a live network.
    Do not fund a wallet against this path without first running one
    small real test buy."""
    guard = _refuse_unless_ready("robinhood_chain")
    if guard:
        return guard
    try:
        from web3 import Web3  # type: ignore
        from eth_account import Account  # type: ignore
    except ImportError:
        return ExecutionResult(False, "web3.py not installed -- add to requirements.txt before enabling")

    try:
        token_checksum = Web3.to_checksum_address(token_address.lower())
    except ValueError:
        return ExecutionResult(False, f"'{token_address}' is not a valid Robinhood Chain address -- "
                                       f"refusing to build a tx to it")

    native_price = _rhc_native_price_usd(token_checksum)
    if native_price is None or native_price <= 0:
        return ExecutionResult(False, "could not derive a live RHC-native/USD price from DexScreener -- "
                                       "refusing to size a buy on a guess")
    amount_in_wei = int((usd_amount / native_price) * 10 ** 18)
    if amount_in_wei <= 0:
        return ExecutionResult(False, f"usd_amount ${usd_amount} converts to 0 wei at derived price "
                                       f"${native_price} -- refusing a zero-size buy")

    pool = find_v4_pool(token_checksum, quote_currency=RHC_NATIVE_CURRENCY)
    if not pool.get("ok"):
        return ExecutionResult(False, f"no Uniswap v4 pool found for this token: {pool.get('reason')}")
    pool_key = pool["pool_key"]
    # Native currency address(0) always sorts as currency0 (uint160(0) is
    # the smallest possible value) -- confirmed by rhc_pool_discovery.py's
    # own sort-rule docstring -- so paying with native RHC ether is always
    # a currency0 -> currency1 swap here.
    zero_for_one = pool_key["currency0"].lower() == RHC_NATIVE_CURRENCY.lower()
    if not zero_for_one:
        # Should not happen given find_v4_pool was called with native as
        # quote_currency, but refuse rather than silently swap the wrong
        # direction if the sort assumption is ever wrong.
        return ExecutionResult(False, "pool_key's currency0 was not native RHC ether -- refusing to "
                                       "guess swap direction")

    account = Account.from_key(EXECUTOR_CONFIG.rhc_private_key)
    deadline = int(__import__("time").time()) + 300
    try:
        calldata = build_v4_exact_in_single_calldata(
            pool_key, zero_for_one=True, amount_in=amount_in_wei, amount_out_minimum=0, deadline=deadline,
        )
    except ValueError as exc:
        return ExecutionResult(False, f"could not build v4 swap calldata: {exc}")

    nonce_result = rpc_call("robinhood_chain", "eth_getTransactionCount", [account.address, "pending"])
    if not nonce_result.get("ok"):
        return ExecutionResult(False, f"could not fetch nonce: {nonce_result.get('reason')}")
    gas_price_result = rpc_call("robinhood_chain", "eth_gasPrice", [])
    if not gas_price_result.get("ok"):
        return ExecutionResult(False, f"could not fetch gas price: {gas_price_result.get('reason')}")

    tx = {
        "to": Web3.to_checksum_address(UNISWAP_V4_UNIVERSAL_ROUTER_RHC),
        "value": amount_in_wei,
        "gas": 500_000,  # conservative fixed limit, same reasoning as BSC's buy path: no estimate_gas
                          # against an untrusted new token contract pre-buy. v4 swaps do more work per
                          # call than a v2 router hop, so this is higher than BSC's 400_000.
        "gasPrice": int(gas_price_result["result"], 16),
        "nonce": int(nonce_result["result"], 16),
        "chainId": ROBINHOOD_CHAIN_ID,
        "data": calldata.hex() if not calldata.hex().startswith("0x") else calldata.hex(),
    }
    if not tx["data"].startswith("0x"):
        tx["data"] = "0x" + tx["data"]
    signed = account.sign_transaction(tx)
    raw_hex = signed.raw_transaction.hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex

    send_result = rpc_call("robinhood_chain", "eth_sendRawTransaction", [raw_hex])
    if not send_result.get("ok"):
        return ExecutionResult(False, f"eth_sendRawTransaction failed: {send_result.get('reason')}")
    tx_hash = send_result.get("result")
    if not isinstance(tx_hash, str):
        return ExecutionResult(False, f"eth_sendRawTransaction returned no usable hash: {send_result}")

    confirmed = _confirm_evm_tx("robinhood_chain", tx_hash)
    if not confirmed.get("ok"):
        return ExecutionResult(False, f"sent but could not confirm (check hash manually): "
                                       f"{confirmed.get('reason')}", tx_signature=tx_hash)
    filled_tokens = _get_evm_fill_amount("robinhood_chain", confirmed.get("receipt") or {}, token_checksum, account.address)
    result = ExecutionResult(True, "buy submitted and confirmed", tx_signature=tx_hash,
                              filled_usd=usd_amount, filled_amount_tokens=filled_tokens)
    _log_trade("buy", "robinhood_chain", token_checksum, result)
    return result


def execute_sell(chain: str, token_address: str, amount_tokens: float, reason: str) -> ExecutionResult:
    """Used by defensive_sell.py when Layer 6's rug signal fires on a
    position this executor opened. Same enable/key guard as the buy paths.
    Routes to the chain-specific sell implementation below -- Solana and
    BSC only; Robinhood Chain sells are blocked on the same v4 pool-key
    gap as RHC buys (see module NEXT_STEPS entry)."""
    guard = _refuse_unless_ready(chain)
    if guard:
        return guard
    if chain == "solana":
        result = _sell_solana(token_address, amount_tokens)
    elif chain == "bsc":
        result = _sell_bsc(token_address, amount_tokens)
    else:
        result = ExecutionResult(False, f"no sell path implemented for chain '{chain}' yet "
                                         f"(reason for this sell attempt: {reason})")
    result.filled_amount_tokens = result.filled_amount_tokens or (amount_tokens if result.ok else None)
    _log_trade("sell", chain, token_address, result, reason=reason)
    return result


def _sell_solana(token_mint: str, amount_tokens: float) -> ExecutionResult:
    """Sells amount_tokens of an SPL token back to SOL via Jupiter --
    mirrors execute_buy_solana's quote -> swap-tx -> sign -> send ->
    confirm pipeline, in the reverse direction. Fetches the mint's real
    decimals on-chain first rather than assuming 9 (SPL tokens commonly
    use 6 or 9, occasionally other values -- assuming wrong would size
    every sell off by orders of magnitude)."""
    decimals_result = rpc_call("solana", "getAccountInfo", [token_mint, {"encoding": "base64"}])
    if not decimals_result.get("ok"):
        return ExecutionResult(False, f"could not fetch mint decimals: {decimals_result.get('reason')}")
    account_info = (decimals_result.get("result") or {}).get("value")
    if not account_info:
        return ExecutionResult(False, f"mint account '{token_mint}' not found on-chain")
    try:
        import base64 as b64mod
        raw = b64mod.b64decode(account_info["data"][0])
        decimals = raw[44]  # SPL Token Mint layout: decimals is the byte at offset 44
    except Exception as exc:  # noqa: BLE001
        return ExecutionResult(False, f"could not parse mint decimals: {exc}")

    raw_amount = int(amount_tokens * (10 ** decimals))
    quote = get_json(f"{CONFIG.jupiter_quote_base_url}/quote", params={
        "inputMint": token_mint,
        "outputMint": WRAPPED_SOL_MINT,
        "amount": raw_amount,
        "slippageBps": 150,  # wider than the buy's 100bps -- a rug-triggered sell needs to land, not get
                              # optimal price; too tight a slippage bound here risks the sell itself failing
    })
    if not quote.get("ok"):
        return ExecutionResult(False, f"jupiter sell quote failed: status {quote.get('status_code')}")

    try:
        pubkey = _solana_pubkey_from_private_key()
    except Exception as exc:  # noqa: BLE001
        return ExecutionResult(False, f"could not derive wallet pubkey: {exc}")

    swap_resp = post_json(f"{CONFIG.jupiter_quote_base_url}/swap", json={
        "quoteResponse": quote.get("json"),
        "userPublicKey": pubkey,
        "wrapAndUnwrapSol": True,
    })
    if not swap_resp.get("ok"):
        return ExecutionResult(False, f"jupiter sell swap-tx build failed: status {swap_resp.get('status_code')}")

    result = _sign_and_send_solana_swap(swap_resp)
    if result.ok and result.tx_signature:
        # Real SOL received, from the confirmed tx's own balance diff (not the
        # quote's pre-trade estimate) -- this is what was missing before Sept 25,
        # 2026: filled_usd was never set on sells, so every close_position() and
        # moonbag trim downstream computed pnl_usd/exit_usd off of None and
        # silently produced no realized-P&L number at all.
        sol_received = _get_solana_native_sol_delta(result.tx_signature)
        if sol_received is not None:
            sol_price = _sol_price_usd()
            if sol_price:
                result.filled_usd = sol_received * sol_price
    return result


def _get_solana_native_sol_delta(tx_sig: str) -> Optional[float]:
    """Real native-SOL balance change for the fee-payer account (always
    accountKeys[0]/preBalances[0]/postBalances[0] in a Solana transaction,
    by protocol convention -- Jupiter builds the swap tx with our wallet as
    fee payer) across a confirmed sell. This is the actual SOL received,
    net of the tx fee, read from the chain itself -- not the quote's
    pre-trade estimate."""
    tx_result = rpc_call("solana", "getTransaction", [
        tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
    ])
    if not tx_result.get("ok"):
        return None
    meta = ((tx_result.get("result") or {}).get("meta")) or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if not pre or not post:
        return None
    delta_lamports = post[0] - pre[0]
    return delta_lamports / 1_000_000_000 if delta_lamports > 0 else None


def _sign_and_send_solana_swap(swap_resp: dict) -> ExecutionResult:
    """Shared sign+send+confirm tail for both Solana buys and sells --
    factored out here (rather than duplicated) so a future fix to the
    signing/submission logic only has to happen once."""
    try:
        import base64
        from solders.keypair import Keypair  # type: ignore
        from solders.transaction import VersionedTransaction  # type: ignore
        import base58  # type: ignore
        swap_tx_b64 = (swap_resp.get("json") or {}).get("swapTransaction")
        if not swap_tx_b64:
            return ExecutionResult(False, "jupiter swap response had no swapTransaction field")
        keypair = Keypair.from_bytes(base58.b58decode(EXECUTOR_CONFIG.solana_private_key))
        unsigned_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
        signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])
        signed_tx_b64 = base64.b64encode(bytes(signed_tx)).decode("ascii")
    except ImportError:
        return ExecutionResult(False, "solders/base58 not installed -- add to requirements.txt before enabling")
    except Exception as exc:  # noqa: BLE001
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
        return ExecutionResult(False, f"sent but could not confirm (check signature manually): "
                                       f"{confirmed.get('reason')}", tx_signature=tx_sig)
    return ExecutionResult(True, "sell submitted and confirmed", tx_signature=tx_sig)


ERC20_ABI = [
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

PANCAKE_ROUTER_SELL_ABI = PANCAKE_ROUTER_ABI + [{
    "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
    "type": "function",
    "stateMutability": "nonpayable",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "amountOutMin", "type": "uint256"},
        {"name": "path", "type": "address[]"},
        {"name": "to", "type": "address"},
        {"name": "deadline", "type": "uint256"},
    ],
    "outputs": [],
}]


def _sell_bsc(token_address: str, amount_tokens: float) -> ExecutionResult:
    """Sells amount_tokens of a BEP-20 token back to BNB via PancakeSwap V2.
    Unlike the buy path, this needs two on-chain steps -- approve() then
    swap -- since the router has to be allowed to move a token the wallet
    holds (buys pay with native BNB via msg.value and skip this entirely).
    If approve succeeds but the swap fails, the allowance is left set;
    that's a state defensive_sell.py's caller should know about, so it's
    called out explicitly in the failure reason rather than silently
    retried, which could double-approve or mis-size a retry."""
    from web3 import Web3  # type: ignore
    from eth_account import Account  # type: ignore

    try:
        token_checksum = Web3.to_checksum_address(token_address.lower())
    except ValueError:
        return ExecutionResult(False, f"'{token_address}' is not a valid BSC address -- refusing to build a tx to it")

    w3 = Web3()
    account = Account.from_key(EXECUTOR_CONFIG.bsc_private_key)
    token = w3.eth.contract(address=token_checksum, abi=ERC20_ABI)

    decimals_calldata = _encode_router_call(token, "decimals", [])
    decimals_result = rpc_call("bsc", "eth_call", [{"to": token_checksum, "data": decimals_calldata}, "latest"])
    if not decimals_result.get("ok"):
        return ExecutionResult(False, f"could not fetch token decimals: {decimals_result.get('reason')}")
    try:
        decimals = int(decimals_result["result"], 16)
    except (TypeError, ValueError):
        return ExecutionResult(False, f"could not parse token decimals from {decimals_result.get('result')}")
    raw_amount = int(amount_tokens * (10 ** decimals))

    router_addr = Web3.to_checksum_address(PANCAKESWAP_V2_ROUTER_BSC)

    allowance_calldata = _encode_router_call(token, "allowance", [account.address, router_addr])
    allowance_result = rpc_call("bsc", "eth_call", [{"to": token_checksum, "data": allowance_calldata}, "latest"])
    current_allowance = int(allowance_result["result"], 16) if allowance_result.get("ok") else 0

    if current_allowance < raw_amount:
        approve_calldata = _encode_router_call(token, "approve", [router_addr, 2**256 - 1])  # unlimited, one-time
        approve_result = _sign_and_send_bsc_tx(account, token_checksum, 0, approve_calldata)
        if not approve_result.ok:
            return ExecutionResult(False, f"approve() failed, no swap attempted: {approve_result.reason}")
        confirmed = _confirm_evm_tx("bsc", approve_result.tx_signature)
        if not confirmed.get("ok"):
            return ExecutionResult(False, f"approve() sent but could not confirm -- swap NOT attempted, "
                                           f"check allowance manually before retrying: {confirmed.get('reason')}")

    router = w3.eth.contract(address=router_addr, abi=PANCAKE_ROUTER_SELL_ABI)

    # Estimated BNB-out via the router's own getAmountsOut, taken right before
    # the swap -- used only to price filled_usd for the dashboard/realized-P&L
    # (Tasks Left #3/#4, Sept 25 2026); this is a pre-trade estimate, not a
    # real balance diff like the Solana sell path gets, since PancakeSwap V2
    # sends native BNB via an internal call that doesn't show up as a log --
    # still real and quote-derived, not a guess, and far better than the
    # previous behavior of leaving filled_usd unset on every BSC sell.
    filled_usd = None
    try:
        amounts_out_calldata = _encode_router_call(router, "getAmountsOut", [
            raw_amount, [token_checksum, Web3.to_checksum_address(WBNB_BSC)],
        ])
        amounts_out_result = rpc_call("bsc", "eth_call", [{"to": router_addr, "data": amounts_out_calldata}, "latest"])
        if amounts_out_result.get("ok"):
            raw = amounts_out_result.get("result")
            if isinstance(raw, str) and raw.startswith("0x"):
                decoded = w3.codec.decode(["uint256[]"], bytes.fromhex(raw[2:]))
                bnb_out_wei = decoded[0][1]
                bnb_price = _bnb_price_usd()
                if bnb_price:
                    filled_usd = (bnb_out_wei / 10**18) * bnb_price
    except Exception:  # noqa: BLE001 -- pricing must never block a real sell
        filled_usd = None

    deadline = int(__import__("time").time()) + 300
    swap_calldata = _encode_router_call(router, "swapExactTokensForETHSupportingFeeOnTransferTokens", [
        raw_amount, 0, [token_checksum, Web3.to_checksum_address(WBNB_BSC)], account.address, deadline,
    ])
    swap_result = _sign_and_send_bsc_tx(account, router_addr, 0, swap_calldata)
    if not swap_result.ok:
        return ExecutionResult(False, f"approve() succeeded but swap failed: {swap_result.reason}")

    confirmed = _confirm_evm_tx("bsc", swap_result.tx_signature)
    if not confirmed.get("ok"):
        return ExecutionResult(False, f"sell sent but could not confirm (check hash manually): "
                                       f"{confirmed.get('reason')}", tx_signature=swap_result.tx_signature)
    return ExecutionResult(True, "sell submitted and confirmed", tx_signature=swap_result.tx_signature,
                            filled_usd=filled_usd)


def _sign_and_send_bsc_tx(account, to_address: str, value_wei: int, calldata: str) -> ExecutionResult:
    """Shared nonce/gas/sign/send tail for any BSC transaction (approve or
    swap) -- factored out so execute_buy_bsc's pattern isn't duplicated a
    third time."""
    nonce_result = rpc_call("bsc", "eth_getTransactionCount", [account.address, "pending"])
    if not nonce_result.get("ok"):
        return ExecutionResult(False, f"could not fetch nonce: {nonce_result.get('reason')}")
    gas_price_result = rpc_call("bsc", "eth_gasPrice", [])
    if not gas_price_result.get("ok"):
        return ExecutionResult(False, f"could not fetch gas price: {gas_price_result.get('reason')}")

    tx = {
        "to": to_address,
        "value": value_wei,
        "gas": 250_000,
        "gasPrice": int(gas_price_result["result"], 16),
        "nonce": int(nonce_result["result"], 16),
        "chainId": 56,
        "data": calldata,
    }
    signed = account.sign_transaction(tx)
    raw_hex = signed.raw_transaction.hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex

    send_result = rpc_call("bsc", "eth_sendRawTransaction", [raw_hex])
    if not send_result.get("ok"):
        return ExecutionResult(False, f"eth_sendRawTransaction failed: {send_result.get('reason')}")
    tx_hash = send_result.get("result")
    if not isinstance(tx_hash, str):
        return ExecutionResult(False, f"eth_sendRawTransaction returned no usable hash: {send_result}")
    return ExecutionResult(True, "submitted", tx_signature=tx_hash)


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

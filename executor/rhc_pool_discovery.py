"""
Uniswap v4 pool discovery for Robinhood Chain -- real on-chain lookup, not a
guess. Added Sept 25 2026.

Why this exists: unlike Uniswap v2/v3, v4 has no per-token-pair factory
lookup. Every pool is identified by a PoolKey (currency0, currency1, fee,
tickSpacing, hooks) hashed into a poolId, and there is no way to go from
"I have this token's address" to "here is its PoolKey" except by finding
the real Initialize event PoolManager emitted when the pool was created.
DexScreener (already used elsewhere in this codebase for vol/liq data) was
checked live Sept 25 2026 and confirmed it does NOT expose fee/tickSpacing/
hooks for v4 pools -- only a `pairAddress` field that is actually the
32-byte poolId hash, not a separate contract. So this queries the real
source of truth directly: PoolManager's own emitted event log.

PoolManager address (0x8366a39cc670b4001a1121b8f6a443a643e40951) -- from
Uniswap's own official GitHub deployments file
(github.com/Uniswap/contracts/blob/main/deployments/4663.md), independently
confirmed by Ali Sept 25 2026 on robinhoodchain.blockscout.com: a verified
contract explicitly labeled "PoolManager" -- same two-source-plus-on-chain-
label confirmation standard already used for UNISWAP_V4_UNIVERSAL_ROUTER_RHC
in swap_executor.py.

Initialize event signature -- confirmed verbatim against Uniswap's own
v4-core source (github.com/Uniswap/v4-core, src/interfaces/IPoolManager.sol)
and cross-checked against the official "Create a Pool on Uniswap v4" guide
(developers.uniswap.org/docs/protocols/v4/guides/create-pool), which
independently agrees on the PoolKey field order and the
"uint160(currency0) < uint160(currency1)" sort rule:

    event Initialize(
        PoolId indexed id,
        Currency indexed currency0,
        Currency indexed currency1,
        uint24 fee,
        int24 tickSpacing,
        IHooks hooks,
        uint160 sqrtPriceX96,
        int24 tick
    );

Topic0 (the event signature hash) is COMPUTED here, not copy-pasted from
anywhere -- keccak256("Initialize(bytes32,address,address,uint24,int24,
address,uint160,int24)"), verified via a real run:
    0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438
(PoolId/Currency/IHooks are all thin wrappers over bytes32/address/address
at the ABI level, which is what the event signature hash is computed over.)

Native RHC ether is represented as currency address(0) in v4 (confirmed by
DexScreener's own sample data: a real live ROBINHOOD/ETH pair had
quoteToken.address == "0x0000000000000000000000000000000000000000") --
this module's default quote_currency reflects that.
"""
from typing import Optional

from executor.rpc_pool import rpc_call

UNISWAP_V4_POOL_MANAGER_RHC = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
INITIALIZE_EVENT_TOPIC0 = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
NATIVE_CURRENCY = "0x0000000000000000000000000000000000000000"

# Real public RHC RPC (see rpc_pool.py) is a single, rate-limited endpoint --
# eth_getLogs is scanned backward in bounded chunks rather than one
# unbounded from-genesis query, since most RPC providers cap the block
# range per call and this codebase has no confirmed number for this one.
_LOG_SCAN_CHUNK_BLOCKS = 5000
_LOG_SCAN_MAX_CHUNKS = 20  # bounds worst-case call count if a token has no pool at all


def _topic_to_address(topic: str) -> str:
    """An indexed `address`/`Currency` event topic is left-padded to 32
    bytes -- the real address is the last 20 bytes (40 hex chars)."""
    return "0x" + topic[-40:]


def _address_to_topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def _decode_initialize_data(data_hex: str) -> dict:
    """Decodes the non-indexed fields of an Initialize event's data blob:
    fee (uint24), tickSpacing (int24), hooks (address), sqrtPriceX96
    (uint160), tick (int24) -- each right-aligned in its own 32-byte word,
    standard Solidity event ABI encoding."""
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    words = [raw[i:i + 64] for i in range(0, len(raw), 64)]
    if len(words) < 5:
        raise ValueError(f"Initialize event data has {len(words)} words, expected 5")
    def _signed_word(word: str) -> int:
        # Solidity ABI-encodes a negative signed int by sign-extending
        # the value across the FULL 32-byte word (two's complement over
        # 256 bits), not just its native bit width -- so the sign check
        # has to look at the word's top bit (>= 2**255), not at 2**23/
        # 2**24 as if only the low 3 bytes carried the sign. An earlier
        # version of this function got that wrong (checked >= 2**23 and
        # subtracted 2**24, which is only correct if the word were
        # zero-padded rather than sign-extended) -- caught by
        # tests/test_rhc_pool_discovery.py's negative-tickSpacing case
        # before this was ever used against a live RPC response.
        raw = int(word, 16)
        return raw - 2 ** 256 if raw >= 2 ** 255 else raw

    fee = int(words[0], 16)  # uint24 -- unsigned, no sign fix needed
    tick_spacing = _signed_word(words[1])
    hooks = "0x" + words[2][-40:]
    sqrt_price_x96 = int(words[3], 16)  # uint160 -- unsigned
    tick = _signed_word(words[4])
    return {
        "fee": fee,
        "tick_spacing": tick_spacing,
        "hooks": hooks,
        "sqrt_price_x96": sqrt_price_x96,
        "tick": tick,
    }


def find_v4_pool(token_address: str, quote_currency: str = NATIVE_CURRENCY,
                  latest_block: Optional[int] = None) -> dict:
    """Scans PoolManager's real Initialize event logs backward from the
    chain tip, looking for a pool pairing token_address with
    quote_currency. Returns {"ok": True, "pool_key": {...}, "block": ...}
    on a real match, or {"ok": False, "reason": ...} if nothing was found
    within the scanned range or the RPC call itself failed -- callers must
    treat "not found" as "this token has no v4 pool yet (or not within the
    scanned depth)", not as an error to retry blindly.

    latest_block: inject a specific starting block for deterministic
    testing; omitted in real use (fetches the real chain tip first)."""
    if latest_block is None:
        tip = rpc_call("robinhood_chain", "eth_blockNumber", [])
        if not tip.get("ok"):
            return {"ok": False, "reason": f"could not fetch chain tip: {tip.get('reason')}"}
        latest_block = int(tip["result"], 16)

    token_topic = _address_to_topic(token_address)
    quote_topic = _address_to_topic(quote_currency)

    to_block = latest_block
    for _ in range(_LOG_SCAN_MAX_CHUNKS):
        from_block = max(0, to_block - _LOG_SCAN_CHUNK_BLOCKS)
        result = rpc_call("robinhood_chain", "eth_getLogs", [{
            "address": UNISWAP_V4_POOL_MANAGER_RHC,
            "topics": [INITIALIZE_EVENT_TOPIC0],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }])
        if not result.get("ok"):
            return {"ok": False, "reason": f"eth_getLogs failed: {result.get('reason')}"}

        for log in result.get("result") or []:
            topics = log.get("topics") or []
            if len(topics) < 4:
                continue
            currency0_topic, currency1_topic = topics[2], topics[3]
            pair = {currency0_topic, currency1_topic}
            if pair != {token_topic, quote_topic}:
                continue
            try:
                decoded = _decode_initialize_data(log.get("data", "0x"))
            except (ValueError, IndexError) as exc:
                return {"ok": False, "reason": f"could not decode Initialize event data: {exc}"}
            return {
                "ok": True,
                "block": int(log.get("blockNumber", "0x0"), 16),
                "pool_key": {
                    "currency0": _topic_to_address(currency0_topic),
                    "currency1": _topic_to_address(currency1_topic),
                    "fee": decoded["fee"],
                    "tick_spacing": decoded["tick_spacing"],
                    "hooks": decoded["hooks"],
                },
                "pool_id": topics[1],
                "sqrt_price_x96_at_init": decoded["sqrt_price_x96"],
            }

        if from_block == 0:
            break
        to_block = from_block - 1

    return {"ok": False, "reason": f"no v4 pool found for {token_address} paired with "
                                    f"{quote_currency} within the last "
                                    f"{_LOG_SCAN_MAX_CHUNKS * _LOG_SCAN_CHUNK_BLOCKS} blocks"}

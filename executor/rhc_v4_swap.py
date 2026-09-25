"""
Uniswap v4 swap-calldata construction for Robinhood Chain's Universal
Router -- the second half of RHC auto-buy (rhc_pool_discovery.find_v4_pool
finds the pool; this builds and encodes the actual swap transaction).
Added Sept 25 2026.

Every command/action byte value and struct layout here was confirmed
against official Uniswap sources before being used -- not guessed, and
not taken from a single AI-summarized fetch on its own. One early check
of IV4Router.sol via a page-summarizing fetch returned a 6-field
ExactInputSingleParams struct with an extra `minHopPriceX36` field that
does NOT appear in Uniswap's own official docs code example or in
V4Router.sol's own action-decoding code -- discarded as a likely stale/
wrong-branch summarization artifact, never used here.

- V4_SWAP command byte (0x10): confirmed via
  github.com/Uniswap/universal-router/blob/main/contracts/libraries/Commands.sol
- SWAP_EXACT_IN_SINGLE (0x06), SETTLE_ALL (0x0c), TAKE_ALL (0x0f) action
  bytes: confirmed via
  github.com/Uniswap/v4-periphery/blob/main/src/libraries/Actions.sol
- ExactInputSingleParams struct (poolKey, zeroForOne, amountIn,
  amountOutMinimum, hookData -- 5 fields): confirmed via Uniswap's own
  official docs code example
  (docs.uniswap.org/sdk/v4/guides/swaps/single-hop-swapping), which shows
  the real v4Planner.addAction(Actions.SWAP_EXACT_IN_SINGLE, [config])
  call with exactly these 5 fields, PoolKey field order (currency0,
  currency1, fee, tickSpacing, hooks) matching rhc_pool_discovery.py's
  own already-confirmed PoolKey layout.
- SETTLE_ALL/TAKE_ALL both decode as (Currency currency, uint256 amount):
  confirmed directly against V4Router.sol's own action-handling code
  (`params.decodeCurrencyAndUint256()` used for both), not inferred from
  the JS example alone.
- execute(bytes,bytes[],uint256) selector 0x3593564c: computed locally
  here via keccak256 of the function signature (eth_utils, no network
  call, no guess) -- matches the selector real Universal Router
  transactions are publicly known to use.

Encoding, per the official docs example's own structure:
  1. actions = bytes1(SWAP_EXACT_IN_SINGLE) ++ bytes1(SETTLE_ALL) ++ bytes1(TAKE_ALL)
  2. params[0] = abi.encode(ExactInputSingleParams)
  3. params[1] = abi.encode(currency_in, amount_in)          -- SETTLE_ALL
  4. params[2] = abi.encode(currency_out, amount_out_minimum) -- TAKE_ALL
  5. v4_swap_input = abi.encode(bytes actions, bytes[] params)
  6. commands = bytes1(V4_SWAP)
  7. calldata = selector + abi.encode(commands, [v4_swap_input], deadline)

This module ONLY builds calldata -- pure functions, no network/RPC calls,
fully unit-testable in isolation. Signing and sending it is
swap_executor.py's job, same as every other chain there. Native-currency
(RHC ether, address(0)) swaps need no Permit2 step, since the input value
is sent as the transaction's own msg.value and settled directly -- an
ERC20-input swap (needed for RHC auto-SELL, not yet built) would need a
Permit2 approve/permit step first, which this module does not attempt.
"""
from typing import Optional

from eth_abi import encode as abi_encode
from eth_utils import function_signature_to_4byte_selector

V4_SWAP_COMMAND = 0x10
ACTION_SWAP_EXACT_IN_SINGLE = 0x06
ACTION_SETTLE_ALL = 0x0C
ACTION_TAKE_ALL = 0x0F

POOL_KEY_ABI_TYPE = "(address,address,uint24,int24,address)"
EXACT_INPUT_SINGLE_ABI_TYPE = f"({POOL_KEY_ABI_TYPE},bool,uint128,uint128,bytes)"

EXECUTE_SELECTOR = function_signature_to_4byte_selector("execute(bytes,bytes[],uint256)")

UINT128_MAX = 2 ** 128 - 1


def _pool_key_tuple(pool_key: dict) -> tuple:
    return (
        pool_key["currency0"],
        pool_key["currency1"],
        int(pool_key["fee"]),
        int(pool_key["tick_spacing"]),
        pool_key["hooks"],
    )


def build_v4_exact_in_single_calldata(pool_key: dict, zero_for_one: bool, amount_in: int,
                                       amount_out_minimum: int, deadline: int,
                                       hook_data: Optional[bytes] = None) -> bytes:
    """Returns the full calldata (4-byte selector + ABI-encoded args) for
    a single call to UniversalRouter.execute() that performs one
    exact-input, single-hop v4 swap.

    pool_key: the dict returned by rhc_pool_discovery.find_v4_pool()'s
    "pool_key" field (currency0/currency1/fee/tick_spacing/hooks).
    zero_for_one: True if swapping currency0 -> currency1 (e.g. a buy
    paying with native RHC ether, since address(0) always sorts as
    currency0 -- see rhc_pool_discovery.py's NATIVE_CURRENCY note).
    amount_in / amount_out_minimum: raw integer amounts (wei / token's
    smallest unit), NOT floats -- callers must convert first, on-chain
    decimals for the OUT token, 18 for native RHC ether.
    deadline: unix timestamp after which the router must revert rather
    than execute a stale swap.

    Pure function -- takes an already-discovered PoolKey and
    already-decided amounts; does no chain I/O itself."""
    if amount_in <= 0:
        raise ValueError("amount_in must be positive")
    if amount_out_minimum < 0:
        raise ValueError("amount_out_minimum must not be negative")
    if amount_in > UINT128_MAX or amount_out_minimum > UINT128_MAX:
        raise ValueError("amount_in/amount_out_minimum must fit in uint128 (v4's native swap amount type)")

    pool_key_tuple = _pool_key_tuple(pool_key)
    currency_in = pool_key_tuple[0] if zero_for_one else pool_key_tuple[1]
    currency_out = pool_key_tuple[1] if zero_for_one else pool_key_tuple[0]

    actions = bytes([ACTION_SWAP_EXACT_IN_SINGLE, ACTION_SETTLE_ALL, ACTION_TAKE_ALL])

    exact_input_single_params = (pool_key_tuple, zero_for_one, amount_in, amount_out_minimum, hook_data or b"")
    param0 = abi_encode([EXACT_INPUT_SINGLE_ABI_TYPE], [exact_input_single_params])
    param1 = abi_encode(["address", "uint256"], [currency_in, amount_in])
    param2 = abi_encode(["address", "uint256"], [currency_out, amount_out_minimum])

    v4_swap_input = abi_encode(["bytes", "bytes[]"], [actions, [param0, param1, param2]])
    commands = bytes([V4_SWAP_COMMAND])

    args = abi_encode(["bytes", "bytes[]", "uint256"], [commands, [v4_swap_input], deadline])
    return EXECUTE_SELECTOR + args

"""Tests for executor/rhc_v4_swap.py -- pure calldata construction for a
Uniswap v4 exact-input single-hop swap via Robinhood Chain's Universal
Router. Added Sept 25 2026.

No network/RPC involved (the module itself makes none) -- these tests
decode the built calldata back apart with eth_abi and check every field
round-trips correctly, catching encoding-order or type mistakes before
this is ever used against a live RPC endpoint with real money behind it.
"""
import pytest
from eth_abi import decode as abi_decode

import executor.rhc_v4_swap as rvs

POOL_KEY = {
    "currency0": "0x0000000000000000000000000000000000000000",
    "currency1": "0x1111111111111111111111111111111111111111",
    "fee": 3000,
    "tick_spacing": 60,
    "hooks": "0x2222222222222222222222222222222222222222",
}


def test_execute_selector_is_the_real_known_universal_router_selector():
    # 0x3593564c is the publicly known selector for
    # execute(bytes,bytes[],uint256) on Uniswap's Universal Router --
    # computed here via keccak256, not hardcoded, so this test is really
    # checking the computation, not just asserting a literal.
    assert rvs.EXECUTE_SELECTOR.hex() == "3593564c"


def test_build_calldata_starts_with_execute_selector():
    calldata = rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 10**18, 0, 9_999_999_999)
    assert calldata[:4] == rvs.EXECUTE_SELECTOR


def test_build_calldata_round_trips_commands_and_deadline():
    deadline = 1_800_000_000
    calldata = rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 10**18, 5, deadline)
    commands, inputs, decoded_deadline = abi_decode(["bytes", "bytes[]", "uint256"], calldata[4:])
    assert commands == bytes([rvs.V4_SWAP_COMMAND])
    assert len(inputs) == 1
    assert decoded_deadline == deadline


def test_build_calldata_zero_for_one_true_uses_currency0_as_input():
    # zero_for_one=True means paying with currency0 (native RHC ether in
    # POOL_KEY) to receive currency1 -- confirms SETTLE_ALL's currency
    # param is currency0 and TAKE_ALL's is currency1, not swapped.
    calldata = rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 10**18, 7, 9_999_999_999)
    _, inputs, _ = abi_decode(["bytes", "bytes[]", "uint256"], calldata[4:])
    actions, params = abi_decode(["bytes", "bytes[]"], inputs[0])
    assert actions == bytes([rvs.ACTION_SWAP_EXACT_IN_SINGLE, rvs.ACTION_SETTLE_ALL, rvs.ACTION_TAKE_ALL])

    settle_currency, settle_amount = abi_decode(["address", "uint256"], params[1])
    take_currency, take_amount = abi_decode(["address", "uint256"], params[2])
    assert settle_currency.lower() == POOL_KEY["currency0"].lower()
    assert settle_amount == 10**18
    assert take_currency.lower() == POOL_KEY["currency1"].lower()
    assert take_amount == 7


def test_build_calldata_zero_for_one_false_uses_currency1_as_input():
    calldata = rvs.build_v4_exact_in_single_calldata(POOL_KEY, False, 500, 0, 9_999_999_999)
    _, inputs, _ = abi_decode(["bytes", "bytes[]", "uint256"], calldata[4:])
    _, params = abi_decode(["bytes", "bytes[]"], inputs[0])
    settle_currency, settle_amount = abi_decode(["address", "uint256"], params[1])
    take_currency, _ = abi_decode(["address", "uint256"], params[2])
    assert settle_currency.lower() == POOL_KEY["currency1"].lower()
    assert settle_amount == 500
    assert take_currency.lower() == POOL_KEY["currency0"].lower()


def test_build_calldata_exact_input_single_params_round_trip():
    calldata = rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 12345, 100, 9_999_999_999,
                                                       hook_data=b"\xab\xcd")
    _, inputs, _ = abi_decode(["bytes", "bytes[]", "uint256"], calldata[4:])
    _, params = abi_decode(["bytes", "bytes[]"], inputs[0])
    decoded = abi_decode([rvs.EXACT_INPUT_SINGLE_ABI_TYPE], params[0])[0]
    pool_key_tuple, zero_for_one, amount_in, amount_out_minimum, hook_data = decoded
    currency0, currency1, fee, tick_spacing, hooks = pool_key_tuple
    assert currency0.lower() == POOL_KEY["currency0"].lower()
    assert currency1.lower() == POOL_KEY["currency1"].lower()
    assert fee == POOL_KEY["fee"]
    assert tick_spacing == POOL_KEY["tick_spacing"]
    assert hooks.lower() == POOL_KEY["hooks"].lower()
    assert zero_for_one is True
    assert amount_in == 12345
    assert amount_out_minimum == 100
    assert hook_data == b"\xab\xcd"


def test_build_calldata_rejects_zero_or_negative_amount_in():
    with pytest.raises(ValueError):
        rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 0, 0, 9_999_999_999)
    with pytest.raises(ValueError):
        rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, -5, 0, 9_999_999_999)


def test_build_calldata_rejects_negative_amount_out_minimum():
    with pytest.raises(ValueError):
        rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 100, -1, 9_999_999_999)


def test_build_calldata_rejects_amount_exceeding_uint128():
    too_big = 2 ** 128
    with pytest.raises(ValueError):
        rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, too_big, 0, 9_999_999_999)
    with pytest.raises(ValueError):
        rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 100, too_big, 9_999_999_999)


def test_build_calldata_defaults_hook_data_to_empty_bytes():
    calldata = rvs.build_v4_exact_in_single_calldata(POOL_KEY, True, 100, 0, 9_999_999_999)
    _, inputs, _ = abi_decode(["bytes", "bytes[]", "uint256"], calldata[4:])
    _, params = abi_decode(["bytes", "bytes[]"], inputs[0])
    decoded = abi_decode([rvs.EXACT_INPUT_SINGLE_ABI_TYPE], params[0])[0]
    assert decoded[-1] == b""

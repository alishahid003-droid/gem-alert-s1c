"""
Tests for Layer 2b -- self-built pump.fun smart-money convergence (Ali,
Sept 23 2026: "can we not have a convergence layer based on top memecoin
traders on pump.fun for pump.fun launches"). Covers pump.fun trade decoding
(layers/pumpfun_trades.py) with realistic fixture transactions matching
pump.fun's real account/instruction shape, and the wallet-PnL/promotion/
convergence logic (layers/layer2b_pumpfun_smart_money.py).
"""
import base58
import pytest

import state
from layers.pumpfun_trades import decode_trade, BUY_DISCRIMINATOR, SELL_DISCRIMINATOR, PUMPFUN_PROGRAM_ID
from layers.layer2b_pumpfun_smart_money import (
    wallet_qualifies, process_trade, get_smart_money_roster, detect_pumpfun_convergence,
    MIN_CLOSED_TRADES_FOR_PROMOTION,
)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


WALLET = "WaLLeT111111111111111111111111111111111111"
MINT = "MintAaaa111111111111111111111111111111111"


def _fake_tx(direction: str, wallet: str = WALLET, mint: str = MINT,
             trader_idx: int = 6, sol_delta_lamports: int = -1_000_000_000, block_time: int = 1700000000):
    """Builds a minimal but structurally realistic getTransaction(jsonParsed)
    response for the pump.fun program's classic buy/sell instruction."""
    discriminator = BUY_DISCRIMINATOR if direction == "buy" else SELL_DISCRIMINATOR
    ix_data = base58.b58encode(bytes(discriminator) + b"\\x00" * 16).decode()
    # accounts list needs >= 7 entries so index 6 (trader) and 2 (mint) exist
    ix_accounts = ["acct0", "acct1", mint, "acct3", "acct4", "acct5", wallet]
    account_keys = ["feepayer"] + [f"acct{i}" for i in range(1, 6)] + [wallet]
    pre_balances = [5_000_000_000] * len(account_keys)
    post_balances = list(pre_balances)
    post_balances[trader_idx] = pre_balances[trader_idx] + sol_delta_lamports
    return {
        "blockTime": block_time,
        "meta": {"preBalances": pre_balances, "postBalances": post_balances},
        "transaction": {
            "message": {
                "accountKeys": account_keys,
                "instructions": [{"programId": PUMPFUN_PROGRAM_ID, "data": ix_data, "accounts": ix_accounts}],
            }
        },
    }


# --- decode_trade ---

def test_decode_buy():
    tx = _fake_tx("buy", sol_delta_lamports=-2_000_000_000)  # spent 2 SOL
    trade = decode_trade(tx)
    assert trade["direction"] == "buy"
    assert trade["wallet"] == WALLET
    assert trade["mint"] == MINT
    assert trade["sol_delta"] == pytest.approx(-2.0)


def test_decode_sell():
    tx = _fake_tx("sell", sol_delta_lamports=3_000_000_000)  # received 3 SOL
    trade = decode_trade(tx)
    assert trade["direction"] == "sell"
    assert trade["sol_delta"] == pytest.approx(3.0)


def test_decode_unrecognized_instruction_returns_none():
    tx = _fake_tx("buy")
    bogus = base58.b58encode(bytes([1, 2, 3, 4, 5, 6, 7, 8]) + b"\\x00" * 16).decode()
    tx["transaction"]["message"]["instructions"][0]["data"] = bogus
    assert decode_trade(tx) is None


def test_decode_malformed_tx_returns_none_not_crash():
    assert decode_trade({}) is None
    assert decode_trade(None) is None
    assert decode_trade({"transaction": {}}) is None


def test_decode_ignores_other_program_instructions():
    tx = _fake_tx("buy")
    tx["transaction"]["message"]["instructions"][0]["programId"] = "SomeOtherProgram11111111111111111111111111"
    assert decode_trade(tx) is None


# --- wallet promotion ---

def test_wallet_does_not_qualify_before_min_trades():
    stats = {"closed_trades": MIN_CLOSED_TRADES_FOR_PROMOTION - 1, "wins": MIN_CLOSED_TRADES_FOR_PROMOTION - 1, "total_realized_sol": 5.0}
    assert wallet_qualifies(stats) is False


def test_wallet_qualifies_with_good_track_record():
    stats = {"closed_trades": 5, "wins": 4, "total_realized_sol": 3.5}  # 80% win rate, positive PnL
    assert wallet_qualifies(stats) is True


def test_wallet_disqualified_by_negative_pnl_despite_good_win_rate():
    stats = {"closed_trades": 5, "wins": 4, "total_realized_sol": -1.0}
    assert wallet_qualifies(stats) is False


def test_process_trade_promotes_after_enough_winning_closes():
    promoted_flags = []
    for i in range(MIN_CLOSED_TRADES_FOR_PROMOTION):
        mint = f"mint{i}"
        process_trade(WALLET, mint, "buy", -1.0, ts=1700000000 + i * 100)
        flag = process_trade(WALLET, mint, "sell", 2.0, ts=1700000050 + i * 100)  # every trade a 2x win
        promoted_flags.append(flag)
    assert WALLET in get_smart_money_roster()
    assert promoted_flags[-1] is True  # promoted on the closing trade that crossed the threshold
    assert sum(promoted_flags) == 1  # only promoted once, not re-added every cycle


def test_sell_with_no_open_position_ignored():
    result = process_trade(WALLET, "orphan-mint", "sell", 2.0)
    assert result is False
    assert WALLET not in get_smart_money_roster()


# --- convergence reuse ---

def test_convergence_empty_when_roster_empty():
    buys = [{"wallet": WALLET, "mint": MINT, "block_time": 1700000000}]
    assert detect_pumpfun_convergence(buys) == []


def test_convergence_fires_for_two_smart_money_wallets_same_token():
    wallet_a, wallet_b = "WalletA1111111111111111111111111111111111", "WalletB1111111111111111111111111111111111"
    for w in (wallet_a, wallet_b):
        for i in range(MIN_CLOSED_TRADES_FOR_PROMOTION):
            m = f"{w}-mint{i}"
            process_trade(w, m, "buy", -1.0, ts=1700000000 + i * 100)
            process_trade(w, m, "sell", 2.0, ts=1700000050 + i * 100)
    assert wallet_a in get_smart_money_roster() and wallet_b in get_smart_money_roster()

    buys = [
        {"wallet": wallet_a, "mint": "NEWGEM", "block_time": 1800000000},
        {"wallet": wallet_b, "mint": "NEWGEM", "block_time": 1800000300},  # 5 min later, within 1hr window
    ]
    events = detect_pumpfun_convergence(buys)
    assert len(events) == 1
    assert events[0]["token"] == "NEWGEM"
    assert events[0]["count"] == 2


# --- Manual seeding (Ali, Sept 24 2026) ---
from layers.layer2b_pumpfun_smart_money import seed_manual_wallets, get_manual_seed_meta

GOOD_WALLET = "4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo"
GOOD_WALLET_2 = "4UrFSCrGxgoCtCUBAEZq7ZmPK3Pczkxx7PwYnkBMi1KR"


def test_seed_manual_wallets_adds_to_roster():
    result = seed_manual_wallets([GOOD_WALLET, GOOD_WALLET_2],
                                  source="pumpfun_leaderboard_1M", note="Ali-supplied Sept 24 2026")
    assert result["added"] == [GOOD_WALLET, GOOD_WALLET_2]
    assert result["already_present"] == []
    assert result["rejected"] == []
    roster = get_smart_money_roster()
    assert GOOD_WALLET in roster and GOOD_WALLET_2 in roster


def test_seed_manual_wallets_rejects_malformed():
    result = seed_manual_wallets(["not-a-wallet", "0OIl-invalid-chars"],
                                  source="test", note="")
    assert result["added"] == []
    assert len(result["rejected"]) == 2
    assert get_smart_money_roster() == set()


def test_seed_manual_wallets_dedupes_already_present():
    seed_manual_wallets([GOOD_WALLET], source="a", note="")
    result = seed_manual_wallets([GOOD_WALLET], source="b", note="")
    assert result["added"] == []
    assert result["already_present"] == [GOOD_WALLET]


def test_seed_manual_wallets_records_provenance():
    seed_manual_wallets([GOOD_WALLET], source="pumpfun_leaderboard_1M", note="rank #1")
    meta = get_manual_seed_meta()
    assert meta[GOOD_WALLET]["source"] == "pumpfun_leaderboard_1M"
    assert meta[GOOD_WALLET]["note"] == "rank #1"
    assert "added_ts" in meta[GOOD_WALLET]


def test_manually_seeded_wallet_participates_in_convergence():
    """A manually-seeded wallet must be detected the same as a live-promoted
    one -- convergence detection only checks roster membership."""
    seed_manual_wallets([GOOD_WALLET, GOOD_WALLET_2], source="test", note="")
    import time
    now = time.time()
    buys = [
        {"wallet": GOOD_WALLET, "mint": "SomeMint111111111111111111111111111111111", "block_time": now},
        {"wallet": GOOD_WALLET_2, "mint": "SomeMint111111111111111111111111111111111", "block_time": now + 60},
    ]
    convergences = detect_pumpfun_convergence(buys)
    assert len(convergences) == 1


# --- Single-wallet buy alerts (Ali, Sept 24 2026 -- "if 2 does not buy...
# then what will happen...dont u think we can miss on something") ---
from layers.layer2b_pumpfun_smart_money import single_wallet_buy_events

def test_single_wallet_buy_event_fires_for_one_tracked_wallet():
    """A lone roster wallet buying must still produce an event -- this is
    exactly the gap Ali flagged: convergence alone requires 2+."""
    seed_manual_wallets([GOOD_WALLET], source="pumpfun_leaderboard_1M", note="rank #2")
    buys = [{"wallet": GOOD_WALLET, "mint": "SomeMint111111111111111111111111111111111",
             "block_time": 1000.0}]
    events = single_wallet_buy_events(buys)
    assert len(events) == 1
    assert events[0]["wallet"] == GOOD_WALLET
    assert events[0]["source"] == "manual"
    assert events[0]["note"] == "rank #2"


def test_single_wallet_buy_event_ignores_untracked_wallet():
    seed_manual_wallets([GOOD_WALLET], source="test", note="")
    buys = [{"wallet": "SomeOtherWallet1111111111111111111111111111", "mint": "MintX",
             "block_time": 1000.0}]
    assert single_wallet_buy_events(buys) == []


def test_single_wallet_buy_event_empty_roster_no_cost():
    assert single_wallet_buy_events([{"wallet": GOOD_WALLET, "mint": "MintX", "block_time": 1.0}]) == []


def test_single_wallet_buy_event_tags_auto_promoted_wallet_correctly():
    """A wallet that earned its spot via live promotion (not manually
    seeded) should be tagged 'auto-promoted', not 'manual'."""
    process_trade(WALLET, MINT, "buy", -1.0, ts=1000.0)
    process_trade(WALLET, MINT, "sell", 2.0, ts=1010.0)
    for i in range(4):
        m = f"Mint{i}aaa111111111111111111111111111111111"
        process_trade(WALLET, m, "buy", -1.0, ts=2000.0 + i * 100)
        process_trade(WALLET, m, "sell", 2.0, ts=2010.0 + i * 100)
    assert WALLET in get_smart_money_roster()
    buys = [{"wallet": WALLET, "mint": "NewMint11111111111111111111111111111111111", "block_time": 3000.0}]
    events = single_wallet_buy_events(buys)
    assert len(events) == 1
    assert events[0]["source"] == "auto-promoted"

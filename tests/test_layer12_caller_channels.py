"""
Tests for Layer 12 -- Telegram caller-channel monitoring (Ali, Sept 28
2026). Covers layers/layer12_caller_channels.py's own functions
(address extraction, getUpdates parsing/filtering) and config.py's
telegram_caller_channel_ids()/layer12_ready() helpers.
"""
import pytest

import config as config_module
from config import Config
from layers.layer12_caller_channels import (
    fetch_caller_channel_posts, extract_token_addresses,
)


# --- extract_token_addresses: pure function, no network ---

def test_extract_solana_address_from_message():
    text = "New gem just launched: 2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP LFG"
    out = extract_token_addresses(text)
    assert "2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP" in out["solana"]
    assert out["evm"] == []


def test_extract_evm_address_lowercased():
    text = "BSC play: 0xAbCdEf0123456789aBcDeF0123456789ABCDEF00 watch this"
    out = extract_token_addresses(text)
    assert out["evm"] == ["0xabcdef0123456789abcdef0123456789abcdef00"]


def test_extract_multiple_distinct_addresses_deduped_and_sorted():
    text = "aaa 2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP bbb 2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP ccc"
    out = extract_token_addresses(text)
    assert out["solana"] == ["2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP"]


def test_extract_no_addresses_in_plain_text():
    out = extract_token_addresses("just chatting, no calls here today")
    assert out == {"solana": [], "evm": []}


def test_extract_handles_none_and_empty():
    assert extract_token_addresses(None) == {"solana": [], "evm": []}
    assert extract_token_addresses("") == {"solana": [], "evm": []}


def test_extract_ignores_tx_signature_length_string():
    # A real Solana tx signature is base58 but 87-88 chars -- well outside
    # the 32-44 char mint-address bound, so this must NOT match.
    long_sig = "5" * 88
    out = extract_token_addresses(f"tx: {long_sig}")
    assert out["solana"] == []


# --- fetch_caller_channel_posts: config gating + response parsing ---

def _cfg(bot_token=None, channel_ids_raw=None):
    return Config(telegram_caller_bot_token=bot_token, telegram_caller_channel_ids_raw=channel_ids_raw)


def test_fetch_fails_closed_when_bot_token_missing(monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG", _cfg(bot_token=None, channel_ids_raw="123"))
    import layers.layer12_caller_channels as mod
    monkeypatch.setattr(mod, "CONFIG", config_module.CONFIG)
    result = fetch_caller_channel_posts()
    assert result["ok"] is False
    assert "TELEGRAM_CALLER_BOT_TOKEN" in result["reason"]


def test_fetch_fails_closed_when_channel_ids_missing(monkeypatch):
    import layers.layer12_caller_channels as mod
    monkeypatch.setattr(mod, "CONFIG", _cfg(bot_token="fake-token", channel_ids_raw=None))
    result = fetch_caller_channel_posts()
    assert result["ok"] is False
    assert "TELEGRAM_CALLER_CHANNEL_IDS" in result["reason"]


def test_fetch_parses_and_filters_channel_posts(monkeypatch):
    import layers.layer12_caller_channels as mod
    monkeypatch.setattr(mod, "CONFIG", _cfg(bot_token="fake-token", channel_ids_raw="-1001,-1002"))

    def fake_get_json(url, params=None, timeout=None):
        return {"ok": True, "json": {"ok": True, "result": [
            {"update_id": 501, "channel_post": {
                "chat": {"id": -1001, "title": "Real Caller Channel"},
                "text": "new call: 2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP",
                "date": 1790000000,
            }},
            {"update_id": 502, "channel_post": {
                "chat": {"id": -9999, "title": "Unrelated channel bot is also in"},
                "text": "some other token 3sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXQ",
                "date": 1790000001,
            }},
            {"update_id": 503, "message": {"text": "not a channel post, a DM"}},
        ]}}
    monkeypatch.setattr(mod, "get_json", fake_get_json)

    result = fetch_caller_channel_posts()
    assert result["ok"] is True
    assert len(result["posts"]) == 1  # the -9999 channel and the DM are both filtered out
    assert result["posts"][0].channel_name == "Real Caller Channel"
    assert result["next_offset"] == 504  # max update_id (503) + 1, even though it wasn't a matching post


def test_fetch_handles_telegram_api_error(monkeypatch):
    import layers.layer12_caller_channels as mod
    monkeypatch.setattr(mod, "CONFIG", _cfg(bot_token="fake-token", channel_ids_raw="-1001"))
    monkeypatch.setattr(mod, "get_json", lambda url, params=None, timeout=None:
                         {"ok": True, "json": {"ok": False, "description": "Unauthorized"}})
    result = fetch_caller_channel_posts()
    assert result["ok"] is False
    assert "Unauthorized" in result["reason"]


def test_fetch_handles_http_failure(monkeypatch):
    import layers.layer12_caller_channels as mod
    monkeypatch.setattr(mod, "CONFIG", _cfg(bot_token="fake-token", channel_ids_raw="-1001"))
    monkeypatch.setattr(mod, "get_json", lambda url, params=None, timeout=None:
                         {"ok": False, "status_code": 502})
    result = fetch_caller_channel_posts()
    assert result["ok"] is False


# --- config.py helpers ---

def test_telegram_caller_channel_ids_parses_comma_separated():
    cfg = Config(telegram_caller_channel_ids_raw="-1001, -1002 ,not-a-number, -1003")
    assert cfg.telegram_caller_channel_ids() == [-1001, -1002, -1003]


def test_telegram_caller_channel_ids_empty_when_unset():
    cfg = Config(telegram_caller_channel_ids_raw=None)
    assert cfg.telegram_caller_channel_ids() == []


def test_layer12_ready_requires_both_token_and_channels():
    assert Config(telegram_caller_bot_token=None, telegram_caller_channel_ids_raw="-1001").layer12_ready() is False
    assert Config(telegram_caller_bot_token="tok", telegram_caller_channel_ids_raw=None).layer12_ready() is False
    assert Config(telegram_caller_bot_token="tok", telegram_caller_channel_ids_raw="-1001").layer12_ready() is True

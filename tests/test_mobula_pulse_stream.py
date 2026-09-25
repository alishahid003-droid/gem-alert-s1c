"""
Tests for the Layer 0b WebSocket speed upgrade (mobula_pulse_stream.py).
No real network -- the websocket connection itself is a fake async
context manager / async iterator, built here rather than depending on the
`websockets` package's own test doubles, so these tests exercise this
module's own dispatch/reconnect logic in isolation.
"""
import asyncio

import pytest

import mobula_pulse_stream as pls


def test_build_subscribe_message_shape():
    msg = pls.build_subscribe_message("fake-key", ["evm:56", "evm:8453"], max_updates_per_minute=45)
    assert msg["type"] == "pulse-v2"
    assert msg["authorization"] == "fake-key"
    assert msg["payload"]["model"] == "default"
    assert msg["payload"]["chainId"] == ["evm:56", "evm:8453"]
    assert msg["payload"]["maxUpdatesPerMinute"] == 45
    assert msg["payload"]["coalesce"] is True


def test_build_subscribe_message_defaults_chain_ids():
    msg = pls.build_subscribe_message("fake-key")
    assert msg["payload"]["chainId"] == pls.DEFAULT_CHAIN_IDS


def test_dispatch_new_token_calls_callback_with_mapped_chain_name():
    received = []
    msg = {"type": "new-token", "payload": {"viewName": "new", "token": {
        "address": "0xTokenAbc", "chainId": "evm:8453", "name": "Test Token",
    }}}
    pls._dispatch_message(msg, on_new_token=lambda token, chain: received.append((token, chain)), on_status=None)
    assert len(received) == 1
    token, chain = received[0]
    assert chain == "base"  # evm:8453 -> "base" via CHAIN_ID_TO_NAME
    assert token["address"] == "0xTokenAbc"


def test_dispatch_new_token_with_unmapped_chain_id_passes_raw_id_through():
    received = []
    msg = {"type": "new-token", "payload": {"token": {"address": "0xY", "chainId": "evm:999"}}}
    pls._dispatch_message(msg, on_new_token=lambda token, chain: received.append((token, chain)), on_status=None)
    assert received[0][1] == "evm:999"  # unknown chain id -- passed through raw, not dropped or crashed on


def test_dispatch_update_token_and_sync_are_not_scored():
    """The whole point of only scoring new-token events -- update-token and
    sync fire far more often than REST polling ever did and must not
    re-trigger scoring/alerting on every price tick."""
    received = []
    for msg_type in ("update-token", "sync", "remove-token"):
        pls._dispatch_message({"type": msg_type, "payload": {"token": {"address": "0xZ"}}},
                               on_new_token=lambda token, chain: received.append((token, chain)), on_status=None)
    assert received == []


def test_dispatch_init_message_reports_status():
    statuses = []
    msg = {"type": "init", "payload": {"viewName": "new", "tokens": [{"a": 1}, {"a": 2}]}}
    pls._dispatch_message(msg, on_new_token=lambda *a: None, on_status=lambda s: statuses.append(s))
    assert len(statuses) == 1
    assert "view=new" in statuses[0]
    assert "tokens=2" in statuses[0]


def test_dispatch_error_message_reports_status_not_exception():
    statuses = []
    msg = {"type": "error", "payload": "plan does not support this endpoint"}
    pls._dispatch_message(msg, on_new_token=lambda *a: None, on_status=lambda s: statuses.append(s))
    assert any("plan does not support" in s for s in statuses)


def test_dispatch_unknown_message_type_is_ignored_not_raised():
    # must not raise -- a protocol addition on Mobula's side should never crash the worker
    pls._dispatch_message({"type": "something-new-mobula-added"}, on_new_token=lambda *a: None, on_status=None)


class _FakeWebSocket:
    """Minimal stand-in for a `websockets` connection: async context manager
    + async iterator over a fixed list of raw JSON strings, then stops."""
    def __init__(self, messages, sent_log):
        self._messages = messages
        self._sent_log = sent_log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self._sent_log.append(data)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for m in self._messages:
            yield m


def test_stream_pulse_events_dispatches_new_token_and_sends_subscribe_first():
    import json

    sent_log = []
    new_tokens_seen = []
    statuses = []

    new_token_msg = json.dumps({"type": "new-token", "payload": {"token": {
        "address": "0xAbc123", "chainId": "evm:56",
    }}})

    def fake_connect(url):
        assert url == pls.WS_URL
        return _FakeWebSocket([new_token_msg], sent_log)

    asyncio.run(pls.stream_pulse_events(
        api_key="fake-key",
        on_new_token=lambda token, chain: new_tokens_seen.append((token, chain)),
        on_status=lambda s: statuses.append(s),
        chain_ids=["evm:56"],
        _connect_fn=fake_connect,
        _max_iterations=1,
    ))

    assert len(sent_log) == 1
    sent = json.loads(sent_log[0])
    assert sent["type"] == "pulse-v2" and sent["authorization"] == "fake-key"

    assert len(new_tokens_seen) == 1
    token, chain = new_tokens_seen[0]
    assert token["address"] == "0xAbc123"
    assert chain == "bsc"

    assert any("connecting" in s for s in statuses)
    assert any("subscribed" in s for s in statuses)


def test_stream_pulse_events_reports_disconnect_and_stops_after_max_iterations():
    statuses = []

    def fake_connect(url):
        raise ConnectionRefusedError("simulated: plan not entitled / connection dropped")

    asyncio.run(pls.stream_pulse_events(
        api_key="fake-key",
        on_new_token=lambda *a: None,
        on_status=lambda s: statuses.append(s),
        _connect_fn=fake_connect,
        _max_iterations=1,
    ))

    assert any("disconnected" in s for s in statuses)
    assert any("simulated: plan not entitled" in s for s in statuses)

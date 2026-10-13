"""PumpPortal metered trade feed: separate keyed socket, daily message cap, watch cap."""
import asyncio
import json

import early_stream
from early_stream import EarlyStream


class FakeWS:
    def __init__(self, msgs=()):
        self.msgs, self.sent = list(msgs), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        async def gen():
            for m in self.msgs:
                yield json.dumps(m)
        return gen()


def test_keyless_unchanged(monkeypatch):
    monkeypatch.delenv("PUMPPORTAL_API_KEY", raising=False)
    s = EarlyStream(lambda e: None)
    assert s.url == early_stream.WS_URL and s.trade_url is None


def test_key_goes_only_on_trade_socket(monkeypatch):
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "SECRET")
    s = EarlyStream(lambda e: None)
    assert "SECRET" not in s.url and s.trade_url.endswith("?api-key=SECRET")


def test_watch_cap(monkeypatch):
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "k")
    monkeypatch.setenv("PUMPPORTAL_MAX_WATCHED", "2")
    s = EarlyStream(lambda e: None)
    ws = FakeWS()
    s.trade_ws = ws
    asyncio.run(s.subscribe_trades(["a", "b", "c"]))
    assert s.watched == {"a", "b"} and ws.sent[0]["keys"] == ["a", "b"]


def test_daily_budget_stops_feed(monkeypatch):
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "k")
    monkeypatch.setenv("PUMPPORTAL_MAX_MSG_PER_DAY", "3")
    got = []
    msgs = [{"txType": "buy", "mint": "a"}] * 6
    s = EarlyStream(lambda e: got.append(e), connect=lambda url: FakeWS(msgs))
    s.watched.add("a")

    async def run():
        try:
            await asyncio.wait_for(s._run_trades(), 0.5)
        except asyncio.TimeoutError:
            pass
    asyncio.run(run())
    assert len(got) == 2 and s.budget_exhausted and not s.watched and s.spend_est_sol == 0.000003
    asyncio.run(s.subscribe_trades(["z"]))
    assert not s.watched            # no new subscriptions once the budget is spent

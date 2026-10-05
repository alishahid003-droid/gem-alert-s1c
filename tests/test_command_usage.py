import state


def _mem(monkeypatch):
    store = {}
    monkeypatch.setattr(state, "backend", lambda: "upstash")
    monkeypatch.setattr(state, "upstash_blocked", lambda: False)
    monkeypatch.setattr(state, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(state, "set_value", lambda k, v: store.__setitem__(k, v) or True)
    return store


def test_flush_adds_delta_once(monkeypatch):
    store = _mem(monkeypatch)
    monkeypatch.setattr(state, "COMMAND_COUNTER", {"n": 100, "since": 0})
    monkeypatch.setattr(state, "_CMD_FLUSHED", {"n": 0})
    state.flush_command_usage(now=1_700_000_000)
    state.flush_command_usage(now=1_700_000_000)   # nothing new -> no double count
    assert state.command_usage_today(1_700_000_000)["used"] == 100
    state.COMMAND_COUNTER["n"] = 130
    state.flush_command_usage(now=1_700_000_000)
    u = state.command_usage_today(1_700_000_000)
    assert u["used"] == 130 and u["budget"] == 16000 and u["pct"] == 1


def test_flush_noop_when_blocked(monkeypatch):
    store = _mem(monkeypatch)
    monkeypatch.setattr(state, "upstash_blocked", lambda: True)
    monkeypatch.setattr(state, "COMMAND_COUNTER", {"n": 50, "since": 0})
    monkeypatch.setattr(state, "_CMD_FLUSHED", {"n": 0})
    state.flush_command_usage()
    assert store == {}

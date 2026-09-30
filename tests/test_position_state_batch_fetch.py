"""Sept 30 2026 -- confirms the dashboard-hang fix: list_open_positions()/
list_closed_positions() must fetch every indexed position in ONE
state.get_values() call, not one state.get_value() call per key. With
dozens of positions accumulated from testing, the old per-key loop meant
dozens of sequential Upstash HTTP round trips, which was the real cause
of /api/data hanging for minutes (see state.py's _upstash_get_many_raw
docstring)."""
import executor.position_state as position_state
import state as state_module


def test_list_open_positions_uses_one_batched_call(monkeypatch):
    monkeypatch.setattr(state_module, "get_value",
                         lambda key: ["pos:a", "pos:b", "pos:c"] if key == "exec_position_index" else None)

    calls = []

    def fake_get_values(keys):
        calls.append(list(keys))
        return {
            "pos:a": {"status": "open", "token": "A"},
            "pos:b": {"status": "closed", "token": "B"},
            "pos:c": {"status": "open", "token": "C"},
        }

    monkeypatch.setattr(state_module, "get_values", fake_get_values)
    monkeypatch.setattr(position_state, "state", state_module)

    result = position_state.list_open_positions()

    assert len(calls) == 1, "must be exactly one batched call, not one per position key"
    assert calls[0] == ["pos:a", "pos:b", "pos:c"]
    tokens = sorted(p["token"] for p in result)
    assert tokens == ["A", "C"]


def test_list_closed_positions_uses_one_batched_call_and_sorts_newest_first(monkeypatch):
    monkeypatch.setattr(state_module, "get_value",
                         lambda key: ["pos:a", "pos:b"] if key == "exec_position_index" else None)

    calls = []

    def fake_get_values(keys):
        calls.append(list(keys))
        return {
            "pos:a": {"status": "closed", "token": "OLD", "closed_ts": 100},
            "pos:b": {"status": "closed", "token": "NEW", "closed_ts": 200},
        }

    monkeypatch.setattr(state_module, "get_values", fake_get_values)
    monkeypatch.setattr(position_state, "state", state_module)

    result = position_state.list_closed_positions(limit=10)

    assert len(calls) == 1, "must be exactly one batched call, not one per position key"
    assert [p["token"] for p in result] == ["NEW", "OLD"]


def test_list_open_positions_handles_empty_index_without_calling_get_values(monkeypatch):
    monkeypatch.setattr(state_module, "get_value", lambda key: None)

    calls = []
    monkeypatch.setattr(state_module, "get_values", lambda keys: calls.append(keys) or {})
    monkeypatch.setattr(position_state, "state", state_module)

    result = position_state.list_open_positions()
    assert result == []
    assert calls == [[]]

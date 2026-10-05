import state
from executor import targets, sprint


def _mem(monkeypatch):
    store = {}
    monkeypatch.setattr(state, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(state, "set_value", lambda k, v: store.__setitem__(k, v) or True)
    return store


def test_marathon_starts_and_tracks_path(monkeypatch):
    _mem(monkeypatch)
    monkeypatch.setenv("MARATHON_DAYS", "100")
    s0 = targets.marathon_status(equity=100.0, now=0)
    assert s0["started"] and s0["equity"] == 100.0
    half = targets.marathon_status(equity=100.0, now=50 * 86400)
    assert abs(half["path_now"] - 10_000.0) < 1          # sqrt(100 * 1e6) = 10k at the halfway mark
    assert half["on_pace"] is False
    ahead = targets.marathon_status(equity=20_000.0, now=50 * 86400)
    assert ahead["on_pace"] is True
    assert "behind" in targets.marathon_line(equity=100.0, now=50 * 86400)


def test_marathon_waits_without_equity(monkeypatch):
    _mem(monkeypatch)
    assert targets.marathon_status(equity=0.0, now=0)["started"] is False


def test_sprint_target_default_and_finish(monkeypatch):
    from executor import compound_scalper as cs
    assert sprint.target_usd() == 90000.0
    saved = {}
    monkeypatch.setattr(cs, "_save_pool", lambda p: saved.update(p))
    pool = {"open_position": None, "balance_usd": 90500.0, "banked_usd": 0.0}
    monkeypatch.setenv("SPRINT_MILESTONES", "")          # no mid locks -> only the final target applies
    out = sprint.apply_milestones(pool, now=1)
    assert out["tripped"] and out["tripped_kind"] == "target_reached"
    assert out["banked_usd"] == 90500.0 and out["balance_usd"] == 0.0

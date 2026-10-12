import local_runner, scheduler
from executor import entrypoint


def test_radar_thread_settings(monkeypatch):
    monkeypatch.delenv("LOCAL_RUNNER_RADAR", raising=False)
    monkeypatch.delenv("LOCAL_RADAR_MINUTES", raising=False)
    assert local_runner.radar_enabled() and local_runner.radar_interval_s() == 180
    monkeypatch.setenv("LOCAL_RUNNER_RADAR", "false")
    assert not local_runner.radar_enabled()
    monkeypatch.setenv("LOCAL_RADAR_MINUTES", "0")
    assert local_runner.radar_interval_s() == 60          # floor


def test_schedule_unchanged():
    assert [n for n, _, _ in local_runner.schedule()][:2] == ["poll-fast", "poll-slow"]
    assert "poll-radar" not in [n for n, _, _ in local_runner.schedule()]


def test_entry_ctx_has_sniper():
    ctx = scheduler._entry_ctx({"raw": {"liquidity_usd": 9000, "bundler_sniper_pct": 0.2}})
    assert ctx["sniper_pct"] == 0.2 and ctx["liquidity_usd"] == 9000


def test_run_poll_radar_survives_crash(monkeypatch, tmp_path, capsys):
    from layers import layer16_catalyst_radar as r
    import state
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    beats = []
    monkeypatch.setattr(state, "record_runner_heartbeat", lambda *a, **k: beats.append(a))
    monkeypatch.setattr(r, "run_cycle", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    scheduler.run_poll_radar()
    assert beats and beats[0][0] == "poll-radar"


def test_regime_hook_off_by_default(monkeypatch):
    from layers import layer16_catalyst_radar as r
    monkeypatch.delenv("REGIME_GATE_ENABLED", raising=False)
    assert r.regime_stake_multiplier() == 1.0
    assert "regime_stake_multiplier" in open(entrypoint.__file__, encoding="utf-8").read()

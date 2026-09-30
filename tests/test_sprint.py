"""5-day sprint mode (Oct 1 2026)."""
import time
import pytest

import state
from executor import sprint
from executor import compound_scalper as cs


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def test_sprint_defaults(monkeypatch):
    monkeypatch.setenv("SPRINT_MODE", "true")
    for k in ("COMPOUND_SEED_USD", "COMPOUND_RISK_PCT", "COMPOUND_SCALPER_ENABLED", "COMPOUND_SESSION_HOURS"):
        monkeypatch.delenv(k, raising=False)
    cfg = cs.CompoundScalperConfig()
    assert cfg.enabled and cfg.seed_usd == 100.0 and cfg.risk_pct == 0.9 and cfg.session_hours == 120.0
    # the backtested exits are NOT changed by the sprint
    assert cfg.take_profit_multiple == 1.5 and cfg.hard_stop_pct == 0.55 and cfg.trail_stop_pct == 0.35
    monkeypatch.setenv("COMPOUND_SEED_USD", "150")
    assert cs.CompoundScalperConfig().seed_usd == 150.0          # explicit env still wins
    monkeypatch.setenv("SPRINT_MODE", "false")
    monkeypatch.delenv("COMPOUND_SEED_USD")
    assert cs.CompoundScalperConfig().seed_usd == 27.5 and not cs.CompoundScalperConfig().enabled


def test_entry_confluence():
    assert not sprint.entry_ok({"coverage": 0.15, "change_m5": 3, "change_h1": 20})[0]     # low data
    assert not sprint.entry_ok({"coverage": None})[0]
    assert not sprint.entry_ok({"coverage": 0.8, "change_m5": -12, "change_h1": 20})[0]    # dumping now
    assert not sprint.entry_ok({"coverage": 0.8, "change_m5": 2, "change_h1": -4})[0]      # no momentum
    assert sprint.entry_ok({"coverage": 0.8, "change_m5": 2, "change_h1": 15})[0]
    assert sprint.entry_ok({}, moonshot=True)[0]


def test_auto_resume_after_cooldown_unless_pool_too_small():
    now = time.time()
    pool = {"tripped": True, "tripped_kind": "losing_streak", "tripped_ts": now - 60,
            "balance_usd": 70, "seed_usd": 100, "consecutive_losses": 3}
    assert sprint.maybe_resume(dict(pool), now=now)["tripped"]                    # still cooling off
    out = sprint.maybe_resume(dict(pool), now=now + 91 * 60)
    assert not out["tripped"] and out["consecutive_losses"] == 0
    small = dict(pool, balance_usd=20)
    assert sprint.maybe_resume(small, now=now + 91 * 60)["tripped"]              # below 25% of seed
    loss = dict(pool, tripped_kind="session_loss")
    assert sprint.maybe_resume(loss, now=now + 999 * 60)["tripped"]              # session loss: final


def test_sprint_auto_starts_pool(monkeypatch):
    monkeypatch.setenv("SPRINT_MODE", "true")
    monkeypatch.setattr(cs, "SCALPER_CONFIG", cs.CompoundScalperConfig())
    d = cs.entry_gate("solana", "TOK", "B", liquidity_usd=500_000)
    assert cs.status()["started"] and cs.status()["balance_usd"] == 100.0
    assert d.should_fire and d.position_usd == 90.0


def test_handler_applies_sprint_filter(monkeypatch):
    import executor.entrypoint as ep
    monkeypatch.setenv("SPRINT_MODE", "true")
    monkeypatch.setattr(cs, "SCALPER_CONFIG", cs.CompoundScalperConfig())
    opened = []
    monkeypatch.setattr(ep.compound_scalper, "open_scalp", lambda *a, **k: opened.append(a) or {"ok": True})
    r = ep.handle_compound_scalper_candidate("solana", "T1", "B", 500_000, liquidity_usd=500_000,
                                             entry_ctx={"coverage": 0.2, "change_m5": 1, "change_h1": 9})
    assert not r["fired"] and "real data" in r["reason"]
    r = ep.handle_compound_scalper_candidate("solana", "T2", "B", 500_000, liquidity_usd=500_000,
                                             entry_ctx={"coverage": 0.8, "change_m5": 1, "change_h1": 9})
    assert r["fired"] and opened


def test_status_line(monkeypatch):
    monkeypatch.setenv("SPRINT_MODE", "true")
    assert "armed" in sprint.status_line()
    monkeypatch.setenv("SPRINT_MODE", "false")
    assert sprint.status_line() is None

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
    assert cfg.enabled and cfg.seed_usd == 100.0 and cfg.risk_pct == 0.9 and cfg.session_hours == 168.0
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


def test_sprint_reserves_wallet(monkeypatch):
    import executor.entrypoint as ep
    monkeypatch.setenv("SPRINT_MODE", "true")
    monkeypatch.setenv("MOONSHOT_ENABLED", "true")
    monkeypatch.setattr(ep.triggers, "_concurrency_block", lambda: None)
    r = ep.handle_moonshot_candidate("solana", "MS", "B", 800_000, 400_000, 70, {"signals": 1})
    assert not r["fired"] and "reserved for the sprint" in r["reason"]
    monkeypatch.setenv("SPRINT_ONLY", "false")
    bought = []
    monkeypatch.setattr(ep, "_attempt_buy_and_record_fill", lambda *a, **k: bought.append(a) or {"ok": True})
    assert ep.handle_moonshot_candidate("solana", "MS2", "B", 800_000, 400_000, 70, {"signals": 1})["fired"]


def test_milestones_bank_profits(monkeypatch):
    monkeypatch.delenv("SPRINT_MILESTONES", raising=False)
    monkeypatch.delenv("SPRINT_TARGET_USD", raising=False)
    sent = []
    import executor.trade_ops as to
    monkeypatch.setattr(to, "_send", lambda t: sent.append(t))
    pool = cs._default_pool()
    pool.update(balance_usd=3_620.0, seed_usd=100.0, session_start_ts=time.time())
    pool = sprint.apply_milestones(pool)
    assert pool["balance_usd"] == 1_500 and pool["banked_usd"] == 2_120.0 and "3500.0" in pool["milestones_hit"]
    pool = sprint.apply_milestones(pool)                                # no double banking
    assert pool["banked_usd"] == 2_120.0
    pool["balance_usd"] = 21_000.0
    pool = sprint.apply_milestones(pool)
    assert pool["balance_usd"] == 5_000 and pool["banked_usd"] == 2_120.0 + 16_000.0
    pool["balance_usd"] = 73_000.0                                      # banked 18,120 + pool 73,000 >= 90k
    pool = sprint.apply_milestones(pool)
    assert pool["tripped"] and pool["tripped_kind"] == "target_reached" and pool["balance_usd"] == 0
    assert pool["banked_usd"] == 91_120.0
    assert len(sent) >= 3 and "MILESTONE" in sent[0]


def test_milestones_wait_for_flat_pool():
    pool = cs._default_pool()
    pool.update(balance_usd=5_000.0, open_position={"token": "X"})
    assert sprint.apply_milestones(pool)["balance_usd"] == 5_000.0


def test_modules_board_rows(monkeypatch):
    import dashboard
    monkeypatch.setenv("SPRINT_MODE", "true")
    rows = dashboard._modules_board(cs.status())
    names = [r["module"] for r in rows]
    assert any("Sprint pool" in n for n in names) and any("Momentum lane" in n for n in names)
    assert any("Moonshot" in n for n in names) and len(rows) == 5
    assert all(r["real"] is False for r in rows)        # EXECUTION_ENABLED off in tests

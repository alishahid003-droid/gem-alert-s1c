import csv
import json

import pytest

import state
from layers import layer19_smart_wallet_watch as c19

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def _write_wallets(tmp_path, rows):
    p = tmp_path / "smart_wallets.csv"
    with open(p, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["wallet", "runner_hits", "dud_hits", "seen", "edge"])
        for r in rows:
            wr.writerow(r)
    return str(p)


def test_load_smart_wallets_and_missing_file(tmp_path):
    path = _write_wallets(tmp_path, [("WalletA", 8, 1, 9, 6.2), ("WalletB", 5, 0, 5, 4.0)])
    w = c19.load_smart_wallets(path)
    assert w == {"WalletA": {"runner_hits": 8, "dud_hits": 1}, "WalletB": {"runner_hits": 5, "dud_hits": 0}}
    assert c19.load_smart_wallets(str(tmp_path / "nope.csv")) == {}


def test_recent_creates_age_window(tmp_path):
    p = tmp_path / "2026-10-20.jsonl"
    rows = [
        {"t": "create", "mint": "TOO_NEW", "ts": NOW - 60, "sym": "A"},            # 1 min: too new
        {"t": "create", "mint": "GOOD", "ts": NOW - 5 * 60, "sym": "FTR"},         # 5 min: in window
        {"t": "create", "mint": "TOO_OLD", "ts": NOW - 20 * 60, "sym": "B"},       # 20 min: too old
        {"t": "snap", "mint": "GOOD", "ts": NOW},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    out = c19.recent_creates(now=NOW, directory=str(tmp_path))
    assert [r["mint"] for r in out] == ["GOOD"]


def test_run_cycle_no_wallet_db_fails_closed(tmp_path):
    out = c19.run_cycle(send=lambda t: {"ok": True}, wallets={}, creates=[], now=NOW, log_dir=str(tmp_path))
    assert out["alerts"] == 0 and "smart_wallets.csv" in out["errors"][0]


def test_bot_wallets_excluded(monkeypatch, tmp_path):
    import bot_wallets
    monkeypatch.setattr(bot_wallets, "load", lambda: {"BotWallet"})
    wallets = {"BotWallet": {"runner_hits": 20, "dud_hits": 0}}
    sent = []
    out = c19.run_cycle(send=lambda t: sent.append(t) or {"ok": True}, wallets=wallets,
                        creates=[{"mint": "M", "sym": "X", "ts": NOW - 300}],
                        fetch_json=lambda *a, **k: {"ok": True, "json": [{"wallet": "BotWallet"}]},
                        dex_batch=lambda a: {}, now=NOW, log_dir=str(tmp_path))
    assert out["hits"] == 0 and out["alerts"] == 0 and sent == []


def test_end_to_end_hit_alerts_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLANATRACKER_API_KEY", "x")
    wallets = {"SmartW": {"runner_hits": 12, "dud_hits": 1}, "Other": {"runner_hits": 3, "dud_hits": 0}}
    sent = []
    pair = {"baseToken": {"address": "M"}, "priceUsd": "0.002"}
    out = c19.run_cycle(
        send=lambda t: sent.append(t) or {"ok": True},
        wallets=wallets,
        creates=[{"mint": "M", "sym": "FTR", "ts": NOW - 300}],
        fetch_json=lambda *a, **k: {"ok": True, "json": [{"wallet": "RandomBuyer"}, {"wallet": "SmartW"}]},
        dex_batch=lambda addrs: {m: pair for m in addrs if m == "M"},
        now=NOW, log_dir=str(tmp_path),
    )
    assert out["checked"] == 1 and out["hits"] == 1 and out["alerts"] == 1
    assert "SmartW" in sent[0] and "M" in sent[0]
    rows = [json.loads(l) for l in open(next(tmp_path.glob("*.jsonl")))]
    assert rows[0]["t"] == "alert" and rows[0]["hit_wallets"] == ["SmartW"]

    # re-running with the SAME create shouldn't re-check it (already in SEEN_KEY)
    out2 = c19.run_cycle(send=lambda t: sent.append(t) or {"ok": True}, wallets=wallets,
                         creates=[{"mint": "M", "sym": "FTR", "ts": NOW - 300}],
                         fetch_json=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not re-fetch")),
                         dex_batch=lambda a: {}, now=NOW + 60, log_dir=str(tmp_path))
    assert out2["candidates"] == 0

    # +70 minutes: 15m and 60m outcomes should be logged
    n = c19.update_outcomes(lambda ms: {"M": 0.004}, NOW + 4200, str(tmp_path))
    assert n == 2
    outs = [json.loads(l) for f in tmp_path.glob("*.jsonl") for l in open(f) if '"outcome"' in l]
    assert {o["horizon_min"] for o in outs} == {15, 60} and all(o["mult"] == 2.0 for o in outs)


def test_daily_credit_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("SOLANATRACKER_API_KEY", "x")
    wallets = {"SmartW": {"runner_hits": 9, "dud_hits": 0}}
    monkeypatch.setenv("SMART_WALLET_DAILY_CAP", "1")
    creates = [{"mint": "A", "sym": "A", "ts": NOW - 300}, {"mint": "B", "sym": "B", "ts": NOW - 300}]
    calls = []

    def fj(*a, **k):
        calls.append(1)
        return {"ok": True, "json": []}

    out = c19.run_cycle(send=lambda t: {"ok": True}, wallets=wallets, creates=creates,
                        fetch_json=fj, dex_batch=lambda a: {}, now=NOW, log_dir=str(tmp_path))
    assert out["checked"] == 1 and len(calls) == 1
    assert "credit cap" in out["errors"][-1]


def test_fetch_first_buyers_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("SOLANATRACKER_API_KEY", raising=False)
    assert c19.fetch_first_buyers("M", fetch_json=lambda *a, **k: (_ for _ in ()).throw(AssertionError)) == []

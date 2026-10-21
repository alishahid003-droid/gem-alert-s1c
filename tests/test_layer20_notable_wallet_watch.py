import json
import os

import state
from layers import layer20_notable_wallet_watch as l20


def _reset():
    state.set_value(l20.SEEN_KEY, [])
    state.set_value(l20.PENDING_KEY, [])


def test_load_registry_and_missing_file(tmp_path):
    assert l20.load_registry(str(tmp_path / "nope.json")) == {}
    p = tmp_path / "notable_wallets.json"
    p.write_text(json.dumps({"wallets": {"W1": {"name": "Alice", "confirmed": True}}}))
    reg = l20.load_registry(str(p))
    assert reg == {"W1": {"name": "Alice", "confirmed": True}}


def test_recent_creates_age_window(tmp_path):
    now = 1_000_000.0
    d = tmp_path / "early_log"
    d.mkdir()
    rows = [
        {"t": "create", "mint": "too_old", "ts": now - 5 * 3600},
        {"t": "create", "mint": "in_window", "ts": now - 60 * 60},
        {"t": "create", "mint": "too_new", "ts": now},
        {"t": "grad", "mint": "ignored", "ts": now - 60},
    ]
    (d / "2026-10-21.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    out = l20.recent_creates(max_age_min=240, min_age_min=10, now=now, directory=str(d))
    mints = {r["mint"] for r in out}
    assert mints == {"in_window"}


def test_run_cycle_empty_registry_fails_closed():
    _reset()
    res = l20.run_cycle(send=lambda m: {"ok": True}, registry={}, creates=[], now=1000.0)
    assert res["checked"] == 0
    assert any("notable_wallets.json is empty" in e for e in res["errors"])


def test_end_to_end_hit_alerts_and_logs(tmp_path):
    _reset()
    registry = {"OWNERW": {"name": "Kimchi", "confirmed": False}}
    creates = [{"mint": "MINT1", "sym": "FAKEKIM", "ts": 1000.0}]

    def fake_rpc(chain, method, params):
        if method == "getTokenLargestAccounts":
            return {"ok": True, "result": {"value": [{"address": "ACCT1", "uiAmount": 750_000_000.0}]}}
        if method == "getTokenSupply":
            return {"ok": True, "result": {"value": {"uiAmount": 1_000_000_000.0}}}
        if method == "getAccountInfo":
            return {"ok": True, "result": {"value": {"data": {"parsed": {"info": {"owner": "OWNERW"}}}}}}
        raise AssertionError(method)

    sent = []
    res = l20.run_cycle(
        send=lambda m: (sent.append(m), {"ok": True})[1],
        rpc=fake_rpc,
        dex_batch=lambda mints: {m: {"priceUsd": "0.01"} for m in mints},
        registry=registry,
        creates=creates,
        now=2000.0,
        log_dir=str(tmp_path),
    )
    assert res["hits"] == 1
    assert res["alerts"] == 1
    assert "FAKEKIM" in sent[0]
    assert "Kimchi" in sent[0]
    assert "UNVERIFIED" in sent[0]
    assert "75%" in sent[0]
    logged = (tmp_path / "2026-10-21.jsonl" if False else None)  # day derived from ts=2000.0 -> 1970, just check any file exists
    files = list(tmp_path.glob("*.jsonl"))
    assert files, "expected a shadow log file to be written"


def test_below_min_pct_does_not_alert(tmp_path):
    _reset()
    registry = {"OWNERW": {"name": "Kimchi", "confirmed": True}}
    creates = [{"mint": "MINT2", "sym": "SMALLHOLD", "ts": 1000.0}]

    def fake_rpc(chain, method, params):
        if method == "getTokenLargestAccounts":
            return {"ok": True, "result": {"value": [{"address": "ACCT2", "uiAmount": 10_000_000.0}]}}
        if method == "getTokenSupply":
            return {"ok": True, "result": {"value": {"uiAmount": 1_000_000_000.0}}}
        if method == "getAccountInfo":
            return {"ok": True, "result": {"value": {"data": {"parsed": {"info": {"owner": "OWNERW"}}}}}}
        raise AssertionError(method)

    os.environ["NOTABLE_WATCH_MIN_PCT"] = "0.10"
    try:
        res = l20.run_cycle(send=lambda m: {"ok": True}, rpc=fake_rpc,
                             dex_batch=lambda mints: {}, registry=registry, creates=creates,
                             now=2000.0, log_dir=str(tmp_path))
    finally:
        os.environ.pop("NOTABLE_WATCH_MIN_PCT", None)
    assert res["hits"] == 0
    assert res["alerts"] == 0


def test_fetch_largest_holder_owners_no_accounts_returns_empty():
    def fake_rpc(chain, method, params):
        return {"ok": False}
    assert l20.fetch_largest_holder_owners("MINT", rpc=fake_rpc) == []

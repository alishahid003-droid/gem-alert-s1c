import api_budget


def test_record_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(api_budget, "LOG_DIR", str(tmp_path))
    for _ in range(5):
        api_budget.record("https://api.geckoterminal.com/api/v2/x", 200)
    api_budget.record("https://api.madeonsol.com/y", 429)
    rep = {r["api"]: r for r in api_budget.report()}
    assert rep["GeckoTerminal"]["calls_today"] == 5 and rep["GeckoTerminal"]["per_minute_limit"] == 8
    assert rep["MadeOnSol"]["http_429"] == 1 and "429" in rep["MadeOnSol"]["flag"]


def test_gate_waits_when_at_limit_then_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(api_budget, "LOG_DIR", str(tmp_path))
    for _ in range(30):
        api_budget.record("https://api.geckoterminal.com/x", 200)
    slept = []
    monkeypatch.setattr(api_budget.time, "sleep", lambda s: slept.append(s))
    api_budget.gate("https://api.geckoterminal.com/y", max_wait=4.0)
    assert slept                       # at 30/30 -> it waited (bounded by max_wait)
    slept.clear()
    api_budget.gate("https://example.org/z")
    assert not slept                   # unknown host -> no wait

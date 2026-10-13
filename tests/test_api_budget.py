import api_budget


def test_record_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(api_budget, "LOG_DIR", str(tmp_path))
    for _ in range(5):
        api_budget.record("https://api.geckoterminal.com/api/v2/x", 200)
    api_budget.record("https://api.madeonsol.com/y", 429)
    rep = {r["api"]: r for r in api_budget.report()}
    assert rep["GeckoTerminal"]["calls_today"] == 5 and rep["GeckoTerminal"]["per_minute_limit"] == 30
    assert rep["MadeOnSol"]["http_429"] == 1 and "429" in rep["MadeOnSol"]["flag"]

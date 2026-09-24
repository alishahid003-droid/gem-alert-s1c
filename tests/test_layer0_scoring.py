import json
import os

from layers.layer0_scoring import (
    signals_from_madeonsol_risk, signals_from_mobula_pulse, score_token,
    PREGRAD_SOL_BANDS, GRADUATED_OR_OTHER_BANDS, score_mobula_pulse_items,
    score_solana_mint,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_madeonsol_risk_parses_and_scores():
    risk = _load("madeonsol_risk_sample.json")
    sig = signals_from_madeonsol_risk(risk, holders_json={"top10_share": 34.0}, bundle_json={"held_pct_of_supply": 6.0},
                                       is_pregraduation=True)
    assert sig.mint_authority_revoked is True
    assert sig.freeze_authority_revoked is True
    assert sig.top10_holder_pct == 0.34
    result = score_token(sig)
    assert 0 <= result.score <= 100
    # stricter pregrad bands should be harder to hit an A on than graduated bands
    assert PREGRAD_SOL_BANDS["A"] >= GRADUATED_OR_OTHER_BANDS["A"]


def test_mobula_pulse_parses_and_scores():
    pulse = _load("mobula_pulse_sample.json")
    item = pulse["data"][0]
    sig = signals_from_mobula_pulse(item)
    assert sig.top10_holder_pct == 0.225
    assert sig.mint_authority_revoked is True
    assert sig.liquidity_usd == 8400
    result = score_token(sig)
    assert result.liquidity_flag == "moderate"  # 8400 is between 1500 and 10000 (TYPICAL_POSITION_USD*3/*20)
    assert result.band in {"A", "B", "C", "D"}


def test_thin_liquidity_flag():
    from layers.layer0_scoring import RawSignals
    sig = RawSignals(liquidity_usd=200)
    result = score_token(sig)
    assert result.liquidity_flag == "thin"


def test_missing_signals_degrade_gracefully_not_crash():
    from layers.layer0_scoring import RawSignals
    sig = RawSignals()  # everything unknown
    result = score_token(sig)
    assert 0 <= result.score <= 100


def test_score_mobula_pulse_items_scores_every_item_from_one_response_no_refetch():
    pulse = _load("mobula_pulse_sample.json")
    items = pulse["data"]
    results = score_mobula_pulse_items("base", items)
    assert len(results) == 1
    assert results[0]["chain"] == "base"
    assert results[0]["address"] == items[0]["address"]
    assert results[0]["score"].band in {"A", "B", "C", "D"}
    assert results[0]["raw"] is items[0]


def test_score_solana_mint_uses_all_three_madeonsol_endpoints(monkeypatch):
    import layers.layer0_scoring as l0

    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        return {"ok": True, "status_code": 200, "url": url, "json": {"held_pct_of_supply": 5.0}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert len(calls) == 3
    assert "error" not in result
    assert result["address"] == "MINT123"
    assert result["score"].band in {"A", "B", "C", "D"}


def test_score_solana_mint_fails_closed_without_api_key(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", None)
    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" in result


def test_score_solana_mint_fails_closed_when_all_three_madeonsol_calls_fail(monkeypatch):
    # Real bug caught live Sept 24 2026: fetch_madeonsol_token_risk used to
    # always return ok=True even when every one of the 3 sub-calls failed
    # (e.g. MadeOnSol's free-key rate limit) -- 6 completely different real
    # coins all came back with an identical fabricated 49/100 "everything
    # unknown" score in the same backtest run, which is what exposed this.
    # A real rate-limit/network failure must surface as an error, not a
    # silently mis-scored token.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        return {"ok": False, "status_code": 429, "url": url,
                "json": {"error": "rate_limit_exceeded", "error_kind": "ip_rotation"}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" in result
    assert "429" in result["error"]
    assert "rate_limit_exceeded" in result["error"]


def test_score_solana_mint_degrades_gracefully_on_partial_madeonsol_failure(monkeypatch):
    # 1-2 of 3 calls failing should NOT throw away the 2 good ones -- only
    # a total (3/3) failure counts as a real fetch failure.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/risk"):
            return {"ok": False, "status_code": 429, "url": url, "json": {"error": "rate_limit_exceeded"}}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        return {"ok": True, "status_code": 200, "url": url, "json": {"held_pct_of_supply": 5.0}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" not in result
    assert result["score"].band in {"A", "B", "C", "D"}

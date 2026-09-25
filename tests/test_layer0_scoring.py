import json
import os
import time

from layers.layer0_scoring import (
    signals_from_madeonsol_risk, signals_from_mobula_pulse, score_token,
    PREGRAD_SOL_BANDS, GRADUATED_OR_OTHER_BANDS, score_mobula_pulse_items,
    score_solana_mint, compute_holder_growth_rate_per_hr, fetch_dexscreener_vol_liq,
    HOLDER_GROWTH_MIN_ELAPSED_SECONDS, fetch_goplus_security,
    parse_goplus_solana_security,
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


def test_score_mobula_pulse_items_scores_every_item_from_one_response_no_refetch(monkeypatch):
    import layers.layer0_scoring as l0

    # Mobula's real security schema has no blacklist/honeypot equivalent
    # (see signals_from_mobula_pulse docstring, bug #5 correction), so
    # freeze_authority_revoked is always missing and this now always tries
    # the GoPlus fallback -- stub it out so this stays a hermetic unit test.
    def fake_get_json(url, headers=None, params=None, timeout=20):
        assert "gopluslabs" in url
        return {"ok": True, "status_code": 200, "url": url,
                "json": {"result": {"0xabc0000000000000000000000000000000000001": {"is_blacklisted": "0", "is_honeypot": "0"}}}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)

    pulse = _load("mobula_pulse_sample.json")
    items = pulse["data"]
    results = score_mobula_pulse_items("base", items)
    assert len(results) == 1
    assert results[0]["chain"] == "base"
    assert results[0]["address"] == items[0]["address"]
    assert results[0]["score"].band in {"A", "B", "C", "D"}
    assert results[0]["raw"] is items[0]


def test_score_solana_mint_uses_all_three_madeonsol_endpoints_plus_dexscreener(monkeypatch):
    # Sept 25 2026: score_solana_mint now makes a 4th call to DexScreener
    # for real vol/liq data (see fetch_dexscreener_vol_liq) -- MadeOnSol's
    # own 3 endpoints carry no volume/liquidity figure at all. Renamed from
    # "...all_three_madeonsol_endpoints" since there are now 4 calls total,
    # 3 MadeOnSol + 1 DexScreener.
    import layers.layer0_scoring as l0

    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"held_pct_of_supply": 5.0}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": [
                {"volume": {"h24": 50000.0}, "liquidity": {"usd": 20000.0}},
            ]}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert len(calls) == 4
    assert any("dexscreener" in c for c in calls)
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


def test_fetch_mobula_pulse_sends_bearer_prefixed_auth_header(monkeypatch):
    # Real bug fixed Sept 24 2026: this was sending a bare `Authorization:
    # <key>` header with no "Bearer " prefix, while every other Mobula call
    # in this codebase (layer10_insider_cluster.py, layer6_exit_realizable.py,
    # wallet_balance.py) correctly uses "Bearer <key>" -- almost certainly
    # the real reason Layer 0b's BSC/Base Pulse scoring has been silently
    # failing (401) in production, caught live via Ali's named-coin backtest.
    import layers.layer0_scoring as l0

    captured = {}

    def fake_get_json(url, headers=None, params=None, timeout=20):
        captured["headers"] = headers
        return {"ok": True, "status_code": 200, "url": url, "json": {"data": []}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "mobula_api_key", "mob_test_key")

    l0.fetch_mobula_pulse("evm:56")  # BSC in Mobula's real evm:<chainId> format (bug #4, fixed Sept 24 2026)
    assert captured["headers"]["Authorization"] == "Bearer mob_test_key"


def test_compute_holder_growth_rate_per_hr_needs_at_least_two_points():
    assert compute_holder_growth_rate_per_hr([]) is None
    assert compute_holder_growth_rate_per_hr([(time.time(), 100)]) is None


def test_compute_holder_growth_rate_per_hr_rejects_too_short_a_window():
    now = time.time()
    # 60s apart -- well under HOLDER_GROWTH_MIN_ELAPSED_SECONDS (5 min)
    history = [(now - 60, 100), (now, 105)]
    assert compute_holder_growth_rate_per_hr(history) is None


def test_compute_holder_growth_rate_per_hr_computes_real_rate():
    now = time.time()
    # exactly 1hr apart, +120 holders -> 120/hr
    history = [(now - 3600, 100), (now, 220)]
    rate = compute_holder_growth_rate_per_hr(history)
    assert rate is not None
    assert abs(rate - 120.0) < 0.01


def test_compute_holder_growth_rate_per_hr_sorts_unordered_input():
    now = time.time()
    # passed newest-first -- function must sort by ts itself, not assume order
    history = [(now, 220), (now - 3600, 100)]
    rate = compute_holder_growth_rate_per_hr(history)
    assert rate is not None
    assert abs(rate - 120.0) < 0.01


def test_fetch_dexscreener_vol_liq_picks_highest_liquidity_pair(monkeypatch):
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        assert "dexscreener" in url
        return {"ok": True, "status_code": 200, "url": url, "json": [
            {"volume": {"h24": 1000.0}, "liquidity": {"usd": 500.0}},
            {"volume": {"h24": 50000.0}, "liquidity": {"usd": 20000.0}},  # deepest pool
        ]}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    result = fetch_dexscreener_vol_liq("solana", "MINT123")
    assert result["ok"] is True
    assert result["liquidity_usd"] == 20000.0
    assert result["volume_24h"] == 50000.0


def test_fetch_dexscreener_vol_liq_fails_gracefully_on_no_pairs(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "get_json", lambda *a, **kw: {"ok": True, "status_code": 200, "json": []})
    result = fetch_dexscreener_vol_liq("solana", "MINT123")
    assert result["ok"] is False


def test_fetch_dexscreener_vol_liq_fails_gracefully_on_unsupported_chain():
    result = fetch_dexscreener_vol_liq("not_a_real_chain", "MINT123")
    assert result["ok"] is False


def test_fetch_dexscreener_vol_liq_fails_gracefully_on_missing_fields(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "get_json", lambda *a, **kw: {
        "ok": True, "status_code": 200, "json": [{"volume": {}, "liquidity": {"usd": 100.0}}],
    })
    result = fetch_dexscreener_vol_liq("solana", "MINT123")
    assert result["ok"] is False


def test_score_mobula_pulse_items_records_holder_point_and_wires_growth_rate(tmp_path, monkeypatch):
    # Real wiring added Sept 25 2026: score_mobula_pulse_items should record
    # each item's real holdersCount into state.py's history and pass a
    # computed growth rate through to signals_from_mobula_pulse once enough
    # history has accumulated -- verified end-to-end across two simulated
    # cycles 1hr apart, using the local_json state backend so this stays
    # hermetic (same pattern as tests/test_state.py's _reset_local_state).
    import layers.layer0_scoring as l0
    import state as state_module

    state_file = tmp_path / "test_state.json"
    monkeypatch.setattr(state_module, "LOCAL_STATE_FILE", str(state_file))
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(state_module.CONFIG, "upstash_redis_rest_token", None)

    def fake_get_json(url, headers=None, params=None, timeout=20):
        assert "gopluslabs" in url
        return {"ok": True, "status_code": 200, "url": url,
                "json": {"result": {"0xabc0000000000000000000000000000000000001": {"is_blacklisted": "0", "is_honeypot": "0"}}}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)

    captured_signals = []
    real_signals_from_mobula_pulse = l0.signals_from_mobula_pulse

    def spy_signals_from_mobula_pulse(pulse_item, chain=None, holder_growth_rate_per_hr=None):
        captured_signals.append(holder_growth_rate_per_hr)
        return real_signals_from_mobula_pulse(pulse_item, chain, holder_growth_rate_per_hr=holder_growth_rate_per_hr)

    monkeypatch.setattr(l0, "signals_from_mobula_pulse", spy_signals_from_mobula_pulse)

    pulse = _load("mobula_pulse_sample.json")
    items = pulse["data"]
    address = items[0]["address"]

    now = time.time()
    monkeypatch.setattr(state_module.time, "time", lambda: now - 3600)
    l0.score_mobula_pulse_items("base", items)
    # first cycle: only 1 history point exists yet -> None, not enough data
    assert captured_signals[-1] is None

    monkeypatch.setattr(state_module.time, "time", lambda: now)
    items2 = [dict(items[0], holdersCount=items[0]["holdersCount"] + 60)]  # +60 holders over 1hr
    l0.score_mobula_pulse_items("base", items2)
    # second cycle, 1hr later: enough history -> real computed rate
    assert captured_signals[-1] is not None
    assert abs(captured_signals[-1] - 60.0) < 0.01

    history = state_module.get_holder_history(address)
    assert len(history) == 2


def test_parse_goplus_solana_security_mint_and_freeze_authority():
    # Real shape confirmed live Sept 25 2026 against USDC's Solana mint
    # (EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v) -- status "1" means the
    # authority is still live (NOT revoked), "0" means revoked.
    data = {
        "mintable": {"authority": [{"address": "X", "malicious_address": 0}], "status": "1"},
        "freezable": {"authority": [], "status": "0"},
    }
    parsed = parse_goplus_solana_security(data)
    assert parsed["mint_authority_revoked"] is False  # status "1" -- still mintable
    assert parsed["freeze_authority_revoked"] is True  # status "0" -- freeze authority gone


def test_parse_goplus_solana_security_lp_locked_uses_balance_not_percent():
    # The live sample's lp_holders[].percent field was NOT a normal 0-100
    # percentage for this token (values were in the hundreds of millions) --
    # deliberately computed from "balance" instead, see module comment.
    data = {
        "lp_holders": [
            {"balance": "800.0", "is_locked": 1, "percent": "999999999"},
            {"balance": "200.0", "is_locked": 0, "percent": "111111111"},
        ],
    }
    parsed = parse_goplus_solana_security(data)
    assert parsed["lp_locked"] is True  # 800/1000 = 80% locked by balance, >= 0.5 threshold


def test_parse_goplus_solana_security_lp_mostly_unlocked():
    data = {
        "lp_holders": [
            {"balance": "100.0", "is_locked": 1},
            {"balance": "900.0", "is_locked": 0},
        ],
    }
    parsed = parse_goplus_solana_security(data)
    assert parsed["lp_locked"] is False  # 100/1000 = 10% locked, below threshold


def test_parse_goplus_solana_security_missing_or_malformed_fields_stay_none():
    assert parse_goplus_solana_security({}) == {
        "mint_authority_revoked": None, "freeze_authority_revoked": None, "lp_locked": None,
    }
    # malformed balance -- must not crash, must stay None rather than guess
    parsed = parse_goplus_solana_security({"lp_holders": [{"balance": "not_a_number", "is_locked": 1}]})
    assert parsed["lp_locked"] is None


def test_fetch_goplus_security_solana_uses_correct_endpoint_and_case_sensitive_key(monkeypatch):
    import layers.layer0_scoring as l0

    mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    captured = {}

    def fake_get_json(url, headers=None, params=None, timeout=20):
        captured["url"] = url
        captured["params"] = params
        # Real endpoint has no /{chain_id} segment for Solana, unlike the EVM path
        assert url.endswith("/solana/token_security")
        return {"ok": True, "status_code": 200, "url": url,
                "json": {"result": {mint: {"mintable": {"status": "0"}, "freezable": {"status": "0"}}}}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    result = fetch_goplus_security("solana", mint)
    assert result["ok"] is True
    assert result["data"]["mintable"]["status"] == "0"
    assert captured["params"] == {"contract_addresses": mint}


def test_score_solana_mint_falls_back_to_goplus_when_madeonsol_risk_is_tier_gated(monkeypatch):
    # Real production scenario, Sept 25 2026: MadeOnSol's /risk 403s for
    # Ali's BASIC-tier key ("tier_required") while /holders and /bundle
    # succeed -- this is exactly when the GoPlus fallback should kick in.
    import layers.layer0_scoring as l0

    mint = "MINT123"
    captured_kwargs = {}
    real_signals_from_madeonsol_risk = l0.signals_from_madeonsol_risk

    def spy_signals(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_signals_from_madeonsol_risk(*args, **kwargs)

    monkeypatch.setattr(l0, "signals_from_madeonsol_risk", spy_signals)

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/risk"):
            return {"ok": False, "status_code": 403, "url": url,
                     "json": {"error": "tier_required", "message": "requires PRO tier"}}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"held_pct_of_supply": 5.0}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        if url.endswith("/solana/token_security"):
            return {"ok": True, "status_code": 200, "url": url, "json": {
                "result": {mint: {"mintable": {"status": "0"}, "freezable": {"status": "0"},
                                   "lp_holders": [{"balance": "900", "is_locked": 1},
                                                  {"balance": "100", "is_locked": 0}]}}}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint(mint, "solana", is_pregraduation=False)
    assert "error" not in result
    assert captured_kwargs["goplus_mint_authority_revoked"] is True
    assert captured_kwargs["goplus_freeze_authority_revoked"] is True
    assert captured_kwargs["goplus_lp_locked"] is True


def test_score_solana_mint_does_not_call_goplus_when_madeonsol_risk_succeeds(monkeypatch):
    # Regression guard: GoPlus is a fallback for a real gap, never a
    # second-guess of real MadeOnSol data -- must not even be called when
    # /risk succeeds.
    import layers.layer0_scoring as l0

    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"held_pct_of_supply": 5.0}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" not in result
    assert not any("gopluslabs" in c or "solana/token_security" in c for c in calls)

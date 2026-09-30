import json
import os
import time

import pytest

import state
from layers.layer0_scoring import (
    signals_from_madeonsol_risk, signals_from_mobula_pulse, score_token,
    PREGRAD_SOL_BANDS, GRADUATED_OR_OTHER_BANDS, score_mobula_pulse_items,
    score_solana_mint, compute_holder_growth_rate_per_hr, fetch_dexscreener_vol_liq,
    HOLDER_GROWTH_MIN_ELAPSED_SECONDS, fetch_goplus_security,
    parse_goplus_solana_security, compute_activity_decay_ratio,
    ACTIVITY_DECAY_MIN_H24_TXNS,
)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    # Added Sept 25 2026: fetch_madeonsol_token_risk now checks/records real
    # MadeOnSol call-budget state (see state.py's madeonsol_budget_remaining
    # docstring) -- without this, every test run here would write into the
    # real on-device state file and falsely deplete the live system's daily
    # budget tracker. Same isolation pattern as tests/test_madeonsol_gating.py.
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setattr(state.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(state.CONFIG, "upstash_redis_rest_token", None)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_bundler_sniper_pct_reads_real_nested_bundle_shape():
    # Regression test for the real bug caught live Sept 27 2026 via Ali's
    # rug-score diagnostic: MadeOnSol's actual /bundle response nests the
    # headline field under a "bundle" object and returns it as a 0-1
    # fraction already -- confirmed live against a real labeled rug
    # (ELONCOIN): {"bundle": {"held_pct_of_supply": 0.1027, ...},
    # "wallets": [...]}. The old code read bundle_json.get(
    # "held_pct_of_supply", 0) at the TOP level (always missing -> silent
    # 0 = "verified zero bundler risk", full 20/20 points) and then divided
    # by 100 again (which would have shrunk a real 10.27% to 0.1027% even
    # after fixing the path). This locks in the real, confirmed shape so a
    # future refactor can't silently reintroduce either half of the bug.
    risk = _load("madeonsol_risk_sample.json")
    real_bundle_response = {
        "mint": "2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP",
        "bundle": {
            "wallet_count": 10,
            "bundle_kind": "same_slot",
            "held_ratio": 0.3376,
            "held_pct_of_supply": 0.1027,
            "fully_exited": False,
            "buy_volume": 304256531.57748,
            "tokens_held": 102732035.865665,
        },
        "wallets": [],
    }
    sig = signals_from_madeonsol_risk(risk, holders_json={}, bundle_json=real_bundle_response,
                                       is_pregraduation=True)
    assert sig.bundler_sniper_pct == 0.1027


def test_bundler_sniper_pct_none_when_bundle_json_empty():
    # An empty/missing bundle response must stay None (-> scored
    # low-neutral by score_token) rather than silently reading as a false
    # "verified zero" the way the pre-fix top-level lookup did.
    risk = _load("madeonsol_risk_sample.json")
    sig = signals_from_madeonsol_risk(risk, holders_json={}, bundle_json={},
                                       is_pregraduation=True)
    assert sig.bundler_sniper_pct is None


def test_band_a_multi_signal_gate_holds_a_with_2_confirmed_favorable_signals(monkeypatch):
    # Sept 29 2026 (Ali's "80%+ precision" push, checklist item #2): band A
    # requires >=2 of the 4 strongest signals to be independently CONFIRMED
    # favorable, not just a high blended average. This token has all 4
    # confirmed favorable -- must hold band A.
    from layers.layer0_scoring import RawSignals
    sig = RawSignals(
        top10_holder_pct=0.1,
        lp_locked_or_curve_healthy=True,
        mint_authority_revoked=True,
        freeze_authority_revoked=True,
        vol_to_liq_ratio=5.0,
        holder_growth_rate_per_hr=30.0,
        bundler_sniper_pct=0.05,
        price_drawdown_from_peak_pct=-2.0,
        liquidity_usd=50000.0,
        is_pregraduation_solana=True,
    )
    result = score_token(sig)
    assert result.band == "A"
    assert not any("GATE:" in r for r in result.reasons)


def test_band_a_multi_signal_gate_demotes_to_b_when_signals_are_mostly_unknown(monkeypatch):
    # Real failure mode this closes: several UNKNOWN signals each
    # contribute their neutral-default partial credit (see every
    # "...-- scored ...-neutral" branch in score_token) and the blended
    # average alone can still clear the band-A numeric threshold, with
    # ZERO signals actually confirmed favorable. Must demote to B.
    # Constructed so the BLENDED score clears band A (>=80) mostly off
    # unknown-neutral credit plus real-but-partial signals, while only 1 of
    # the 4 strict gate signals is independently confirmed: freeze
    # authority is confirmed revoked but mint authority is unknown, so the
    # blended score still gives full auth credit (1 known bit, favorable)
    # while the gate correctly refuses to count it (needs BOTH confirmed).
    from layers.layer0_scoring import RawSignals
    sig = RawSignals(
        top10_holder_pct=0.0,
        lp_locked_or_curve_healthy=None,
        mint_authority_revoked=None,
        freeze_authority_revoked=True,
        vol_to_liq_ratio=5.0,
        holder_growth_rate_per_hr=30.0,
        bundler_sniper_pct=None,
        price_drawdown_from_peak_pct=-2.0,
        liquidity_usd=50000.0,
        is_pregraduation_solana=True,
    )
    result = score_token(sig)
    assert result.score >= 80  # confirms this really did clear band A structurally
    assert result.band == "B"
    assert any("GATE: only 1/4" in r for r in result.reasons)


def test_band_a_multi_signal_gate_does_not_touch_band_b_or_below(monkeypatch):
    # The gate only ever demotes FROM band A -- it must never fire (or
    # matter) for a token that never reached A in the first place.
    from layers.layer0_scoring import RawSignals
    sig = RawSignals(
        top10_holder_pct=0.5,
        lp_locked_or_curve_healthy=False,
        mint_authority_revoked=False,
        freeze_authority_revoked=False,
        vol_to_liq_ratio=0.1,
        holder_growth_rate_per_hr=2.0,
        bundler_sniper_pct=0.4,
        price_drawdown_from_peak_pct=-30.0,
        liquidity_usd=5000.0,
        is_pregraduation_solana=True,
    )
    result = score_token(sig)
    assert result.band != "A"
    assert not any("GATE:" in r for r in result.reasons)


def test_launch_shape_scores_low_on_severe_drawdown_high_near_peak():
    # New signal wired live Sept 27 2026 -- Birdeye real launch-window
    # price shape. Confirms the actual scoring curve: a token still near
    # its peak scores full marks, a token that collapsed (the real
    # signature every one of the 10 real labeled rugs/pump_dumps showed
    # in Ali's diagnostic) scores near-zero on this component specifically.
    risk = _load("madeonsol_risk_sample.json")
    sig_near_peak = signals_from_madeonsol_risk(risk, holders_json={}, bundle_json={},
                                                 is_pregraduation=True,
                                                 price_drawdown_from_peak_pct=-5.0)
    sig_collapsed = signals_from_madeonsol_risk(risk, holders_json={}, bundle_json={},
                                                 is_pregraduation=True,
                                                 price_drawdown_from_peak_pct=-95.0)
    sig_unknown = signals_from_madeonsol_risk(risk, holders_json={}, bundle_json={},
                                               is_pregraduation=True)
    result_near_peak = score_token(sig_near_peak)
    result_collapsed = score_token(sig_collapsed)
    result_unknown = score_token(sig_unknown)
    assert "launch-window drawdown -5.0% from peak" in result_near_peak.reasons
    assert "launch-window drawdown -95.0% from peak" in result_collapsed.reasons
    assert "launch-window price shape unknown -- scored low-neutral" in result_unknown.reasons
    # near-peak must score strictly higher than collapsed on this component
    # -- verified via the actual total score since both share every other
    # input identically.
    assert result_near_peak.score > result_collapsed.score
    # unknown (low-neutral, 40% credit) sits between the two extremes.
    assert result_collapsed.score < result_unknown.score < result_near_peak.score


def test_score_solana_mint_wires_real_birdeye_launch_shape(monkeypatch):
    # Confirms the live (not just backtest) path actually calls
    # fetch_birdeye_ohlcv using the token's real DexScreener launch date,
    # and that the resulting drawdown reaches score_token via
    # signals_from_madeonsol_risk. fetch_birdeye_ohlcv itself is patched
    # directly (rather than the underlying get_json it uses internally,
    # which lives in a different module's namespace) -- its own real
    # HTTP behavior is already covered by tests/test_layer0d_point_in_time.py.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": [
                {"volume": {"h24": 50000.0}, "liquidity": {"usd": 20000.0},
                 "pairCreatedAt": int((time.time() - 3600) * 1000)},
            ]}
        if "gopluslabs" in url:
            return {"ok": False, "status_code": 500, "url": url, "json": {}}
        raise AssertionError(f"unexpected URL: {url}")

    captured = {}

    def fake_fetch_birdeye_ohlcv(chain, address, time_from, time_to, interval="1H"):
        captured["called_with"] = (chain, address, time_from, time_to, interval)
        return {"ok": True, "candles": [{"o": 1.0, "h": 3.0, "l": 0.5, "c": 0.6, "v": 1000}]}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_birdeye_ohlcv", fake_fetch_birdeye_ohlcv)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" not in result
    assert captured.get("called_with") is not None
    assert captured["called_with"][0] == "solana"
    assert captured["called_with"][1] == "MINT123"
    # real drawdown from the fake candle: (0.6-3.0)/3.0*100 = -80.0
    assert result["score"].reasons and any("drawdown -80.0%" in r for r in result["score"].reasons)


def test_score_solana_mint_never_calls_birdeye_for_robinhood_chain(monkeypatch):
    # Birdeye has no Robinhood Chain mapping -- must not even attempt the
    # call for that chain, same convention as GoPlus's Solana-only gate.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/rhc/tokens/MINT123/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/rhc/tokens/MINT123/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {}}
        if url.endswith("/rhc/tokens/MINT123/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": [
                {"volume": {"h24": 50000.0}, "liquidity": {"usd": 20000.0},
                 "pairCreatedAt": int((time.time() - 3600) * 1000)},
            ]}
        if "gopluslabs" in url:
            return {"ok": False, "status_code": 500, "url": url, "json": {}}
        raise AssertionError(f"unexpected URL: {url}")

    def fake_fetch_birdeye_ohlcv(*args, **kwargs):
        raise AssertionError("fetch_birdeye_ohlcv must not be called for robinhood_chain")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_birdeye_ohlcv", fake_fetch_birdeye_ohlcv)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "robinhood_chain", is_pregraduation=True)
    assert "error" not in result


def test_madeonsol_risk_parses_and_scores():
    risk = _load("madeonsol_risk_sample.json")
    sig = signals_from_madeonsol_risk(risk, holders_json={"top10_share": 34.0}, bundle_json={"bundle": {"held_pct_of_supply": 0.06}},
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
            return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": [
                {"volume": {"h24": 50000.0}, "liquidity": {"usd": 20000.0}},
            ]}
        if "gopluslabs" in url:
            return {"ok": False, "status_code": 500, "url": url, "json": {}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = score_solana_mint("MINT123", "solana", is_pregraduation=True)
    # 5 calls total as of Sept 29 2026: 1 free GoPlus pre-filter (runs
    # first) + 3 MadeOnSol (risk/holders/bundle) + 1 DexScreener.
    assert len(calls) == 5
    assert any("dexscreener" in c for c in calls)
    assert any("gopluslabs" in c for c in calls)
    assert "error" not in result
    assert result["address"] == "MINT123"
    assert result["score"].band in {"A", "B", "C", "D"}


def test_score_solana_mint_prefilter_rejects_confirmed_rug_for_free_no_madeonsol_call(monkeypatch):
    # Real fix, Sept 29 2026 (Ali's "80%+ precision, smart play" push): the
    # single strongest measured rug signal (arXiv 2603.24625) -- freeze
    # authority NOT renounced, or LP NOT locked -- is checked for free via
    # GoPlus BEFORE any MadeOnSol call is spent. A confirmed-bad token must
    # be rejected here without ever touching /risk, /holders, or /bundle.
    import layers.layer0_scoring as l0

    mint = "RUGMINT"
    calls = []

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        if url.endswith("/solana/token_security"):
            return {"ok": True, "status_code": 200, "url": url, "json": {
                "result": {mint: {"mintable": {"status": "0"}, "freezable": {"status": "1"},
                                   "lp_holders": [{"balance": "1000", "is_locked": 0}]}}}}
        raise AssertionError(f"unexpected URL (MadeOnSol/DexScreener should never be called): {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint(mint, "solana", is_pregraduation=True)
    assert result.get("pre_filter_rejected") is True
    assert "error" in result
    assert calls == ["https://api.gopluslabs.io/api/v1/solana/token_security"]


def test_score_solana_mint_prefilter_does_not_reject_on_unknown_goplus_data(monkeypatch):
    # Conservative-by-design: unknown/missing GoPlus fields (None) must
    # never be treated as a red flag -- same "stay unknown, don't guess"
    # philosophy as the rest of this module. Only an explicit False
    # (confirmed bad) rejects. Falls through to the normal MadeOnSol flow.
    import layers.layer0_scoring as l0

    mint = "MINT123"

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/solana/token_security"):
            return {"ok": True, "status_code": 200, "url": url, "json": {
                "result": {mint: {}}}}  # no mintable/freezable/lp_holders at all -- everything None
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint(mint, "solana", is_pregraduation=True)
    assert result.get("pre_filter_rejected") is not True
    assert "error" not in result


def test_score_solana_mint_prefilter_falls_through_on_goplus_failure(monkeypatch):
    # Never a hard dependency -- if GoPlus itself fails/times out, scoring
    # must proceed to the normal MadeOnSol flow exactly as before this
    # pre-filter existed, not error out or skip the token.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/solana/token_security"):
            return {"ok": False, "status_code": 500, "url": url, "json": {}}
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" not in result
    assert result["score"].band in {"A", "B", "C", "D"}


def test_score_solana_mint_prefilter_skips_goplus_without_api_key(monkeypatch):
    # Pre-filter only runs when a real MadeOnSol call would actually be
    # spent (madeonsol_api_key configured) -- otherwise fetch_madeonsol_
    # token_risk's own "not configured" fail-fast still fires first, no
    # network call attempted at all.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        raise AssertionError(f"no network call should happen without an API key: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", None)

    result = l0.score_solana_mint("MINT123", "solana", is_pregraduation=True)
    assert "error" in result


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
        return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
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


def test_compute_holder_growth_rate_per_hr_prefers_recent_window_over_stale_history():
    # Real gap fixed Sept 30 2026 (checklist: "minute-level, not hourly").
    # Token was flat for its first ~100 min, then added holders fast in the
    # last ~10 min. Old behavior (oldest-vs-newest only) would dilute this
    # down to a much smaller number over the whole ~110 min span. New
    # behavior should report close to the REAL recent rate instead.
    now = time.time()
    history = [
        (now - 110 * 60, 100),  # 110 min ago: 100 holders (flat start)
        (now - 100 * 60, 101),  # 100 min ago: basically flat
        (now - 10 * 60, 110),   # 10 min ago: still flat-ish
        (now, 190),              # now: +80 holders in the last 10 min
    ]
    rate = compute_holder_growth_rate_per_hr(history)
    assert rate is not None
    # Recent window (last 20 min) = (now-10min, 110) -> (now, 190):
    # +80 holders / (10/60)hr = 480/hr -- NOT the old diluted
    # (190-100)/(110/60) = ~49/hr full-history number.
    assert abs(rate - 480.0) < 0.01


def test_compute_holder_growth_rate_per_hr_falls_back_when_recent_window_too_thin():
    # Only one point falls inside the recent window (itself) -- not enough
    # to compute a recent-window rate, so it must fall back to the old
    # oldest-vs-newest full-history behavior rather than returning None.
    now = time.time()
    history = [(now - 3600, 100), (now, 220)]  # only 2 points, 1hr apart
    rate = compute_holder_growth_rate_per_hr(history)
    assert rate is not None
    assert abs(rate - 120.0) < 0.01


def test_compute_holder_growth_rate_per_hr_recent_window_still_respects_min_elapsed():
    # Two points inside the recent window but only 60s apart -- must still
    # refuse to compute a rate from that (would massively amplify noise),
    # and there's no older point to fall back to either, so this must
    # stay None, not silently return some other number.
    now = time.time()
    history = [(now - 60, 100), (now, 105)]
    assert compute_holder_growth_rate_per_hr(history) is None


# ---------------------------------------------------------------------------
# Activity-decay ratio (pump-dump detection) -- added Sept 30 2026,
# checklist item C.
# ---------------------------------------------------------------------------

def test_compute_activity_decay_ratio_needs_both_counts():
    assert compute_activity_decay_ratio(None, 100) is None
    assert compute_activity_decay_ratio(5, None) is None
    assert compute_activity_decay_ratio(None, None) is None


def test_compute_activity_decay_ratio_rejects_thin_baseline():
    # h24 total under ACTIVITY_DECAY_MIN_H24_TXNS -- too little real
    # trading to trust a baseline rate at all, even if h1 is 0.
    assert compute_activity_decay_ratio(0, ACTIVITY_DECAY_MIN_H24_TXNS - 1) is None


def test_compute_activity_decay_ratio_steady_activity_is_near_one():
    # 240 txns over 24h = 10/hr average; last hour also had 10 -> ratio 1.0
    ratio = compute_activity_decay_ratio(10, 240)
    assert ratio is not None
    assert abs(ratio - 1.0) < 0.01


def test_compute_activity_decay_ratio_real_collapse():
    # 480 txns over 24h = 20/hr average; last hour only had 1 -> ratio 0.05
    # (activity down to 5% of its own 24h pace -- a real cliff).
    ratio = compute_activity_decay_ratio(1, 480)
    assert ratio is not None
    assert abs(ratio - 0.05) < 0.001


def test_compute_activity_decay_ratio_accelerating_activity_above_one():
    # A token picking up steam: last hour running hotter than its 24h
    # average -- ratio > 1, never clamped, since this only ever gates a
    # DOWNWARD override (score_token itself enforces that, not this
    # function -- this stays a pure, honest ratio).
    ratio = compute_activity_decay_ratio(50, 240)  # 10/hr avg, 50 last hour
    assert ratio is not None
    assert abs(ratio - 5.0) < 0.01


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
            return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        if url.endswith("/solana/token_security"):
            return {"ok": True, "status_code": 200, "url": url, "json": {
                "result": {mint: {"mintable": {"status": "0"}, "freezable": {"status": "0"},
                                   "lp_holders": [{"balance": "900", "is_locked": 1},
                                                  {"balance": "100", "is_locked": 0}]}}}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint(mint, "solana", is_pregraduation=False)
    assert "error" not in result
    assert captured_kwargs["goplus_mint_authority_revoked"] is True
    assert captured_kwargs["goplus_freeze_authority_revoked"] is True
    assert captured_kwargs["goplus_lp_locked"] is True


def test_score_solana_mint_prefilter_calls_goplus_first_but_never_overrides_real_madeonsol_signals(monkeypatch):
    # Updated Sept 29 2026: GoPlus IS now called first on every Solana mint
    # (free pre-filter, see score_solana_mint's docstring) -- so the old
    # "never called when /risk succeeds" guarantee no longer holds by
    # design. What must still hold: even though GoPlus is now called first,
    # it must never be used to second-guess/override real MadeOnSol signal
    # data once /risk succeeds -- that's the real regression this test
    # guards, just with the correct updated premise. The pre-filter call
    # here returns a clean (not-flagged) result, so scoring proceeds to
    # MadeOnSol as normal.
    import layers.layer0_scoring as l0

    calls = []
    mint = "MINT123"
    real_signals_from_madeonsol_risk = l0.signals_from_madeonsol_risk
    captured_kwargs = {}

    def spy_signals(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return real_signals_from_madeonsol_risk(*args, **kwargs)

    monkeypatch.setattr(l0, "signals_from_madeonsol_risk", spy_signals)

    def fake_get_json(url, headers=None, params=None, timeout=20):
        calls.append(url)
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        if url.endswith("/solana/token_security"):
            return {"ok": True, "status_code": 200, "url": url, "json": {
                "result": {mint: {"mintable": {"status": "0"}, "freezable": {"status": "0"},
                                   "lp_holders": [{"balance": "1000", "is_locked": 1}]}}}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: None)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.score_solana_mint(mint, "solana", is_pregraduation=True)
    assert "error" not in result
    # GoPlus WAS called (pre-filter) ...
    assert any("solana/token_security" in c for c in calls)
    # ... but never used as a signal-fallback source, since /risk succeeded.
    assert captured_kwargs["goplus_mint_authority_revoked"] is None
    assert captured_kwargs["goplus_freeze_authority_revoked"] is None
    assert captured_kwargs["goplus_lp_locked"] is None


def test_fetch_madeonsol_token_risk_fails_closed_when_budget_below_3(monkeypatch):
    import layers.layer0_scoring as l0
    state.record_madeonsol_calls(state.MADEONSOL_DAILY_BUDGET - 2)  # only 2 slots left, need 3

    def unexpected_call(*a, **kw):
        raise AssertionError("get_json should not be called when budget can't cover all 3 calls")

    monkeypatch.setattr(l0, "get_json", unexpected_call)
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    result = l0.fetch_madeonsol_token_risk("MINT123", "solana")
    assert result["ok"] is False
    assert "budget" in result["reason"].lower()


def test_fetch_madeonsol_token_risk_records_3_calls_on_success(monkeypatch):
    import layers.layer0_scoring as l0

    monkeypatch.setattr(l0, "get_json", lambda *a, **kw: {"ok": True, "status_code": 200, "json": {}})
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    assert state.madeonsol_calls_today() == 0
    l0.fetch_madeonsol_token_risk("MINT123", "solana")
    assert state.madeonsol_calls_today() == 3


def test_fetch_solana_top10_holder_pct_computes_real_concentration(monkeypatch):
    # Free RPC alternative to MadeOnSol's PRO-gated /holders (Sept 27 2026).
    import layers.layer0_scoring as l0

    def fake_rpc_call(chain, method, params, timeout=15):
        assert chain == "solana"
        if method == "getTokenLargestAccounts":
            return {"ok": True, "result": {"value": [
                {"uiAmount": 100.0}, {"uiAmount": 50.0}, {"uiAmount": 25.0},
            ]}}
        if method == "getTokenSupply":
            return {"ok": True, "result": {"value": {"uiAmount": 1000.0}}}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    pct = l0.fetch_solana_top10_holder_pct("MINT123")
    # top 10 (only 3 accounts exist here) = 175/1000 = 0.175
    assert pct == 0.175


def test_fetch_solana_top10_holder_pct_none_on_rpc_failure(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": False, "reason": "rate limited"})
    assert l0.fetch_solana_top10_holder_pct("MINT123") is None


def test_fetch_solana_top10_holder_pct_none_when_supply_unknown(monkeypatch):
    import layers.layer0_scoring as l0

    def fake_rpc_call(chain, method, params, timeout=15):
        if method == "getTokenLargestAccounts":
            return {"ok": True, "result": {"value": [{"uiAmount": 10.0}]}}
        return {"ok": True, "result": {"value": {"uiAmount": None}}}

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    assert l0.fetch_solana_top10_holder_pct("MINT123") is None


def test_fetch_solana_holder_count_returns_real_account_count(monkeypatch):
    import layers.layer0_scoring as l0

    captured = {}

    def fake_rpc_call(chain, method, params, timeout=15):
        captured["method"] = method
        captured["params"] = params
        assert chain == "solana"
        assert method == "getProgramAccounts"
        return {"ok": True, "result": [{"pubkey": "a"}, {"pubkey": "b"}, {"pubkey": "c"}]}

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    count = l0.fetch_solana_holder_count("MINT123")
    assert count == 3
    # confirm it filtered by dataSize 165 + memcmp on the real mint, not a guess
    assert captured["params"][0] == l0.SPL_TOKEN_PROGRAM_ID
    filters = captured["params"][1]["filters"]
    assert {"dataSize": 165} in filters
    assert {"memcmp": {"offset": 0, "bytes": "MINT123"}} in filters


def test_fetch_solana_holder_count_none_on_rpc_failure(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": False, "reason": "down"})
    assert l0.fetch_solana_holder_count("MINT123") is None


def test_signals_from_madeonsol_risk_uses_rpc_top10_fallback_only_when_madeonsol_missing(monkeypatch):
    # Real MadeOnSol holders data must always win when present; the free
    # RPC value only fills the gap when MadeOnSol's own data is absent.
    risk = _load("madeonsol_risk_sample.json")

    # MadeOnSol present -> RPC fallback ignored even if passed
    sig = signals_from_madeonsol_risk(
        risk, holders_json={"top10_share": 34.0}, bundle_json={"bundle": {"held_pct_of_supply": 0.06}},
        is_pregraduation=True, rpc_top10_holder_pct=0.99,
    )
    assert sig.top10_holder_pct == 0.34

    # MadeOnSol absent -> RPC fallback used
    sig2 = signals_from_madeonsol_risk(
        risk, holders_json={}, bundle_json={"bundle": {"held_pct_of_supply": 0.06}},
        is_pregraduation=True, rpc_top10_holder_pct=0.21,
    )
    assert sig2.top10_holder_pct == 0.21


def test_score_solana_mint_records_holder_point_and_computes_growth(monkeypatch):
    # Real wiring check: score_solana_mint should feed state.record_holder_point
    # with the free RPC holder count, so holder_growth_rate_per_hr -- 20 of
    # every score's 100 points -- actually gets real Solana data over time
    # instead of staying permanently None.
    import layers.layer0_scoring as l0

    def fake_get_json(url, headers=None, params=None, timeout=20):
        if url.endswith("/risk"):
            return {"ok": True, "status_code": 200, "url": url, "json": _load("madeonsol_risk_sample.json")}
        if url.endswith("/holders"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"top10_share": 30.0}}
        if url.endswith("/bundle"):
            return {"ok": True, "status_code": 200, "url": url, "json": {"bundle": {"held_pct_of_supply": 0.05}}}
        if "dexscreener" in url:
            return {"ok": True, "status_code": 200, "url": url, "json": []}
        if "gopluslabs" in url:
            return {"ok": False, "status_code": 500, "url": url, "json": {}}
        raise AssertionError(f"unexpected URL: {url}")

    holder_counts = iter([10, 40])  # simulate real growth across two cycles

    monkeypatch.setattr(l0, "get_json", fake_get_json)
    monkeypatch.setattr(l0, "fetch_solana_holder_count", lambda mint: next(holder_counts))
    monkeypatch.setattr(l0.CONFIG, "madeonsol_api_key", "msk_test")

    mint = "GROWTHMINT"
    now = time.time()
    # First cycle: only 1 point recorded so far -- rate stays None (needs >=2).
    monkeypatch.setattr(l0.time, "time", lambda: now)
    l0.score_solana_mint(mint, "solana", is_pregraduation=True)
    history_after_first = state.get_holder_history(mint)
    assert len(history_after_first) == 1
    assert history_after_first[0][1] == 10

    # Second cycle, 1 real hour later -- rate should now compute for real.
    monkeypatch.setattr(l0.time, "time", lambda: now + 3600)
    result = l0.score_solana_mint(mint, "solana", is_pregraduation=True)
    history_after_second = state.get_holder_history(mint)
    assert len(history_after_second) == 2
    assert "error" not in result


def test_fetch_solana_token_deployer_returns_oldest_tx_fee_payer(monkeypatch):
    import layers.layer0_scoring as l0

    captured = {}

    def fake_rpc_call(chain, method, params, timeout=15):
        captured.setdefault("calls", []).append((method, params))
        assert chain == "solana"
        if method == "getSignaturesForAddress":
            assert params[0] == "MINT123"
            assert params[1] == {"limit": 1000}
            # newest-first, as the real RPC returns them
            return {"ok": True, "result": [
                {"signature": "sig_newest"},
                {"signature": "sig_middle"},
                {"signature": "sig_oldest"},
            ]}
        if method == "getTransaction":
            assert params[0] == "sig_oldest"
            assert params[1] == {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            return {"ok": True, "result": {
                "transaction": {"message": {"accountKeys": [
                    {"pubkey": "DEPLOYER_WALLET_ABC", "signer": True},
                    {"pubkey": "OTHER_ACCOUNT", "signer": False},
                ]}}
            }}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    deployer = l0.fetch_solana_token_deployer("MINT123")
    assert deployer == "DEPLOYER_WALLET_ABC"
    methods_called = [c[0] for c in captured["calls"]]
    assert methods_called == ["getSignaturesForAddress", "getTransaction"]


def test_fetch_solana_token_deployer_none_on_no_signatures(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": True, "result": []})
    assert l0.fetch_solana_token_deployer("MINT123") is None


def test_fetch_solana_token_deployer_none_on_rpc_failure(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": False, "reason": "down"})
    assert l0.fetch_solana_token_deployer("MINT123") is None


def test_fetch_solana_token_deployer_none_on_malformed_transaction(monkeypatch):
    import layers.layer0_scoring as l0

    def fake_rpc_call(chain, method, params, timeout=15):
        if method == "getSignaturesForAddress":
            return {"ok": True, "result": [{"signature": "sig1"}]}
        return {"ok": True, "result": {"unexpected": "shape"}}

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    assert l0.fetch_solana_token_deployer("MINT123") is None


def test_fetch_solana_dev_holding_pct_computes_real_percentage(monkeypatch):
    import layers.layer0_scoring as l0

    captured = {}

    def fake_rpc_call(chain, method, params, timeout=15):
        captured.setdefault("calls", []).append((method, params))
        if method == "getTokenAccountsByOwner":
            assert params[0] == "DEPLOYER_WALLET_ABC"
            assert params[1] == {"mint": "MINT123"}
            assert params[2] == {"encoding": "jsonParsed"}
            return {"ok": True, "result": {"value": [
                {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 150000000.0}}}}}},
            ]}}
        if method == "getTokenSupply":
            return {"ok": True, "result": {"value": {"uiAmount": 1000000000.0}}}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    pct = l0.fetch_solana_dev_holding_pct("MINT123", "DEPLOYER_WALLET_ABC")
    assert pct == pytest.approx(0.15)


def test_fetch_solana_dev_holding_pct_none_without_deployer_wallet(monkeypatch):
    import layers.layer0_scoring as l0
    # No RPC call should even be attempted -- guard clause, not a wasted call.
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert l0.fetch_solana_dev_holding_pct("MINT123", None) is None


def test_fetch_solana_dev_holding_pct_none_on_rpc_failure(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": False, "reason": "down"})
    assert l0.fetch_solana_dev_holding_pct("MINT123", "DEPLOYER_WALLET_ABC") is None


def test_fetch_solana_dev_holding_pct_zero_when_deployer_holds_nothing(monkeypatch):
    import layers.layer0_scoring as l0

    def fake_rpc_call(chain, method, params, timeout=15):
        if method == "getTokenAccountsByOwner":
            return {"ok": True, "result": {"value": []}}
        if method == "getTokenSupply":
            return {"ok": True, "result": {"value": {"uiAmount": 1000000000.0}}}
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    pct = l0.fetch_solana_dev_holding_pct("MINT123", "DEPLOYER_WALLET_ABC")
    assert pct == 0.0


def test_classify_dev_holding_pct_tiers():
    import layers.layer0_scoring as l0
    assert l0.classify_dev_holding_pct(None) == "unknown"
    assert l0.classify_dev_holding_pct(0.0) == "none"
    assert l0.classify_dev_holding_pct(0.049) == "none"
    assert l0.classify_dev_holding_pct(0.05) == "notable"
    assert l0.classify_dev_holding_pct(0.10) == "notable"
    assert l0.classify_dev_holding_pct(0.101) == "risk"
    assert l0.classify_dev_holding_pct(0.30) == "risk"


def test_fetch_solana_wallet_first_seen_ts_returns_oldest_blocktime(monkeypatch):
    import layers.layer0_scoring as l0

    def fake_rpc_call(chain, method, params, timeout=15):
        assert chain == "solana"
        assert method == "getSignaturesForAddress"
        assert params[0] == "WALLET_ABC"
        assert params[1] == {"limit": 1000}
        return {"ok": True, "result": [
            {"signature": "sig_newest", "blockTime": 1900000000},
            {"signature": "sig_oldest", "blockTime": 1700000000},
        ]}

    monkeypatch.setattr(l0, "rpc_call", fake_rpc_call)
    assert l0.fetch_solana_wallet_first_seen_ts("WALLET_ABC") == 1700000000


def test_fetch_solana_wallet_first_seen_ts_none_on_empty_history(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": True, "result": []})
    assert l0.fetch_solana_wallet_first_seen_ts("WALLET_ABC") is None


def test_fetch_solana_wallet_first_seen_ts_none_on_rpc_failure(monkeypatch):
    import layers.layer0_scoring as l0
    monkeypatch.setattr(l0, "rpc_call", lambda *a, **kw: {"ok": False, "reason": "down"})
    assert l0.fetch_solana_wallet_first_seen_ts("WALLET_ABC") is None


def test_classify_deployer_wallet_age_tiers():
    import layers.layer0_scoring as l0
    now = 2_000_000_000.0
    assert l0.classify_deployer_wallet_age(None, now) == "unknown"
    assert l0.classify_deployer_wallet_age(now - 60, now) == "fresh"           # 1 min old
    assert l0.classify_deployer_wallet_age(now - 3599, now) == "fresh"         # just under 1hr
    assert l0.classify_deployer_wallet_age(now - 3600 * 2, now) == "new"       # 2hr old
    assert l0.classify_deployer_wallet_age(now - 3600 * 23, now) == "new"      # 23hr old
    assert l0.classify_deployer_wallet_age(now - 3600 * 25, now) == "established"  # 25hr old


def test_classify_deployer_wallet_age_uses_real_wallclock_by_default(monkeypatch):
    import layers.layer0_scoring as l0
    fixed_now = 2_000_000_000.0
    monkeypatch.setattr(l0.time, "time", lambda: fixed_now)
    # first_seen 30 minutes before "now" -> fresh, without passing now_ts explicitly
    assert l0.classify_deployer_wallet_age(fixed_now - 1800) == "fresh"


# --- Launch-window collapse override (Ali, Sept 28 2026) -- real backtest
# evidence: the blended structural score alone let real labeled rugs/
# pump_dumps with a severe launch-window collapse (-95% to -99.8%) still
# clear band B, because the drawdown signal was only 10 of 100 points and
# got diluted by everything else. This hard-overrides band to D whenever a
# severe collapse (<=-60%, matching score_token's own existing "severe
# collapse" cutoff on this signal's point curve) is present, regardless of
# how healthy the rest of the snapshot looks -- see score_token's inline
# comment for the exact real numbers this is calibrated against. ---
from layers.layer0_scoring import RawSignals


def _near_perfect_signals(price_drawdown_from_peak_pct=None):
    """Every OTHER signal maxed out/healthy -- isolates the override's
    effect from the rest of the scoring curve. Without the override, this
    would score comfortably in band A on its own (see
    test_near_perfect_signals_without_collapse_score_band_a below)."""
    return RawSignals(
        top10_holder_pct=0.05,
        lp_locked_or_curve_healthy=True,
        mint_authority_revoked=True,
        freeze_authority_revoked=True,
        vol_to_liq_ratio=3.0,
        holder_growth_rate_per_hr=30.0,
        bundler_sniper_pct=0.0,
        price_drawdown_from_peak_pct=price_drawdown_from_peak_pct,
        liquidity_usd=50_000.0,
        is_pregraduation_solana=False,
    )


def _near_perfect_signals_for_activity(txn_activity_decay_ratio=None):
    """Same idea as _near_perfect_signals above, but isolates the NEW
    activity-decay override (added Sept 30 2026, checklist item C) instead
    -- every other signal maxed out/healthy, including a healthy (non-
    collapsed) price_drawdown_from_peak_pct so the two overrides don't
    interfere with each other in these tests."""
    return RawSignals(
        top10_holder_pct=0.05,
        lp_locked_or_curve_healthy=True,
        mint_authority_revoked=True,
        freeze_authority_revoked=True,
        vol_to_liq_ratio=3.0,
        holder_growth_rate_per_hr=30.0,
        bundler_sniper_pct=0.0,
        price_drawdown_from_peak_pct=-5.0,
        liquidity_usd=50_000.0,
        is_pregraduation_solana=False,
        txn_activity_decay_ratio=txn_activity_decay_ratio,
    )


def test_near_perfect_signals_without_collapse_score_band_a():
    # Sanity baseline -- confirms the override is what's forcing D below,
    # not some other effect of these particular signal values.
    result = score_token(_near_perfect_signals(price_drawdown_from_peak_pct=-5.0))
    assert result.band == "A"


def test_severe_collapse_overrides_band_to_d_even_with_perfect_other_signals():
    result = score_token(_near_perfect_signals(price_drawdown_from_peak_pct=-99.8))  # real JEANCOIN figure
    assert result.band == "D"
    assert any("OVERRIDE" in r for r in result.reasons)


def test_collapse_exactly_at_threshold_triggers_override():
    result = score_token(_near_perfect_signals(price_drawdown_from_peak_pct=-60.0))
    assert result.band == "D"


def test_collapse_just_under_threshold_does_not_trigger_override():
    result = score_token(_near_perfect_signals(price_drawdown_from_peak_pct=-59.9))
    assert result.band == "A"
    assert not any("OVERRIDE" in r for r in result.reasons)


def test_real_moonshot_drawdown_does_not_trigger_override():
    # PAID, a real labeled moonshot from Ali's backtest -- worst observed
    # drawdown among real moonshots was -37.96%, well clear of the -60%
    # cutoff this override uses.
    result = score_token(_near_perfect_signals(price_drawdown_from_peak_pct=-37.96))
    assert result.band == "A"


def test_unknown_drawdown_does_not_trigger_override():
    # Missing data must never fake a verdict, same convention as every
    # other signal in this file -- unknown stays unknown, no override.
    sig = _near_perfect_signals(price_drawdown_from_peak_pct=None)
    result = score_token(sig)
    assert result.band == "A"


def test_override_never_promotes_a_band_only_demotes():
    # A token that would already score D on its own (bad everything) with
    # a severe collapse on top must simply stay D, not error or flip.
    sig = RawSignals(
        top10_holder_pct=0.9, lp_locked_or_curve_healthy=False,
        mint_authority_revoked=False, freeze_authority_revoked=False,
        vol_to_liq_ratio=200.0, holder_growth_rate_per_hr=0.0,
        bundler_sniper_pct=0.9, price_drawdown_from_peak_pct=-99.0,
        liquidity_usd=100.0, is_pregraduation_solana=False,
    )
    result = score_token(sig)
    assert result.band == "D"


def test_override_applies_to_pregraduation_solana_bands_too():
    sig = _near_perfect_signals(price_drawdown_from_peak_pct=-95.94)  # real StonkBlend figure
    sig.is_pregraduation_solana = True
    result = score_token(sig)
    assert result.band == "D"


# ---------------------------------------------------------------------------
# Activity-decay override tests (added Sept 30 2026, checklist item C) --
# same discipline as the drawdown-override tests above, mirrored for the
# new independent signal.
# ---------------------------------------------------------------------------

def test_no_activity_decay_signal_scores_band_a():
    # Sanity baseline, same role as test_near_perfect_signals_without_collapse_score_band_a.
    result = score_token(_near_perfect_signals_for_activity(txn_activity_decay_ratio=None))
    assert result.band == "A"


def test_severe_activity_decay_overrides_band_to_d():
    # Real collapse pattern: last hour running at 5% of the 24h average pace.
    result = score_token(_near_perfect_signals_for_activity(txn_activity_decay_ratio=0.05))
    assert result.band == "D"
    assert any("OVERRIDE" in r for r in result.reasons)
    assert any("activity collapsed" in r for r in result.reasons)


def test_activity_decay_exactly_at_threshold_triggers_override():
    result = score_token(_near_perfect_signals_for_activity(txn_activity_decay_ratio=0.15))
    assert result.band == "D"


def test_activity_decay_just_above_threshold_does_not_trigger():
    result = score_token(_near_perfect_signals_for_activity(txn_activity_decay_ratio=0.151))
    assert result.band == "A"
    assert not any("activity collapsed" in r for r in result.reasons)


def test_steady_activity_does_not_trigger_override():
    result = score_token(_near_perfect_signals_for_activity(txn_activity_decay_ratio=1.0))
    assert result.band == "A"


def test_accelerating_activity_does_not_trigger_override():
    # Ratio > 1 (activity picking up, not collapsing) must never be treated
    # as a decay signal.
    result = score_token(_near_perfect_signals_for_activity(txn_activity_decay_ratio=3.0))
    assert result.band == "A"


def test_activity_decay_override_never_promotes_a_band_only_demotes():
    sig = RawSignals(
        top10_holder_pct=0.9, lp_locked_or_curve_healthy=False,
        mint_authority_revoked=False, freeze_authority_revoked=False,
        vol_to_liq_ratio=200.0, holder_growth_rate_per_hr=0.0,
        bundler_sniper_pct=0.9, price_drawdown_from_peak_pct=-5.0,
        liquidity_usd=100.0, is_pregraduation_solana=False,
        txn_activity_decay_ratio=0.05,
    )
    result = score_token(sig)
    assert result.band == "D"


def test_activity_decay_and_drawdown_overrides_can_both_fire_independently():
    # Real-world case: both signals confirm the same pump-dump. Must not
    # error -- band lands on D either way. The drawdown override runs
    # first and already sets band=D, so the activity-decay override's own
    # `if band != "D"` guard correctly skips re-appending a second reason
    # (same "no-op once already at the floor" behavior as
    # test_override_never_promotes_a_band_only_demotes above) -- this
    # confirms the two overrides compose safely, not that both always log.
    sig = _near_perfect_signals(price_drawdown_from_peak_pct=-80.0)
    sig.txn_activity_decay_ratio = 0.05
    result = score_token(sig)
    assert result.band == "D"
    assert any("OVERRIDE" in r for r in result.reasons)

    # And the reverse order -- activity-decay alone (no drawdown collapse)
    # must independently reach D on its own, confirming it's not silently
    # dependent on the drawdown override having fired first.
    sig2 = _near_perfect_signals(price_drawdown_from_peak_pct=-5.0)
    sig2.txn_activity_decay_ratio = 0.05
    result2 = score_token(sig2)
    assert result2.band == "D"
    assert any("activity collapsed" in r for r in result2.reasons)


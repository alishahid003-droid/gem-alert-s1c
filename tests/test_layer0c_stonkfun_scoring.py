import json
import os

import pytest

import state
from layers.layer0c_stonkfun_scoring import (
    parse_stonkfun_token_detail,
    compute_momentum,
    _within_lookback,
    MOMENTUM_GEM_MIN_MULTIPLE,
    is_snipe_candidate,
    SNIPE_TRIGGER_MULTIPLE,
    SNIPE_MIN_LIQUIDITY_USD,
    StonkfunSignals,
    score_stonkfun_token,
    parse_stonkfun_tokens,
    compute_stonkfun_deployer_tier_from_payload,
    STONKFUN_ALLOWED_QUOTE_SYMBOLS,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    # Same pattern as test_executor_circuit_breaker.py -- the new snipe-
    # worker test writes to state.py (mark_layer0c_snipe_bought /
    # position_state via handle_stage1_candidate) and must not touch the
    # real .gem_alert_state.json on disk, or repeat test runs see stale
    # 'already bought' entries from a PRIOR run and silently skip work.
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def test_deep_liquidity_and_healthy_ratio_scores_band_a():
    sig = StonkfunSignals(
        market_cap_usd=100_000, peak_market_cap_usd=110_000,
        liquidity_usd=60_000, volume24h_usd=150_000, graduation_progress=1.0,
    )
    result = score_stonkfun_token(sig)
    assert result.band == "A"
    assert result.score >= 65


def test_worst_case_signals_score_band_d():
    sig = StonkfunSignals(
        market_cap_usd=500, peak_market_cap_usd=50_000,
        liquidity_usd=0, volume24h_usd=0, graduation_progress=0.0,
    )
    result = score_stonkfun_token(sig)
    assert result.band == "D"


def test_fully_unknown_signals_still_returns_a_score_not_a_crash():
    result = score_stonkfun_token(StonkfunSignals())
    assert 0 <= result.score <= 100
    assert result.band in {"A", "B", "C", "D"}


def test_dumped_token_well_below_peak_scores_worse_than_one_at_peak():
    at_peak = score_stonkfun_token(StonkfunSignals(
        market_cap_usd=100_000, peak_market_cap_usd=100_000,
        liquidity_usd=60_000, volume24h_usd=100_000, graduation_progress=1.0,
    ))
    dumped = score_stonkfun_token(StonkfunSignals(
        market_cap_usd=5_000, peak_market_cap_usd=100_000,
        liquidity_usd=60_000, volume24h_usd=100_000, graduation_progress=1.0,
    ))
    assert dumped.score < at_peak.score


def test_parse_filters_to_sol_quoted_launches_only():
    payload = _load("stonkfun_tokens_sample.json")
    tokens = parse_stonkfun_tokens(payload)
    # Fixture has 3 entries: one SOL-quoted, one xSOL (leverage), one stock-paired.
    assert len(tokens) == 1
    assert tokens[0]["symbol"] == "REALSOL"


def test_parse_output_has_expected_shape():
    payload = _load("stonkfun_tokens_sample.json")
    tokens = parse_stonkfun_tokens(payload)
    t = tokens[0]
    assert set(["mint", "symbol", "name", "created_at", "score", "band", "reasons"]) <= set(t.keys())


def test_allowed_quote_symbols_are_sol_variants_only():
    assert STONKFUN_ALLOWED_QUOTE_SYMBOLS == {"SOL", "WSOL", "WRAPPED SOL"}


def test_deployer_tier_flags_serial_low_mcap_spammer():
    payload = _load("stonkfun_launches_spammer_sample.json")
    result = compute_stonkfun_deployer_tier_from_payload(payload)
    assert result["tier"] == "spammer"
    assert result["launch_count"] >= 5


def test_deployer_tier_single_launch_is_neutral_not_elite():
    payload = _load("stonkfun_launches_single_sample.json")
    result = compute_stonkfun_deployer_tier_from_payload(payload)
    # v1 honestly can't call this "good"/"elite" from launch count alone --
    # see compute_stonkfun_deployer_tier's docstring.
    assert result["tier"] == "neutral"
    assert result["launch_count"] == 1


def test_deployer_tier_no_history_is_unknown():
    result = compute_stonkfun_deployer_tier_from_payload({"data": []})
    assert result["tier"] == "unknown"
    assert result["launch_count"] == 0


def test_parse_token_detail_extracts_real_levercat_fields():
    payload = _load("stonkfun_levercat_detail_real.json")
    detail = parse_stonkfun_token_detail(payload)
    assert detail["mint"] == "AGi2s9zPRPHs3zEDPhPTroumTEXK5ufymYSfEFndCSSW"
    assert detail["symbol"] == "LEVERCAT"
    assert detail["quote_symbol"] == "XSOL"
    assert detail["start_mcap_usd"] == 2948.746581036123
    assert detail["peak_mcap_usd"] == 8092369.744539557


def test_parse_token_detail_flags_xsol_quote_as_not_executable():
    # Real ground truth: LEVERCAT is xSOL-quoted, so swap_executor has no
    # route for it -- momentum detection must still see it, just tagged.
    payload = _load("stonkfun_levercat_detail_real.json")
    detail = parse_stonkfun_token_detail(payload)
    assert detail["executable"] is False


def test_parse_token_detail_returns_none_on_malformed_payload():
    assert parse_stonkfun_token_detail({"data": {}}) is None
    assert parse_stonkfun_token_detail({}) is None


def test_compute_momentum_flags_real_levercat_as_a_momentum_gem():
    # Real numbers: $2,948.75 -> $8,092,369.74 peak = ~2,744x. Should clear
    # MOMENTUM_GEM_MIN_MULTIPLE (20x) by a wide margin.
    payload = _load("stonkfun_levercat_detail_real.json")
    detail = parse_stonkfun_token_detail(payload)
    momentum = compute_momentum(detail)
    assert momentum["is_momentum_gem"] is True
    assert momentum["peak_multiple"] > 2000  # sanity check on the real ratio


def test_compute_momentum_not_a_gem_below_threshold():
    detail = {"start_mcap_usd": 10_000, "peak_mcap_usd": 15_000, "current_mcap_usd": 12_000}
    momentum = compute_momentum(detail)
    assert momentum["is_momentum_gem"] is False
    assert momentum["peak_multiple"] == 1.5


def test_compute_momentum_returns_none_without_start_mcap():
    assert compute_momentum({"start_mcap_usd": None, "peak_mcap_usd": 50_000}) is None
    assert compute_momentum({"start_mcap_usd": 0, "peak_mcap_usd": 50_000}) is None


def test_within_lookback_true_for_recent_launch():
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    assert _within_lookback(recent, now_utc=now) is True


def test_within_lookback_false_for_stale_launch():
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    stale = (now - timedelta(hours=200)).isoformat().replace("+00:00", "Z")
    assert _within_lookback(stale, now_utc=now) is False


def test_within_lookback_false_for_missing_or_bad_timestamp():
    assert _within_lookback(None) is False
    assert _within_lookback("not-a-date") is False


def test_momentum_gem_threshold_is_documented_and_stable():
    # Locks the threshold value so a future edit can't silently loosen/
    # tighten it without a test failing to flag the change.
    assert MOMENTUM_GEM_MIN_MULTIPLE == 20


def test_snipe_candidate_passes_with_healthy_early_signals():
    detail = {"start_mcap_usd": 5_000, "current_mcap_usd": 20_000, "liquidity_usd": 10_000}
    result = is_snipe_candidate(detail, deployer_tier_result={"tier": "neutral", "launch_count": 1})
    assert result["candidate"] is True
    assert result["current_multiple"] == 4.0


def test_snipe_candidate_rejects_spammer_deployer_even_with_good_multiple():
    detail = {"start_mcap_usd": 3_000, "current_mcap_usd": 50_000, "liquidity_usd": 20_000}
    result = is_snipe_candidate(detail, deployer_tier_result={"tier": "spammer", "launch_count": 8})
    assert result["candidate"] is False
    assert "spammer" in result["reasons"][0]


def test_snipe_candidate_rejects_below_liquidity_floor():
    detail = {"start_mcap_usd": 3_000, "current_mcap_usd": 20_000, "liquidity_usd": 500}
    assert SNIPE_MIN_LIQUIDITY_USD > 500
    result = is_snipe_candidate(detail, deployer_tier_result=None)
    assert result["candidate"] is False


def test_snipe_candidate_rejects_below_trigger_multiple():
    detail = {"start_mcap_usd": 10_000, "current_mcap_usd": 15_000, "liquidity_usd": 10_000}
    assert (15_000 / 10_000) < SNIPE_TRIGGER_MULTIPLE
    result = is_snipe_candidate(detail, deployer_tier_result=None)
    assert result["candidate"] is False


def test_snipe_candidate_uses_current_not_peak_mcap():
    # Correctness check for the look-ahead-bias fix: a huge peak with a
    # current mcap still below the trigger must NOT pass -- peak is never
    # read by is_snipe_candidate at all.
    detail = {"start_mcap_usd": 3_000, "current_mcap_usd": 4_000, "peak_mcap_usd": 8_000_000,
              "liquidity_usd": 50_000}
    result = is_snipe_candidate(detail, deployer_tier_result=None)
    assert result["candidate"] is False


def test_snipe_candidate_handles_missing_start_mcap_gracefully():
    result = is_snipe_candidate({"start_mcap_usd": None, "current_mcap_usd": 50_000, "liquidity_usd": 10_000})
    assert result["candidate"] is False
    assert result["current_multiple"] is None


def test_snipe_worker_dry_run_cycle_does_not_call_real_execution():
    # Integration-shaped test of run_one_cycle with every fetch function
    # monkeypatched -- no live network, no real buy, confirms the wiring
    # (listing -> detail -> deployer -> candidate check -> trigger) works
    # end to end and stays dry (no execute_buy_solana call) when dry_run=True.
    import worker_stonkfun_snipe as w

    mint = "SnipeTestMint1111111111111111111111111111"

    def fake_fetch_listing(limit):
        return {"ok": True, "raw": {"json": {"data": [
            {"mint": mint, "createdAt": "2026-09-23T00:00:00.000Z"}
        ]}}}

    def fake_fetch_detail(m):
        return {"ok": True, "raw": {"json": {"data": {
            "token": {"mint": m, "symbol": "SNIPE", "name": "Snipe Test",
                      "quote": {"symbol": "SOL"}, "createdAt": "2026-09-23T00:00:00.000Z",
                      "market": {"marketCapUsd": 20_000, "peakMarketCapUsd": 20_000,
                                 "liquidityUsd": 10_000}, "status": "new"},
            "launch": {"creator": "TestCreatorWallet1111111111111111111111111",
                       "startMarketCapUsd": 4_000},
        }}}}

    def fake_fetch_launches(creator, limit=50):
        return {"ok": True, "raw": {"json": {"data": [
            {"mint": mint, "startMarketCapUsd": 4_000, "createdAt": "2026-09-23T00:00:00.000Z"}
        ]}}}

    orig_listing, orig_detail, orig_launches = w.fetch_new_stonkfun_tokens, w.fetch_stonkfun_token_detail, w.fetch_stonkfun_launches_by_creator
    called = {"execute_buy": False}
    orig_execute = w.execute_buy_solana

    def fake_execute_buy(*a, **kw):
        called["execute_buy"] = True
        return None

    w.fetch_new_stonkfun_tokens = fake_fetch_listing
    w.fetch_stonkfun_token_detail = fake_fetch_detail
    w.fetch_stonkfun_launches_by_creator = fake_fetch_launches
    w.execute_buy_solana = fake_execute_buy
    try:
        result = w.run_one_cycle(limit=25, max_deep_lookups=15, dry_run=True)
    finally:
        w.fetch_new_stonkfun_tokens = orig_listing
        w.fetch_stonkfun_token_detail = orig_detail
        w.fetch_stonkfun_launches_by_creator = orig_launches
        w.execute_buy_solana = orig_execute

    assert result["ok"] is True
    assert mint in result["checked"]
    assert called["execute_buy"] is False  # dry run must never call real execution



def test_stonkfun_signal_count_all_three_signals_present():
    import worker_stonkfun_snipe as w
    detail = {"start_mcap_usd": 3_000, "current_mcap_usd": 30_000, "liquidity_usd": 15_000}
    deployer_result = {"tier": "neutral", "launch_count": 3}
    assert w._stonkfun_signal_count(detail, deployer_result) == 3


def test_stonkfun_signal_count_zero_when_nothing_strong():
    import worker_stonkfun_snipe as w
    detail = {"start_mcap_usd": 3_000, "current_mcap_usd": 9_000, "liquidity_usd": 2_500}
    deployer_result = {"tier": "neutral", "launch_count": 1}
    assert w._stonkfun_signal_count(detail, deployer_result) == 0


def test_stonkfun_signal_count_spammer_deployer_never_contributes():
    import worker_stonkfun_snipe as w
    detail = {"start_mcap_usd": 3_000, "current_mcap_usd": 30_000, "liquidity_usd": 15_000}
    deployer_result = {"tier": "spammer", "launch_count": 6}
    # momentum + liquidity still count; the spammer tier itself just never adds its own point
    assert w._stonkfun_signal_count(detail, deployer_result) == 2


def test_stonkfun_signal_count_handles_missing_start_mcap():
    import worker_stonkfun_snipe as w
    detail = {"start_mcap_usd": None, "current_mcap_usd": 30_000, "liquidity_usd": 15_000}
    assert w._stonkfun_signal_count(detail, None) == 1  # only the liquidity signal can fire


def test_manage_open_positions_reprices_and_fires_moonbag_trim():
    # A position already opened at entry_mcap=$10,000; current mcap is now 4x that
    # ($40,000) -- should cross the 3x trim tier and moonbag.check_and_trim should
    # fire a (correct, though execution-inert) trim decision for it.
    import worker_stonkfun_snipe as w
    import executor.position_state as position_state

    mint = "ManageTestMint111111111111111111111111111"
    position_state.record_stage_entry("solana", mint, "stage1", 10.0, 10_000.0, "test entry")

    def fake_fetch_detail(m):
        return {"ok": True, "raw": {"json": {"data": {
            "token": {"mint": m, "symbol": "MANAGE", "name": "Manage Test",
                      "quote": {"symbol": "SOL"}, "createdAt": "2026-09-23T00:00:00.000Z",
                      "market": {"marketCapUsd": 40_000, "peakMarketCapUsd": 40_000,
                                 "liquidityUsd": 12_000}, "status": "new"},
            "launch": {"creator": "TestCreatorWallet1111111111111111111111111",
                       "startMarketCapUsd": 10_000},
        }}}}

    orig_detail = w.fetch_stonkfun_token_detail
    w.fetch_stonkfun_token_detail = fake_fetch_detail
    try:
        result = w.manage_open_stonkfun_positions(dry_run=True)
    finally:
        w.fetch_stonkfun_token_detail = orig_detail

    assert mint in result["managed"]
    assert len(result["trims_fired"]) == 1
    assert "3.0x" in result["trims_fired"][0]["trim_decision"].reason or \
           result["trims_fired"][0]["trim_decision"].tier_multiple == 3.0


def test_manage_open_positions_skips_tokens_whose_detail_lookup_fails():
    # Fail-closed: a position whose detail fetch errors is skipped for this
    # cycle, never guessed at -- confirms manage_open_stonkfun_positions
    # doesn't crash or fabricate a price when StonkFun's API can't answer.
    import worker_stonkfun_snipe as w
    import executor.position_state as position_state

    mint = "UnreachableTestMint11111111111111111111111"
    position_state.record_stage_entry("solana", mint, "stage1", 10.0, 10_000.0, "test entry")

    def failing_fetch_detail(m):
        return {"ok": False, "reason": "network unreachable"}

    orig_detail = w.fetch_stonkfun_token_detail
    w.fetch_stonkfun_token_detail = failing_fetch_detail
    try:
        result = w.manage_open_stonkfun_positions(dry_run=True)
    finally:
        w.fetch_stonkfun_token_detail = orig_detail

    assert mint not in result["managed"]
    assert result["trims_fired"] == []


def test_run_one_cycle_includes_management_results():
    # run_one_cycle now calls manage_open_stonkfun_positions internally --
    # confirm its return dict carries that result under "managed" so a
    # caller (run_loop's logging, or a future dashboard) can see it.
    import worker_stonkfun_snipe as w

    def empty_listing(limit):
        return {"ok": True, "raw": {"json": {"data": []}}}

    orig_listing = w.fetch_new_stonkfun_tokens
    w.fetch_new_stonkfun_tokens = empty_listing
    try:
        result = w.run_one_cycle(limit=25, max_deep_lookups=15, dry_run=True)
    finally:
        w.fetch_new_stonkfun_tokens = orig_listing

    assert "managed" in result
    assert "managed" in result["managed"] and "trims_fired" in result["managed"] and "defends_fired" in result["managed"]

import json
import os

import layers.layer3_backing_check as layer3
from layers.layer3_backing_check import (
    parse_reddit_crypto_token, baseline_from_daily_series, detect_spike,
    classify_backing, check_backing_spike, _quota_exhausted, _record_quota_from_headers,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_parses_reddit_crypto_token_response():
    parsed = parse_reddit_crypto_token(_load("adanos_reddit_crypto_token_sample.json"))
    assert parsed["found"] is True
    assert parsed["buzz_score"] == 82
    assert parsed["mentions"] == 420
    assert parsed["sentiment_score"] == 0.55
    assert parsed["trend"] == "rising"
    assert len(parsed["daily_series"]) == 7
    assert len(parsed["top_subreddits"]) == 2
    assert len(parsed["top_mentions"]) == 1


def test_parse_handles_symbol_not_found():
    parsed = parse_reddit_crypto_token(_load("adanos_reddit_crypto_token_not_found.json"))
    assert parsed == {"found": False}


def test_baseline_from_daily_series_excludes_latest_day():
    parsed = parse_reddit_crypto_token(_load("adanos_reddit_crypto_token_sample.json"))
    baseline = baseline_from_daily_series(parsed["daily_series"])
    # mean of the first 6 days' mentions (40,55,60,58,62,70), excluding the
    # spiking 7th day (420) which is what gets compared AGAINST the baseline.
    assert baseline == (40 + 55 + 60 + 58 + 62 + 70) / 6


def test_baseline_from_daily_series_empty_returns_none():
    assert baseline_from_daily_series([]) is None
    assert baseline_from_daily_series([{"mentions": None}]) is None


def test_spike_detected_relative_to_baseline():
    assert detect_spike(current_volume=420, baseline_volume=100) is True
    assert detect_spike(current_volume=150, baseline_volume=100) is False


def test_spike_cold_start_uses_conservative_absolute_threshold():
    assert detect_spike(current_volume=500, baseline_volume=None) is True
    assert detect_spike(current_volume=60, baseline_volume=0) is False


def test_spike_alone_never_auto_tags_verified_real_CASHCAT_vs_MEME():
    # CASHCAT and MEME produce the identical spike shape -- classify_backing
    # must NOT distinguish them without external confirmation.
    cashcat_like = classify_backing(spike_detected=True, externally_confirmed_real=False)
    meme_like = classify_backing(spike_detected=True, externally_confirmed_real=False)
    assert cashcat_like.tag == "needs-verification"
    assert meme_like.tag == "needs-verification"
    assert cashcat_like.tag == meme_like.tag  # same input shape -> same (safe) output


def test_verified_real_only_with_explicit_external_confirmation():
    result = classify_backing(spike_detected=True, externally_confirmed_real=True)
    assert result.tag == "verified-real"


def test_no_spike_no_tag():
    result = classify_backing(spike_detected=False)
    assert result.tag == "none"


# --- Adanos-specific: quota tracking and the end-to-end convenience call ---

def test_record_and_check_quota_exhausted(tmp_path, monkeypatch):
    state_file = tmp_path / "test_state.json"
    monkeypatch.setattr(layer3.state, "LOCAL_STATE_FILE", str(state_file))
    monkeypatch.setattr(layer3.state.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(layer3.state.CONFIG, "upstash_redis_rest_token", None)

    assert _quota_exhausted() is False  # unknown quota fails OPEN, not closed
    _record_quota_from_headers({"X-RateLimit-Remaining-Monthly": "12"})
    assert _quota_exhausted() is False
    _record_quota_from_headers({"X-RateLimit-Remaining-Monthly": "0"})
    assert _quota_exhausted() is True


def test_record_quota_ignores_missing_or_malformed_header(tmp_path, monkeypatch):
    state_file = tmp_path / "test_state.json"
    monkeypatch.setattr(layer3.state, "LOCAL_STATE_FILE", str(state_file))
    monkeypatch.setattr(layer3.state.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(layer3.state.CONFIG, "upstash_redis_rest_token", None)

    _record_quota_from_headers({})  # no-op, must not raise
    _record_quota_from_headers({"X-RateLimit-Remaining-Monthly": "not-a-number"})  # no-op, must not raise
    assert _quota_exhausted() is False


def test_check_backing_spike_end_to_end_spike_case(monkeypatch):
    sample = _load("adanos_reddit_crypto_token_sample.json")
    monkeypatch.setattr(layer3, "fetch_reddit_crypto_token",
                         lambda symbol, days=None: {"ok": True, "raw": {"json": sample, "status_code": 200}})
    out = check_backing_spike("PONS")
    assert out["ok"] is True
    assert out["result"].spike_detected is True
    assert out["result"].tag == "needs-verification"  # never auto-verified, even on a real-looking spike
    assert out["label"] == "PONS: symbol-matched, not contract-verified"


def test_check_backing_spike_symbol_not_found(monkeypatch):
    not_found = _load("adanos_reddit_crypto_token_not_found.json")
    monkeypatch.setattr(layer3, "fetch_reddit_crypto_token",
                         lambda symbol, days=None: {"ok": True, "raw": {"json": not_found, "status_code": 200}})
    out = check_backing_spike("ZZZNOTHING")
    assert out["ok"] is True
    assert out["result"].tag == "none"


def test_fetch_skips_without_api_key(monkeypatch):
    monkeypatch.setattr(layer3.CONFIG, "adanos_api_key", None)
    out = layer3.fetch_reddit_crypto_token("PONS")
    assert out == {"ok": False, "reason": "ADANOS_API_KEY not configured"}


def test_fetch_skips_when_quota_exhausted_without_calling_get_json(tmp_path, monkeypatch):
    state_file = tmp_path / "test_state.json"
    monkeypatch.setattr(layer3.state, "LOCAL_STATE_FILE", str(state_file))
    monkeypatch.setattr(layer3.state.CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(layer3.state.CONFIG, "upstash_redis_rest_token", None)
    monkeypatch.setattr(layer3.CONFIG, "adanos_api_key", "sk_live_test")
    layer3.state.record_adanos_quota(0)

    def _boom(*a, **k):
        raise AssertionError("get_json should not be called once the quota is known exhausted")
    monkeypatch.setattr(layer3, "get_json", _boom)

    out = layer3.fetch_reddit_crypto_token("PONS")
    assert out["ok"] is False
    assert "quota exhausted" in out["reason"]


def test_check_backing_spike_propagates_fetch_failure(monkeypatch):
    monkeypatch.setattr(layer3, "fetch_reddit_crypto_token",
                         lambda symbol, days=None: {"ok": False, "reason": "ADANOS_API_KEY not configured"})
    out = check_backing_spike("PONS")
    assert out["ok"] is False
    assert "ADANOS_API_KEY" in out["reason"]

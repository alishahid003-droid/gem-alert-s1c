"""
Tests for Layer 13 -- Fomo copy-trading/thesis detection (Ali, Sept 30
2026). Covers layers/layer13_fomo_copytrade.py's roster matching, alert
detection, new-trader-candidate discovery, and config.py's
fomoapi_ready(). Network calls (get_json, score_solana_mint,
fetch_trader_balance_usd) are monkeypatched -- no real HTTP in these
tests, same convention as test_layer12_caller_channels.py.
"""
import pytest

import config as config_module
from config import Config
import layers.layer13_fomo_copytrade as mod
from layers.roster import SELL_WATCH_ROSTER
import state


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    monkeypatch.setattr(mod, "fetch_trader_pnl", lambda handle: {"pnl_24h": None, "pnl_7d": None, "pnl_30d": None})
    monkeypatch.setattr(mod, "fetch_trader_profile", lambda handle: None)  # no network in tests
    yield


def _counts(result):
    return {"buys_recorded": result["buys_recorded"], "theses_recorded": result["theses_recorded"]}


def _cfg(api_key=None):
    return Config(fomoapi_api_key=api_key)


# --- config.py: fomoapi_ready() ---

def test_fomoapi_ready_false_without_key():
    assert Config(fomoapi_api_key=None).fomoapi_ready() is False


def test_fomoapi_ready_true_with_key():
    assert Config(fomoapi_api_key="fapi_test").fomoapi_ready() is True


# --- _match_roster: pure function, no network ---

def test_match_roster_by_handle_case_insensitive():
    name = next(iter(SELL_WATCH_ROSTER))
    assert mod._match_roster(name.upper()) == name
    assert mod._match_roster("  " + name.lower() + "  ") == name


def test_match_roster_by_display_name_when_handle_misses():
    name = next(iter(SELL_WATCH_ROSTER))
    assert mod._match_roster("some_unrelated_handle", display_name=name) == name


def test_match_roster_none_for_untracked():
    assert mod._match_roster("definitely_not_on_the_roster_xyz") is None
    assert mod._match_roster(None, None) is None


# --- fetch_fomo_alerts: config gating + response parsing ---

def test_fetch_alerts_fails_closed_when_key_missing(monkeypatch):
    monkeypatch.setattr(mod, "CONFIG", _cfg(api_key=None))
    result = mod.fetch_fomo_alerts()
    assert result["ok"] is False
    assert "FOMOAPI_API_KEY" in result["reason"]


def test_fetch_alerts_parses_response(monkeypatch):
    monkeypatch.setattr(mod, "CONFIG", _cfg(api_key="fapi_test"))
    monkeypatch.setattr(mod, "get_json", lambda url, headers=None, params=None, timeout=None:
                         {"ok": True, "json": {"alerts": [{"alertType": "buy", "trader": "x"}]}})
    result = mod.fetch_fomo_alerts()
    assert result["ok"] is True
    assert len(result["alerts"]) == 1


def test_fetch_alerts_handles_http_failure(monkeypatch):
    monkeypatch.setattr(mod, "CONFIG", _cfg(api_key="fapi_test"))
    monkeypatch.setattr(mod, "get_json", lambda url, headers=None, params=None, timeout=None:
                         {"ok": False, "status_code": 401})
    result = mod.fetch_fomo_alerts()
    assert result["ok"] is False


# --- detect_roster_buys_and_theses: the real detection logic ---

def test_detect_ignores_non_roster_traders(monkeypatch):
    monkeypatch.setattr(mod, "_score_if_solana", lambda chain, mint, allow_paid=True: {"score": 90, "band": "A", "reason": None})
    alerts = [{"alertType": "buy", "trader": "totally_unknown_person", "chain": "solana",
               "tokenAddress": "Mint111", "token": "TEST"}]
    result = mod.detect_roster_buys_and_theses(alerts)
    assert _counts(result) == {"buys_recorded": 0, "theses_recorded": 0}


def test_detect_records_roster_buy_with_score(monkeypatch):
    tracked = next(iter(SELL_WATCH_ROSTER))
    monkeypatch.setattr(mod, "_score_if_solana", lambda chain, mint, allow_paid=True: {"score": 72, "band": "B", "reason": None})
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_signal", lambda **kw: recorded.append(kw))
    alerts = [{"alertType": "buy", "trader": tracked, "chain": "solana",
               "tokenAddress": "Mint111", "token": "TEST", "usdValue": 5000,
               "text": f"{tracked} bought $TEST"}]
    result = mod.detect_roster_buys_and_theses(alerts)
    assert _counts(result) == {"buys_recorded": 1, "theses_recorded": 0}
    assert len(recorded) == 1
    assert recorded[0]["kind"] == "buy"
    assert recorded[0]["trader"] == tracked
    assert recorded[0]["score"] == 72
    assert recorded[0]["band"] == "B"


def test_detect_records_roster_thesis_with_link(monkeypatch):
    tracked = next(iter(SELL_WATCH_ROSTER))
    monkeypatch.setattr(mod, "_score_if_solana", lambda chain, mint, allow_paid=True: {"score": None, "band": None, "reason": "x"})
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_signal", lambda **kw: recorded.append(kw))
    alerts = [{"alertType": "thesis", "trader": tracked, "chain": "solana",
               "tokenAddress": "Mint222", "token": "PI", "text": "Dexscreener banner looks pretty lit",
               "links": [{"text": "dexscreener", "link": "https://dexscreener.com/solana/Mint222"}]}]
    result = mod.detect_roster_buys_and_theses(alerts)
    assert _counts(result) == {"buys_recorded": 0, "theses_recorded": 1}
    assert recorded[0]["kind"] == "thesis"
    assert recorded[0]["thesis_link"] == "https://dexscreener.com/solana/Mint222"
    assert recorded[0]["score"] is None  # unscored, never guessed


def test_detect_skips_non_solana_chain_without_guessing_score(monkeypatch):
    tracked = next(iter(SELL_WATCH_ROSTER))
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_signal", lambda **kw: recorded.append(kw))
    alerts = [{"alertType": "buy", "trader": tracked, "chain": "robinhood",
               "tokenAddress": "0xabc", "token": "SI"}]
    result = mod.detect_roster_buys_and_theses(alerts)
    assert result["buys_recorded"] == 1
    assert recorded[0]["score"] is None
    assert recorded[0]["band"] is None


def test_detect_ignores_sell_and_other_alert_types(monkeypatch):
    tracked = next(iter(SELL_WATCH_ROSTER))
    alerts = [{"alertType": "sell", "trader": tracked, "chain": "solana", "tokenAddress": "Mint1"},
              {"alertType": "listing", "trader": tracked, "chain": "solana", "tokenAddress": "Mint2"}]
    result = mod.detect_roster_buys_and_theses(alerts)
    assert _counts(result) == {"buys_recorded": 0, "theses_recorded": 0}


# --- find_new_trader_candidates ---

def test_candidates_skips_roster_members(monkeypatch):
    tracked = next(iter(SELL_WATCH_ROSTER))
    calls = []
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: calls.append(h) or 10000.0)
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_candidate", lambda **kw: recorded.append(kw))
    rows = [{"handle": tracked, "displayName": tracked, "pnlUsd": 1000, "volumeUsd": 5000}]
    result = mod.find_new_trader_candidates(rows)
    assert calls == []  # never even checked -- already tracked
    assert (result["checked"], result["candidates_found"]) == (0, 0)
    assert recorded == []


def test_candidates_records_off_roster_trader_above_threshold(monkeypatch):
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: 7500.0)
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_candidate", lambda **kw: recorded.append(kw))
    rows = [{"handle": "brand_new_whale", "displayName": "Brand New Whale",
             "pnlUsd": 20000, "volumeUsd": 100000}]
    result = mod.find_new_trader_candidates(rows, min_balance_usd=5000.0)
    assert (result["checked"], result["candidates_found"]) == (1, 1)
    assert recorded[0]["handle"] == "brand_new_whale"
    assert recorded[0]["balance_usd"] == 7500.0


def test_candidates_excludes_below_threshold(monkeypatch):
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: 1200.0)
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_candidate", lambda **kw: recorded.append(kw))
    rows = [{"handle": "small_fish", "displayName": "Small Fish"}]
    result = mod.find_new_trader_candidates(rows, min_balance_usd=5000.0)
    assert (result["checked"], result["candidates_found"]) == (1, 0)
    assert recorded == []


def test_candidates_treats_unknown_balance_as_excluded_not_zero(monkeypatch):
    # fetch_trader_balance_usd returning None (fetch failed) must NOT be
    # treated as "balance $0, excluded silently forever" -- it's excluded
    # THIS cycle only, same fail-safe convention as every other optional
    # signal in this codebase (never guess a number you don't have).
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: None)
    recorded = []
    monkeypatch.setattr(mod.state, "record_fomo_candidate", lambda **kw: recorded.append(kw))
    rows = [{"handle": "unknown_balance_guy", "displayName": "?"}]
    result = mod.find_new_trader_candidates(rows)
    assert (result["checked"], result["candidates_found"]) == (1, 0)
    assert recorded == []


def test_candidates_respects_max_checked_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: calls.append(h) or 9000.0)
    monkeypatch.setattr(mod.state, "record_fomo_candidate", lambda **kw: None)
    rows = [{"handle": f"trader_{i}", "displayName": f"Trader {i}"} for i in range(10)]
    mod.find_new_trader_candidates(rows, max_checked=3)
    assert len(calls) == 3


# --- Sept 30 2026: normalized matching, dedupe, promotion, credit governor ---

def test_match_roster_ignores_spacing_and_case():
    assert mod._match_roster("PointFarmCapital") == "point farm capital"
    assert mod._match_roster("logan_lim") == "Logan Lim"


def test_learned_handle_from_leaderboard_matches_later_alert():
    learned = mod.learn_handles_from_leaderboard([{"handle": "pfc_xyz", "displayName": "point farm capital"}])
    assert learned == 1
    assert mod._match_roster("pfc_xyz") == "point farm capital"


def test_duplicate_alert_recorded_once(monkeypatch):
    monkeypatch.setattr(mod, "_score_if_solana", lambda chain, mint, allow_paid=True: {"score": 60, "band": "B", "reason": None})
    import time as _t
    a = {"id": "abc1", "alertType": "buy", "trader": "Unipcs", "chain": "sol", "tokenAddress": "MintD",
         "ts": int(_t.time() * 1000)}
    first = mod.detect_roster_buys_and_theses([a])
    second = mod.detect_roster_buys_and_theses([a])
    assert first["buys_recorded"] == 1 and second["buys_recorded"] == 0
    assert len(mod.state.get_fomo_signal_feed()) == 1


def test_two_roster_buyers_produce_convergence(monkeypatch):
    import time as _t
    monkeypatch.setattr(mod, "_score_if_solana", lambda chain, mint, allow_paid=True: {"score": None, "band": None, "reason": "x"})
    now_ms = int(_t.time() * 1000)
    alerts = [{"id": "1", "alertType": "buy", "trader": "Unipcs", "chain": "solana", "tokenAddress": "MintC", "ts": now_ms},
              {"id": "2", "alertType": "buy", "trader": "AJC", "chain": "solana", "tokenAddress": "MintC", "ts": now_ms}]
    out = mod.detect_roster_buys_and_theses(alerts)
    assert out["convergence"] and out["convergence"][0]["count"] == 2


def test_unmatched_handles_reported():
    out = mod.detect_roster_buys_and_theses([{"alertType": "buy", "trader": "randomer", "tokenAddress": "M"}])
    assert out["unmatched"] == {"randomer": 1}
    assert "randomer" in mod.state.get_fomo_unmatched_handles()


def test_alert_ts_parses_ms_seconds_and_iso():
    assert mod.alert_ts_seconds({"ts": 1759230000000}) == 1759230000.0
    assert mod.alert_ts_seconds({"timestamp": 1759230000}) == 1759230000.0
    assert mod.alert_ts_seconds({"createdAt": "2025-09-30T10:00:00Z"}) is not None


def test_promotion_verdict_rules():
    assert mod.promotion_verdict(9000, 100, 500, False)[0] is True
    assert mod.promotion_verdict(9000, 100, 500, True)[0] is False      # insider
    assert mod.promotion_verdict(9000, -5, 500, False)[0] is False      # losing week
    assert mod.promotion_verdict(9000, None, 500, None)[0] is False     # unknown pnl
    assert mod.promotion_verdict(4000, 100, 500, False)[0] is False     # under $5k


def test_candidate_auto_promoted_then_matches_roster(monkeypatch):
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: 20000.0)
    monkeypatch.setattr(mod, "fetch_trader_pnl", lambda h: {"pnl_24h": 1.0, "pnl_7d": 500.0, "pnl_30d": 4000.0})
    out = mod.find_new_trader_candidates([{"handle": "hotnewtrader", "displayName": "Hot New"}])
    assert out["promoted"] == ["hotnewtrader"]
    assert mod._match_roster("hotnewtrader") == "Hot New"
    assert mod.tier_for("Hot New") == "auto-promoted"


def test_auto_promote_can_be_disabled(monkeypatch):
    monkeypatch.setenv("FOMO_AUTO_PROMOTE", "false")
    monkeypatch.setattr(mod, "fetch_trader_balance_usd", lambda h: 20000.0)
    monkeypatch.setattr(mod, "fetch_trader_pnl", lambda h: {"pnl_24h": 1.0, "pnl_7d": 500.0, "pnl_30d": 4000.0})
    out = mod.find_new_trader_candidates([{"handle": "hotnewtrader2", "displayName": "Hot2"}])
    assert out["promoted"] == []


def test_credit_governor_backs_off_after_402(monkeypatch):
    monkeypatch.setattr(mod, "CONFIG", _cfg(api_key="fapi_test"))
    calls = []

    def fake(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return {"ok": False, "status_code": 402, "headers": {"x-credits-remaining": "0", "x-credits-cost": "125"}}
    monkeypatch.setattr(mod, "get_json", fake)
    assert mod.fetch_fomo_alerts()["ok"] is False
    second = mod.fetch_fomo_alerts()
    assert second["ok"] is False and "backing off" in second["reason"]
    assert len(calls) == 1  # no second paid call while backed off


def test_credit_governor_daily_budget(monkeypatch):
    monkeypatch.setattr(mod, "CONFIG", _cfg(api_key="fapi_test"))
    monkeypatch.setenv("FOMOAPI_DAILY_CREDIT_BUDGET", "250")
    monkeypatch.setattr(mod, "get_json", lambda url, headers=None, params=None, timeout=None:
                        {"ok": True, "status_code": 200, "headers": {"x-credits-cost": "125"}, "json": {"alerts": []}})
    assert mod.fetch_fomo_alerts()["ok"] and mod.fetch_fomo_alerts()["ok"]
    third = mod.fetch_fomo_alerts()
    assert third["ok"] is False and "budget" in third["reason"]


def test_fetch_new_alerts_pages_until_cursor(monkeypatch):
    monkeypatch.setattr(mod, "CONFIG", _cfg(api_key="fapi_test"))
    pages = {
        None: [{"id": str(i), "alertType": "buy", "trader": "x", "ts": (1_759_000_000 + 2000 - i) * 1000} for i in range(100)],
        "p2": [{"id": str(100 + i), "alertType": "buy", "trader": "x", "ts": (1_759_000_000 + 1900 - i) * 1000} for i in range(100)],
    }

    def fake_fetch(since_iso=None, alert_type=None, limit=100, before_iso=None):
        return {"ok": True, "alerts": pages[None] if before_iso is None else pages["p2"]}
    monkeypatch.setattr(mod, "fetch_fomo_alerts", fake_fetch)
    import datetime
    since = datetime.datetime.fromtimestamp(1_759_000_000 + 1850, tz=datetime.timezone.utc).isoformat()
    out = mod.fetch_new_alerts(since, max_pages=3)
    assert out["pages"] == 2 and out["gap"] is False
    assert all(mod.alert_ts_seconds(a) > 1_759_000_000 + 1850 for a in out["alerts"])

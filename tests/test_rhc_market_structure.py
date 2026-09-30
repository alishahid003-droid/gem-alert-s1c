"""Robinhood Chain market-structure scoring + on-chain sell-leg check (Oct 1 2026)."""
from layers.layer0_scoring import (robinhood_market_structure, score_token, RawSignals,
                                   flatten_geckoterminal_pools)
from executor.swap_executor import rhc_round_trip_refusal


def item(**kw):
    base = {"buyers_h24": 620, "sellers_h24": 380, "buys_h1": 90, "sells_h1": 60,
            "liquidity_usd": 120_000, "fdv_usd": 900_000, "change_h1": 12, "change_h6": 40,
            "change_h24": 150, "change_m5": 2, "volume_24h_usd": 400_000}
    base.update(kw)
    return base


def test_strong_crowd_scores_high():
    ms, detail = robinhood_market_structure(item(), {"info": {"websites": [1], "socials": [1]}})
    assert ms >= 0.9 and "buyers/24h" in detail and "team links 2/2" in detail


def test_weak_or_dumping_coin_scores_low():
    ms, _ = robinhood_market_structure(item(buyers_h24=40, sellers_h24=90, buys_h1=10, sells_h1=40,
                                            liquidity_usd=8_000, change_h1=-20, change_h6=-40,
                                            change_h24=-60, change_m5=-25), {"info": {}})
    assert ms < 0.15


def test_not_enough_data():
    assert robinhood_market_structure({"liquidity_usd": 1000})[0] is None


def _sig(ms):
    return RawSignals(vol_to_liq_ratio=3.3, liquidity_usd=120_000, market_structure_score=ms,
                      market_structure_detail="x")


def test_rhc_score_and_coverage_use_real_data_only():
    good = score_token(_sig(0.95))
    bad = score_token(_sig(0.1))
    assert good.signal_coverage == 1.0                 # passes the 50% real-data buy gate
    assert good.band in ("A", "B") and good.score >= 70
    assert bad.band in ("C", "D") and bad.score < good.score - 30
    # without market structure (every other chain) nothing changes: defaults remain
    other = score_token(RawSignals(vol_to_liq_ratio=3.3, liquidity_usd=120_000))
    assert other.signal_coverage < 0.5


def test_gt_flatten_reads_unique_buyers():
    js = {"data": [{"attributes": {"transactions": {"h1": {"buys": 9, "sells": 4},
                                                     "h24": {"buys": 90, "sells": 50, "buyers": 61, "sellers": 33}},
                                   "price_change_percentage": {"h6": "12.5", "h24": "-3"}},
                    "relationships": {"base_token": {"data": {"id": "robinhood_0xabc"}}}}]}
    it = flatten_geckoterminal_pools(js)[0]
    assert it["buyers_h24"] == 61 and it["sellers_h24"] == 33 and it["buys_h1"] == 9
    assert it["change_h6"] == 12.5 and it["change_h24"] == -3


def test_rhc_round_trip_refusal():
    assert rhc_round_trip_refusal(10**18, None).startswith("no on-chain sell quote")
    assert "lose 40%" in rhc_round_trip_refusal(10**18, int(0.6 * 10**18))
    assert rhc_round_trip_refusal(10**18, int(0.95 * 10**18)) is None

"""Layer 14 revival watch + momentum scalper entry + scalper paper trades."""
import pytest

import state
import scheduler
import layers.layer14_revival as l14
import executor.paper_ledger as pl
import executor.compound_scalper as cs
from layers.layer0_scoring import ScoreResult

T0 = 1_790_700_000.0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    yield


def pair(liq=20_000, h1=45.0, m5=5.0, buys=120, sells=60, vol_h1=15_000, mcap=300_000):
    return {"baseToken": {"address": "TokR", "symbol": "R"}, "liquidity": {"usd": liq}, "priceUsd": "0.01",
            "marketCap": mcap, "priceChange": {"m5": m5, "h1": h1},
            "txns": {"h1": {"buys": buys, "sells": sells}, "h24": {"buys": 900, "sells": 700}},
            "volume": {"h1": vol_h1, "h24": 200_000}, "pairCreatedAt": (T0 - 7200) * 1000, "dexId": "raydium"}


def test_liquidity_add_is_a_revival():
    ok, why, momentum = l14.is_revived(2_000, l14.momentum_metrics(pair(h1=0, buys=10, sells=10)))
    assert ok and not momentum and "liquidity" in why[0]


def test_momentum_revival():
    ok, why, momentum = l14.is_revived(18_000, l14.momentum_metrics(pair()))
    assert ok and momentum


def test_dumping_coin_is_not_momentum():
    ok, _, momentum = l14.is_revived(18_000, l14.momentum_metrics(pair(m5=-25)))
    assert not momentum and not ok


def test_sell_pressure_is_not_momentum():
    _, _, momentum = l14.is_revived(18_000, l14.momentum_metrics(pair(buys=50, sells=80)))
    assert momentum is False


def test_pair_maps_to_scorer_item():
    it = l14.pair_to_gt_item("TokR", pair())
    assert it["liquidity_usd"] == 20_000 and it["txns_h1_total"] == 180 and it["pool_created_at"]


def test_hard_fail_never_watched():
    sr = ScoreResult(score=20, band="D", liquidity_flag="thin", reasons=["authority revoked: 0/2"])
    assert scheduler._hard_fail(sr) is True


def test_band_c_enrolled_with_baseline(monkeypatch):
    monkeypatch.setattr(scheduler, "send_alert", lambda a: {"sent": False})
    sr = ScoreResult(score=40, band="C", liquidity_flag="thin", reasons=["x"], signal_coverage=0.6)
    scheduler._handle_scored({"address": "TokC", "score": sr, "raw": {"liquidity_usd": 3000}}, "bsc",
                             source="geckoterminal", mc=50_000)
    w = state.get_soft_fail_watch()
    assert w[0]["band"] == "C" and w[0]["baseline_liq"] == 3000


def test_revival_cycle_rescoring_and_momentum_scalp(monkeypatch):
    state.watch_add("TokR", "solana", False, 30, ["x"], baseline_liq=2_000, band="D")
    monkeypatch.setattr(l14, "fetch_dexscreener_batch", lambda chain, addrs: {"TokR": pair()})
    sr = ScoreResult(score=48, band="C", liquidity_flag="moderate", reasons=["ok"], signal_coverage=0.5)
    monkeypatch.setattr(scheduler, "score_geckoterminal_pools",
                        lambda chain, items: [{"address": "TokR", "score": sr, "raw": items[0]}])
    handled, scalps = [], []
    monkeypatch.setattr(scheduler, "_handle_scored", lambda scored, chain, **kw: handled.append(kw["source"]))
    monkeypatch.setattr(scheduler, "handle_compound_scalper_candidate",
                        lambda *a, **kw: scalps.append(kw) or {"fired": False})
    out = scheduler._run_revival_watch_cycle(None)
    assert out == {"checked": 1, "revived": 1}
    assert handled == ["revival"] and scalps and scalps[0]["momentum"] is True
    again = scheduler._run_revival_watch_cycle(None)       # cooldown: not re-fired
    assert again["revived"] == 0


def test_momentum_lowers_scalper_band_floor():
    assert cs.signal_qualifies("C", momentum=True) is True
    assert cs.signal_qualifies("C", momentum=False) is False
    assert cs.signal_qualifies("D", momentum=True) is False


def test_scalper_paper_trade_take_profit_then_trail():
    pl.open_paper("solana", "S", "scalper", "scalper_momentum", 25, 100_000, strategy="scalper", now=T0)
    snap = lambda m: (lambda c, t: {"mcap_usd": m, "liquidity_usd": 50_000})
    tp = cs.SCALPER_CONFIG.take_profit_multiple
    pl.manage(snap(100_000 * (tp + 0.1)), now=T0 + 60)
    trail = cs.SCALPER_CONFIG.trail_stop_pct
    pl.manage(snap(100_000 * (tp + 0.1) * (1 - trail - 0.05)), now=T0 + 120)
    r = pl.scoreboard()["recent"][0]
    assert r["exit_type"] == "trail_stop" and r["pnl_usd"] > 0


def test_dexscreener_batch_falls_back_to_geckoterminal_for_robinhood_chain(monkeypatch):
    """28 Oct 2026 (Ali): robinhood_chain paper trades were dying at a 59% dead-on-arrival rate --
    not a bad strategy, DexScreener simply has no indexed pool yet for many fresh robinhood_chain
    launches (confirmed live: a direct query for one of these exact tokens returned [] while the
    SAME token queried against GeckoTerminal came back with real price/fdv/liquidity data). A token
    DexScreener's batch call doesn't return now gets one GeckoTerminal fallback lookup, ONLY on
    robinhood_chain."""
    addr = "0xdead"

    def fake_get_json(url):
        if "dexscreener.com" in url:
            return {"ok": True, "json": []}          # DexScreener has nothing for this token
        if "geckoterminal.com" in url:
            assert f"/networks/robinhood/tokens/{addr}" in url
            return {"ok": True, "json": {"data": {"attributes": {
                "fdv_usd": "3999.335646672", "market_cap_usd": None,
                "total_reserve_in_usd": "3926.3385037310956626409481"}}}}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(l14, "get_json", fake_get_json)
    out = l14.fetch_dexscreener_batch("robinhood_chain", [addr])
    assert addr in out
    assert float(out[addr]["fdv"]) == pytest.approx(3999.335646672)
    assert float(out[addr]["liquidity"]["usd"]) == pytest.approx(3926.3385037310956626409481)


def test_dexscreener_batch_no_fallback_for_other_chains(monkeypatch):
    """Contrast: the GeckoTerminal fallback is scoped to robinhood_chain only -- a missing solana
    token stays missing, same as before this fix."""
    addr = "SolMissing"

    def fake_get_json(url):
        assert "geckoterminal.com" not in url, "fallback must not fire for non-robinhood_chain"
        return {"ok": True, "json": []}

    monkeypatch.setattr(l14, "get_json", fake_get_json)
    out = l14.fetch_dexscreener_batch("solana", [addr])
    assert addr not in out


def test_geckoterminal_fallback_returns_none_on_no_data(monkeypatch):
    """A token missing from both sources stays unpriced -- the fallback never fabricates a value."""
    def fake_get_json(url):
        if "dexscreener.com" in url:
            return {"ok": True, "json": []}
        return {"ok": False, "json": None}

    monkeypatch.setattr(l14, "get_json", fake_get_json)
    out = l14.fetch_dexscreener_batch("robinhood_chain", ["0xnowhere"])
    assert "0xnowhere" not in out

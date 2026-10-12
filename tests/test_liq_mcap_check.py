from layers import layer0_scoring as L


def _good(**kw):
    base = dict(top10_holder_pct=0.1, lp_locked_or_curve_healthy=True, mint_authority_revoked=True,
                freeze_authority_revoked=True, vol_to_liq_ratio=3.0, holder_growth_rate_per_hr=40,
                bundler_sniper_pct=0.02, price_drawdown_from_peak_pct=0.0, liquidity_usd=300_000)
    base.update(kw)
    return L.RawSignals(**base)


def test_check_verdicts(monkeypatch):
    for k in ("LIQ_MCAP_MIN_MCAP", "LIQ_MCAP_KILL_RATIO", "LIQ_MCAP_CAP_RATIO"):
        monkeypatch.delenv(k, raising=False)
    assert L.liq_mcap_check(100_000, 10_000_000)[1] == "thin"     # exactly 1% is thin, not fake
    assert L.liq_mcap_check(50_000, 10_000_000)[1] == "fake"
    assert L.liq_mcap_check(150_000, 10_000_000)[1] == "thin"     # 1.5%
    assert L.liq_mcap_check(500_000, 10_000_000)[1] == "ok"       # 5%
    assert L.liq_mcap_check(1_000, 100_000)[1] == "ok"            # tiny cap: ratio not judged
    assert L.liq_mcap_check(None, 5_000_000) == (None, "n/a")
    assert L.liq_mcap_check(10_000, 5_000_000, is_pregraduation=True) == (None, "n/a")


def test_fake_liquidity_forces_band_d(monkeypatch):
    for k in ("LIQ_MCAP_MIN_MCAP", "LIQ_MCAP_KILL_RATIO", "LIQ_MCAP_CAP_RATIO"):
        monkeypatch.delenv(k, raising=False)
    healthy = L.score_token(_good(mcap_usd=5_000_000 / 1.0 * 0.4))     # $2M cap, $300k liq = 15%
    assert healthy.band in ("A", "B")
    fake = L.score_token(_good(liquidity_usd=40_000, mcap_usd=10_000_000))
    assert fake.band == "D" and any("not sellable" in r for r in fake.reasons)


def test_unknown_mcap_changes_nothing():
    a = L.score_token(_good())
    b = L.score_token(_good(mcap_usd=None))
    assert (a.score, a.band) == (b.score, b.band)


def test_thin_demotes_a_to_b(monkeypatch):
    monkeypatch.delenv("LIQ_MCAP_CAP_RATIO", raising=False)
    s = L.score_token(_good(liquidity_usd=150_000, mcap_usd=10_000_000))
    assert s.band != "A"

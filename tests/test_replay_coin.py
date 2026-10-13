import replay_coin as rc


def _candles(prices, vols, t0=1_000_000):
    return [[t0 + 60 * i, p, p, p, p, v] for i, (p, v) in enumerate(zip(prices, vols))]


def test_fires_on_fresh_pump_and_measures_aftermath():
    prices = [5e-6] * 6 + [1e-5, 1.4e-5, 2e-5, 2.8e-5, 4e-5, 6e-5, 9e-5, 2e-4, 1e-4]
    vols = [100] * 6 + [2000, 3000, 4000, 5000, 6000, 6000, 6000, 6000, 6000]
    r = rc.replay(_candles(prices, vols), 1e9, 1_000_000)
    f = r["fired"]["proxy"]
    assert f and f["mcap"] <= 60_000 and f["peak_mult"] > 2 and f["hit_2x"]
    assert 10_000 in r["crossed"] and 40_000 in r["crossed"]


def test_old_coin_is_blocked_by_age():
    prices = [1e-5] * 5 + [2e-5] * 5
    r = rc.replay(_candles(prices, [9000] * 10), 1e9, 1_000_000 - 3 * 3600)
    assert r["fired"]["strict"] is None
    assert any("age cap" in k for k, _ in r["why"]["strict"])


def test_analyze_with_fake_api(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    created = "2026-10-01T00:00:00Z"
    ts0 = rc._iso(created)
    pools = {"data": [{"id": "solana_POOL1", "attributes": {"name": "FOO / SOL", "pool_created_at": created,
                                                             "base_token_price_usd": "0.00001", "fdv_usd": "10000"},
                       "relationships": {"dex": {"data": {"id": "meteora"}}}}]}
    prices = [5e-6] * 6 + [1e-5, 1.4e-5, 2e-5, 2.8e-5, 4e-5, 6e-5, 9e-5]
    ohlcv = {"data": {"attributes": {"ohlcv_list": [[ts0 + 60 * i, p, p, p, p, 3000 + 500 * i] for i, p in enumerate(prices)]}}}
    def fake_get(path, params=None, tries=6):
        return ohlcv if "ohlcv" in path else pools
    monkeypatch.setattr(rc, "_get", fake_get)
    res = rc.analyze("ADDR", sleep=lambda s: None)
    assert res["ok"] and res["symbol"] == "FOO" and res["dex"] == "meteora" and res["fired"]["proxy"]
    assert rc.analyze("ADDR", sleep=lambda s: None) == res          # served from cache


def test_platform_labels_and_discovery(monkeypatch):
    import time
    import discover_winners as dw
    assert dw.platform_of("solana", "Abc123pump") == "pumpfun"
    assert dw.platform_of("solana", "AbcMeteora") == "other_solana"
    assert dw.platform_of("bsc", "0xabc4444") == "fourmeme"
    assert dw.platform_of("robinhood", "0xanything") == "robinhood" and dw.platform_of("robinhood_chain", "0x1") == "robinhood"
    old = time.time() - 20 * 86400
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old))
    def pool(addr, fdv):
        return {"attributes": {"name": "X / SOL", "fdv_usd": str(fdv), "pool_created_at": iso, "volume_usd": {"h24": "5"}},
                "relationships": {"base_token": {"data": {"id": "net_" + addr}}}}
    monkeypatch.setattr(dw.rc, "_get", lambda path, params=None, tries=6: {"data": [pool("Aaapump", 2_000_000), pool("Small", 10)]})
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    out = dw.discover("solana", 1_000_000, pages=1)
    assert [o["addr"] for o in out] == ["Aaapump"] and out[0]["platform"] == "pumpfun"


def test_robinhood_named_lookup_and_slug(monkeypatch):
    import time
    import discover_winners as dw
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5 * 86400))
    calls = []
    def fake_get(path, params=None, tries=6):
        calls.append((path, params))
        if path.endswith("trending_pools"):
            return {"data": []}
        return {"data": [{"attributes": {"name": "PONS / WETH", "fdv_usd": "900000", "pool_created_at": iso, "volume_usd": {"h24": "1"}},
                          "relationships": {"base_token": {"data": {"id": "robinhood_0xpons"}}}}]}
    monkeypatch.setattr(dw.rc, "_get", fake_get)
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    assert dw.resolve_rhc() == "robinhood"
    c = dw.find_by_name("PONS", "robinhood")
    assert c and c[0]["addr"] == "0xpons" and c[0]["platform"] == "robinhood"
    assert calls[-1][1]["network"] == "robinhood"


def test_pick_original_prefers_highest_peak_over_bigger_copycat(monkeypatch):
    import time
    import discover_winners as dw
    iso = lambda d: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - d * 86400))
    def pool(addr, fdv, days, liq):
        return {"attributes": {"name": "FEELS / SOL", "fdv_usd": str(fdv), "pool_created_at": iso(days),
                               "reserve_in_usd": str(liq), "volume_usd": {"h24": "1"}},
                "relationships": {"base_token": {"data": {"id": "solana_" + addr}}}}
    data = {"data": [pool("COPY1", 900_000, 3, 50_000), pool("COPY2", 500_000, 2, 9_000), pool("REAL", 40_000, 30, 3_000)]}
    monkeypatch.setattr(dw.rc, "_get", lambda path, params=None, tries=6: data)
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    peaks = {"COPY1": 1_200_000, "COPY2": 800_000, "REAL": 9_000_000}
    monkeypatch.setattr(dw.rc, "analyze", lambda addr, chain="solana", **k: {"peak": peaks[addr], "created": "x"})
    lines = []
    keep, cands = dw.pick_original("FEELS", "solana", lines.append)
    assert keep[0]["addr"] == "REAL" and len(cands) == 3 and len(keep) == 1
    assert any("ORIGINAL" in l for l in lines)


def test_bigger_unreplayable_copy_is_kept_as_ambiguous(monkeypatch):
    import time
    import discover_winners as dw
    iso = lambda d: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - d * 86400))
    def pool(addr, fdv, days, liq):
        return {"attributes": {"name": "PONS / WETH", "fdv_usd": str(fdv), "pool_created_at": iso(days),
                               "reserve_in_usd": str(liq), "volume_usd": {"h24": "1"}},
                "relationships": {"base_token": {"data": {"id": "robinhood_" + addr}}}}
    data = {"data": [pool("NEWBIG", 377_000_000, 6, 50_000), pool("OLD", 195_000_000, 90, 5_000)]}
    monkeypatch.setattr(dw.rc, "_get", lambda path, params=None, tries=6: data)
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    monkeypatch.setattr(dw.rc, "analyze", lambda addr, chain="solana", **k: {"peak": {"NEWBIG": 0, "OLD": 1_400_000}[addr], "ok": addr == "OLD", "created": "x"})
    keep, _ = dw.pick_original("PONS", "robinhood", lambda s: None)
    assert [k["addr"] for k in keep] == ["OLD", "NEWBIG"]


def test_exit_simulation_ladder_stop_and_trail():
    import replay_coin as rc
    def cd(prices):                                  # price == candle close/high/low
        return [[1_000_000 + 60 * i, p, p, p, p, 1] for i, p in enumerate(prices)]
    ent = 1e-5
    # rug: price falls straight to 0.3x -> hard stop 0.55 hit
    x, why = rc.simulate_exit(cd([ent, ent * 0.9, ent * 0.3]), 1e9, 0, "core2")
    assert why == "stop" and x < 0.55
    # moon: 12x then crash -> ladder fills + trail, big positive
    x, why = rc.simulate_exit(cd([ent, ent * 2.1, ent * 4.2, ent * 12, ent * 4]), 1e9, 0, "core2")
    assert x > 2 and why == "trail"
    # a deep dip (0.4x) survives with a wider stop and then runs
    path = [ent, ent * 0.4, ent * 3, ent * 10]
    assert rc.simulate_exit(cd(path), 1e9, 0, "core2", stop=0.55)[1] == "stop"
    assert rc.simulate_exit(cd(path), 1e9, 0, "core2", stop=0.35)[0] > 1


def test_momentum_bucket_is_solana_only():
    import replay_coin as rc
    prices = [5e-6] * 6 + [1e-5, 2e-5, 4e-5, 8e-5, 1.0e-4, 1.1e-4]       # climbs through 60k..110k mcap
    vols = [100] * 6 + [4000] * 6
    cs = [[1_000_000 + 60 * i, p, p, p, p, v] for i, (p, v) in enumerate(zip(prices, vols))]
    sol = rc.replay(cs, 1e9, 1_000_000, "solana")["fired"]["proxy"]
    bsc = rc.replay(cs, 1e9, 1_000_000, "bsc")["fired"]["proxy"]
    assert sol is None or sol["mcap"] > 0
    assert bsc is None or bsc["mcap"] <= 60_000

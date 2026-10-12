import time

import pytest

import state
from layers import layer16_catalyst_radar as r

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def _pair(chain, addr, sym, liq, mcap, created_ms=None):
    return {"chainId": chain, "baseToken": {"address": addr, "symbol": sym}, "liquidity": {"usd": liq},
            "marketCap": mcap, "fdv": mcap, "pairCreatedAt": created_ms}


# ----------------------------------------------------------------- ladder
def test_classify_titles():
    assert r.classify_listing_title("Binance Will List MarsCoin (MARSCOIN) with Seed Tag Applied") == \
        {"stage": "spot", "tickers": ["MARSCOIN"]}
    c = r.classify_listing_title("Binance Futures Will Launch USDⓈ-M MARSCOIN Perpetual Contract With Up to 20x Leverage")
    assert c["stage"] == "futures" and c["tickers"] == ["MARSCOIN"]
    a = r.classify_listing_title("Binance Alpha Will Be the First Platform to Feature MarsCoin (MARSCOIN)")
    assert a["stage"] == "alpha"
    assert r.classify_listing_title("Binance Will Delist ABC (ABC) on 2026-10-01") is None
    assert r.classify_listing_title("Binance Will Add XYZ (XYZ) on Earn") is None
    assert r.classify_listing_title("Binance Completes Maintenance") is None
    assert r.classify_listing_title("") is None


def test_articles_both_shapes():
    a = {"data": {"articles": [{"title": "T", "code": "c1", "releaseDate": 5}]}}
    b = {"data": {"catalogs": [{"articles": [{"title": "T", "code": "c1", "releaseDate": 5}]}]}}
    assert r.articles_from_binance(a) == r.articles_from_binance(b)
    assert r.articles_from_binance(None) == [] and r.articles_from_binance({"data": None}) == []


def test_ladder_steps_and_silent_history():
    arts = [
        {"title": "Binance Alpha Will Feature Foo (FOO)", "code": "a", "release_ms": (NOW - 30 * 3600) * 1000},  # old
        {"title": "Binance Futures Will Launch USDⓈ-M FOO Perpetual Contract", "code": "b", "release_ms": (NOW - 600) * 1000},
        {"title": "Binance Will List Foo (FOO)", "code": "c", "release_ms": (NOW - 60) * 1000},
    ]
    ladder = {}
    ev = r.ladder_update(arts, ladder, NOW)
    assert [(e["stage"], e["prev_stage"]) for e in ev] == [("futures", "alpha"), ("spot", "futures")]
    assert ladder["FOO"]["stage"] == "spot"
    assert r.ladder_update(arts, ladder, NOW) == []          # no repeat events
    # a lower stage after a higher one is ignored
    assert r.ladder_update([{"title": "Binance Alpha Will Feature Foo (FOO)", "code": "z", "release_ms": NOW * 1000}], ladder, NOW) == []


def test_resolve_ticker_cases():
    big = [_pair("solana", "A", "MARS", 400_000, 90e6), _pair("solana", "A", "MARS", 100_000, 90e6)]
    assert r.resolve_ticker("MARS", lambda *a: {"pairs": big})["token"] == "A"
    # copycat with comparable liquidity -> ambiguous
    amb = big + [_pair("solana", "B", "MARS", 300_000, 1e6)]
    assert r.resolve_ticker("MARS", lambda *a: {"pairs": amb}) is None
    # tiny copycat is fine
    ok = big + [_pair("solana", "B", "MARS", 5_000, 1e5)]
    assert r.resolve_ticker("MARS", lambda *a: {"pairs": ok})["token"] == "A"
    # too little liquidity, wrong symbol, unsupported chain, nothing
    assert r.resolve_ticker("MARS", lambda *a: {"pairs": [_pair("solana", "A", "MARS", 50_000, 1e6)]}) is None
    assert r.resolve_ticker("MARS", lambda *a: {"pairs": [_pair("solana", "A", "OTHER", 900_000, 1e6)]}) is None
    assert r.resolve_ticker("MARS", lambda *a: {"pairs": [_pair("ethereum", "A", "MARS", 900_000, 1e6)]}) is None
    assert r.resolve_ticker("MARS", lambda *a: None) is None


def test_ladder_candidates_alpha_alert_only_futures_paper():
    fetch = lambda *a: {"pairs": [_pair("bsc", "0xabc", "FOO", 500_000, 120e6)]}
    ev = [{"id": "l:1:FOO", "ticker": "FOO", "stage": "alpha", "prev_stage": None, "title": "t"},
          {"id": "l:2:FOO", "ticker": "FOO", "stage": "futures", "prev_stage": "alpha", "title": "t"}]
    a, f = r.ladder_candidates(ev, fetch)
    assert a["paper"] is False and a["token"] is None
    assert f["paper"] is True and f["chain"] == "bsc" and f["token"] == "0xabc" and f["profile"] == "quick"
    huge = lambda *a: {"pairs": [_pair("bsc", "0xabc", "FOO", 500_000, 9e9)]}
    assert r.ladder_candidates(ev[1:], huge)[0]["paper"] is False
    none = r.ladder_candidates(ev[1:], lambda *a: None)[0]
    assert none["paper"] is False and "alert only" in none["reason"]


# ----------------------------------------------------------------- news-jack
RSS = """<rss><channel><item><title>Robinhood CEO Tenev answers AMC chief - Reuters</title></item>
<item><title>Markets close higher</title></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Tenev Robinhood stock tokens row</title></entry></feed>"""


def test_parse_feed_titles():
    assert r.parse_feed_titles(RSS)[0].startswith("Robinhood CEO")
    assert r.parse_feed_titles(ATOM) == ["Tenev Robinhood stock tokens row"]
    assert r.parse_feed_titles("<not xml") == [] and r.parse_feed_titles(None) == []


def test_extract_terms_strips_source_and_stopwords():
    t = r.extract_terms("Robinhood CEO Tenev answers AMC chief - Reuters")
    assert "robinhood" in t and "tenev" in t and "amc" in t
    assert "reuters" not in t and "the" not in t
    assert "bitcoin" not in r.extract_terms("Bitcoin rallies")  # generic crypto words excluded


def test_hot_terms_need_two_feeds_unless_trends():
    feeds = {"https://a.com/rss": ["Robinhood CEO Tenev answers"], "https://b.com/rss": ["Tenev defends Robinhood"],
             "https://c.com/rss": ["Zorblax unveiled"]}
    h = r.hot_terms(feeds)
    assert "tenev" in h and "robinhood" in h and "zorblax" not in h
    feeds["https://trends.google.com/trending/rss?geo=US"] = ["zorblax"]
    h = r.hot_terms(feeds)
    assert h["zorblax"]["trend"] is True and h["zorblax"]["score"] >= 3


def _coin(sym, liq, age_min, chain="solana", addr=None, h1=None, base=None):
    import datetime
    created = datetime.datetime.fromtimestamp((base if base is not None else NOW) - age_min * 60, tz=datetime.timezone.utc).isoformat()
    return {"name": f"{sym} / SOL", "address": addr or sym + "addr", "liquidity_usd": liq, "pool_created_at": created,
            "chain": chain, "market_cap_usd": liq * 5, "change_h1": h1}


def test_match_new_coins_filters():
    hot = {"tenev": {"score": 5, "trend": True, "feeds": 2}, "robinhood chain": {"score": 3, "trend": False, "feeds": 3}}
    coins = [_coin("TENEV", 20_000, 90), _coin("TENEV", 20_000, 20 * 60), _coin("TENEV", 500, 30),
             _coin("OTHER", 50_000, 30), _coin("ROBINHOODCHAIN", 9_000, 40)]
    m = r.match_new_coins(hot, coins, NOW)
    assert [x["coin"]["name"] for x in m] == ["TENEV / SOL", "ROBINHOODCHAIN / SOL"]
    assert m[0]["term"] == "tenev" and m[1]["term"] == "robinhood chain"


def test_leaders_and_clones():
    hot = {"tenev": {"score": 5, "trend": True, "feeds": 2}}
    coins = [_coin("TENEV", 20_000, 90, addr="x1"), _coin("TENEV", 80_000, 60, addr="x2"), _coin("TENEV", 9_000, 10, addr="x3")]
    cands = r.news_candidates(r.match_new_coins(hot, coins, NOW))
    by = {c["token"]: c for c in cands}
    assert by["x2"]["paper"] is True and by["x1"]["paper"] is False and by["x3"]["paper"] is False
    assert "CLONE" in by["x1"]["reason"] and by["x2"]["score"] > by["x1"]["score"]


def test_cross_chain_copies():
    b = time.time()
    coins = [_coin("TENEV", 10_000, 600, "bsc", base=b), _coin("TENEV", 10_000, 30, "solana", base=b),
             _coin("LONE", 10_000, 30, "solana", base=b),
             _coin("OLD", 10_000, 5000, "bsc", base=b), _coin("OLD", 10_000, 10, "solana", base=b)]
    out = r.cross_chain_copies(coins, now=b)
    assert set(out) == {"TENEV"} and out["TENEV"][0]["chain"] == "bsc"


# ----------------------------------------------------------------- launchpad / buyback
def test_launchpad_surges():
    rows = [{"name": "Pons", "category": "Launchpad", "total24h": 6_000_000, "change_1d": 180, "change_7d": 900, "symbol": "PONS"},
            {"name": "SleepyPump", "category": "Dexs", "total24h": 100_000, "change_1d": 300, "change_7d": 10},
            {"name": "Tiny.fun", "category": "Launchpad", "total24h": 1_000, "change_1d": 900},
            {"name": "SteadyLaunch", "category": "Launchpad", "total24h": 500_000, "change_1d": 5, "change_7d": 10},
            {"name": "Uniswap", "category": "Dexs", "total24h": 9e6, "change_1d": 500}]
    s = r.launchpad_surges(rows)
    assert [x["name"] for x in s] == ["Pons", "SleepyPump"]
    c = r.launchpad_candidates(s[:1], lambda *a: {"pairs": [_pair("base", "0xp", "PONS", 800_000, 250e6)]})[0]
    assert c["paper"] is True and c["chain"] == "base"
    c2 = r.launchpad_candidates(s[1:], lambda *a: None)[0]
    assert c2["paper"] is False and "launchpad surge" in c2["reason"]


def test_buyback_proximity():
    coins = [{"symbol": f"C{i}", "address": f"a{i}", "mcap_usd": 1_000_000 - i * 10_000, "volume_h1": 10, "volume_h24": 240}
             for i in range(30)]
    coins[13]["volume_h1"] = 50      # rank 14, hot
    coins[2]["volume_h1"] = 50       # rank 3, outside window
    coins[16]["volume_h1"] = 5       # rank 17, not hot
    out = r.buyback_proximity(coins, k=15)
    assert [c["symbol"] for c in out] == ["C13"] and out[0]["rank"] == 14


# ----------------------------------------------------------------- regime
def test_regime_states_and_multiplier(monkeypatch):
    samples = [{"ts": i, "n_liquid": 10 + i % 5, "green": 0.4 + (i % 5) * 0.02} for i in range(40)]
    assert r.regime_state(samples[:5])["state"] == "unknown"
    hot = samples + [{"ts": 99, "n_liquid": 60, "green": 0.9}]
    cold = samples + [{"ts": 99, "n_liquid": 1, "green": 0.05}]
    assert r.regime_state(hot)["state"] == "hot" and r.regime_state(cold)["state"] == "cold"
    monkeypatch.delenv("REGIME_GATE_ENABLED", raising=False)
    state.set_value(r.REGIME_KEY, cold)
    assert r.regime_stake_multiplier() == 1.0                      # gate off by default
    monkeypatch.setenv("REGIME_GATE_ENABLED", "true")
    assert r.regime_stake_multiplier() == 0.5
    state.set_value(r.REGIME_KEY, hot)
    assert r.regime_stake_multiplier() == 1.0


def test_record_regime_spacing():
    state.set_value(r.REGIME_KEY, [])
    r.record_regime({"ts": 1000.0, "n_liquid": 1, "green": 0.5})
    r.record_regime({"ts": 1010.0, "n_liquid": 2, "green": 0.5})   # too soon
    r.record_regime({"ts": 1500.0, "n_liquid": 3, "green": 0.5})
    assert [s["n_liquid"] for s in state.get_value(r.REGIME_KEY)] == [1, 3]


# ----------------------------------------------------------------- conviction
def test_conviction_and_candidates():
    buys = [{"wallet": "S1", "sol_delta": 6.0}, {"wallet": "S2", "sol_delta": 1.0}, {"wallet": "X", "sol_delta": 50}]
    c = r.conviction(buys, ["S1", "S2"])
    assert c["fires"] and c["wallets"] == ["S1"] and c["total_sol"] == 6.0
    assert not r.conviction(buys, ["S2"])["fires"]
    arch = {"M1": {"first_ts": 1, "buys": [{"w": "S1", "t": 1, "sol": 7}]}, "M2": {"first_ts": 1, "buys": [{"w": "S1", "t": 1, "sol": 0.5}]}}
    fires = r.conviction_scan(arch, ["S1"])
    assert [f["mint"] for f in fires] == ["M1"]
    pairs = {"M1": {"baseToken": {"symbol": "ZED"}, "marketCap": 800_000, "liquidity": {"usd": 40_000}}}
    cand = r.conviction_candidates(fires, lambda addrs: pairs)[0]
    assert cand["paper"] and cand["symbol"] == "ZED" and cand["chain"] == "solana"
    pairs["M1"]["marketCap"] = 50_000_000
    assert r.conviction_candidates(fires, lambda addrs: pairs) == []
    assert r.conviction_candidates([], lambda addrs: pairs) == []


# ----------------------------------------------------------------- output
def test_format_alert_clean():
    c = {"kind": "news", "symbol": "A*B_C`", "chain": "solana", "token": "Tok1", "reason": "x_y [z]", "mcap": 1e6,
         "liquidity": 5e4, "paper": True}
    s = r.format_alert(c, {"state": "hot"})
    assert "CATALYST RADAR" in s and "A B C" in s and "[Market: hot]" in s and "[PAPER trade opened]" in s
    assert "_" not in s.split("\n")[2] and "*" not in s.split("\n")[1]


def test_open_paper_for_and_dedupe():
    c = {"kind": "ladder", "symbol": "F", "chain": "solana", "token": "TK", "mcap": 1e6, "liquidity": 5e4, "paper": True,
         "profile": "quick", "score": 90}
    pos = r.open_paper_for(c)
    assert pos and pos["id"] == "radar_ladder:solana:TK" and pos["strategy"] == "quick"
    assert r.open_paper_for(c) is None                          # already open
    assert r.open_paper_for({**c, "paper": False}) is None
    assert r.open_paper_for({**c, "mcap": None, "token": "T2"}) is None


# ----------------------------------------------------------------- full cycle
def _fakes(monkeypatch):
    monkeypatch.setenv("RADAR_NEWS_FEEDS", "https://a.com/rss,https://b.com/rss")
    monkeypatch.setenv("RADAR_CHAINS", "solana")
    art = {"data": {"articles": [{"title": "Binance Futures Will Launch USDⓈ-M FOO Perpetual Contract", "code": "b1",
                                  "releaseDate": (time.time() - 120) * 1000}]}}
    fees = {"protocols": [{"name": "NewPad.fun", "category": "Launchpad", "total24h": 800_000, "change_1d": 400, "change_7d": 50}]}

    def fetch_json(url, params=None):
        if "binance" in url:
            return art
        if "llama" in url:
            return fees
        if "dexscreener" in url:
            return {"pairs": [_pair("solana", "FOOTOKEN", "FOO", 600_000, 80e6)]} if (params or {}).get("q") == "FOO" else {"pairs": []}
        return None

    feeds = {"https://a.com/rss": "<rss><channel><item><title>Tenev Robinhood fight</title></item></channel></rss>",
             "https://b.com/rss": "<rss><channel><item><title>Robinhood Tenev replies</title></item></channel></rss>"}
    import datetime
    created = datetime.datetime.fromtimestamp(time.time() - 1800, tz=datetime.timezone.utc).isoformat()
    coins = [{"name": "TENEV / SOL", "address": "TENEVADDR", "liquidity_usd": 30_000, "market_cap_usd": 400_000,
              "pool_created_at": created, "chain": "solana", "change_h1": 40}]
    return fetch_json, (lambda u: feeds.get(u)), (lambda c: list(coins))


def test_run_cycle_end_to_end_and_dedupe(monkeypatch):
    fj, ft, cf = _fakes(monkeypatch)
    sent = []
    out = r.run_cycle(lambda m: sent.append(m) or {"sent": True}, fetch_json=fj, fetch_text=ft, coins_fn=cf,
                      dex_batch=lambda a: {}, now=time.time())
    assert out["errors"] == [], out
    kinds = " ".join(sent)
    assert "LISTING LADDER" in kinds and "NEWS-JACK" in kinds and "LAUNCHPAD SURGE" in kinds
    assert out["alerts"] == 3 and out["paper"] >= 2
    from executor import paper_ledger
    ids = set(paper_ledger._open())
    assert "radar_ladder:solana:FOOTOKEN" in ids and "radar_news:solana:TENEVADDR" in ids
    # second pass: nothing new -> no repeat alerts
    sent2 = []
    out2 = r.run_cycle(lambda m: sent2.append(m), fetch_json=fj, fetch_text=ft, coins_fn=cf, dex_batch=lambda a: {}, now=time.time())
    assert sent2 == [] and out2["alerts"] == 0


def test_run_cycle_survives_every_failure(monkeypatch):
    monkeypatch.setenv("RADAR_NEWS_FEEDS", "https://a.com/rss")
    monkeypatch.setenv("RADAR_CHAINS", "solana,bsc")

    def boom(*a, **k):
        raise RuntimeError("network down")
    sent = []
    out = r.run_cycle(lambda m: sent.append(m), fetch_json=boom, fetch_text=boom, coins_fn=boom,
                      dex_batch=boom, now=time.time())
    assert out["alerts"] == 0 and sent == []
    assert out["errors"], "failures must be recorded, not raised"


def test_run_cycle_alert_cap(monkeypatch):
    fj, ft, cf = _fakes(monkeypatch)
    monkeypatch.setenv("RADAR_MAX_ALERTS_PER_CYCLE", "1")
    sent = []
    out = r.run_cycle(lambda m: sent.append(m), fetch_json=fj, fetch_text=ft, coins_fn=cf, dex_batch=lambda a: {}, now=time.time())
    assert out["alerts"] == 1 and len(sent) == 1


def test_run_cycle_daily_cap(monkeypatch):
    fj, ft, cf = _fakes(monkeypatch)
    monkeypatch.setenv("RADAR_MAX_ALERTS_PER_DAY", "2")
    sent = []
    r.run_cycle(lambda m: sent.append(m), fetch_json=fj, fetch_text=ft, coins_fn=cf, dex_batch=lambda a: {}, now=time.time())
    assert len(sent) == 2                                    # 3 candidates, 2 allowed today
    out = r.run_cycle(lambda m: sent.append(m), fetch_json=fj, fetch_text=ft, coins_fn=cf, dex_batch=lambda a: {}, now=time.time())
    assert len(sent) == 2 and out["alerts"] == 0 and any("daily alert cap" in n for n in out["notes"])

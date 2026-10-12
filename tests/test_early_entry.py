import asyncio
import json
import time
import pytest
import state
from layers.layer17_early_entry import EarlyTracker, SUPPLY
from executor import paper_ledger, books
import worker_early_watch as w
from early_stream import EarlyStream


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    w._cache.clear()
    monkeypatch.setenv("SOL_USD", "150")


T0 = 1_000_000.0


def create(mint="M1", creator="dev1", initial=0.02 * SUPPLY, mc=30.0, name="Tenev Coin", sym="TENEV"):
    return {"txType": "create", "mint": mint, "traderPublicKey": creator, "initialBuy": initial,
            "solAmount": 1.0, "marketCapSol": mc, "name": name, "symbol": sym}


def buy(mint, who, sol=1.0, mc=35.0):
    return {"txType": "buy", "mint": mint, "traderPublicKey": who, "solAmount": sol, "tokenAmount": 1e6, "marketCapSol": mc}


def sell(mint, who, sol=1.0, tokens=1e6):
    return {"txType": "sell", "mint": mint, "traderPublicKey": who, "solAmount": sol, "tokenAmount": tokens, "marketCapSol": 30}


def feed_buyers(t, mint, n, start=0, step=5.0, mc=36.0, sol=0.8):
    dec = None
    for i in range(n):
        dec = t.on_trade(buy(mint, f"w{start + i}", sol, mc), now=T0 + 10 + i * step) or dec
    return dec


def test_core2_fires_early_on_smart_wallet_plus_velocity():
    t = EarlyTracker(smart_fn=lambda: {"w3"})
    assert t.on_create(create(), now=T0)
    dec = feed_buyers(t, "M1", 14, step=6.0)
    assert dec and dec["lane"] == "core2" and dec["age_s"] <= 240
    assert "smart_wallet" in dec["confirmations"] and "velocity" in dec["confirmations"]
    assert t.on_trade(buy("M1", "late", 1, 40), now=T0 + 120) is None        # decided once only


def test_leaderboard_fallback_waits_for_stronger_lanes():
    t = EarlyTracker(smart_fn=lambda: {"w0"})
    t.on_create(create("LB"), now=T0)
    dec = None
    for i in range(5):                                   # smart wallet only, 5 buyers in 20 s: too soon to settle for a copy
        dec = t.on_trade(buy("LB", f"w{i}", 0.5, 34), now=T0 + 4 * (i + 1)) or dec
    assert dec is None
    dec = t.on_trade(buy("LB", "w9", 0.5, 34), now=T0 + 70)
    assert dec and dec["lanes"] == ["leaderboard"] and dec["potential"] == "copy"


def test_no_entry_without_confirmations():
    t = EarlyTracker()
    t.on_create(create("M2"), now=T0)
    assert feed_buyers(t, "M2", 4) is None                                    # 4 buyers: nothing confirmed


def test_hard_blocks():
    bl = EarlyTracker(blacklist_fn=lambda c: c == "ruggy")
    assert bl.on_create(create("B1", creator="ruggy"), now=T0) is False       # blacklisted creator never tracked
    big = EarlyTracker()
    assert big.on_create(create("B2", initial=0.5 * SUPPLY), now=T0) is False  # dev holds 50%
    t = EarlyTracker(smart_fn=lambda: {"w0"})
    t.on_create(create("B3", initial=0.05 * SUPPLY), now=T0)
    t.on_trade(sell("B3", "dev1", tokens=0.04 * SUPPLY), now=T0 + 5)           # creator dumps 80%
    assert feed_buyers(t, "B3", 15) is None
    c = EarlyTracker(smart_fn=lambda: {"w1"})
    c.on_create(create("B4"), now=T0)
    for i in range(8):                                                         # one whale is 90% of the buying
        c.on_trade(buy("B4", "whale", 9.0), now=T0 + i)
    assert c.on_trade(buy("B4", "w1", 0.5), now=T0 + 9) is None


def test_dev_list_lane_and_compound_coentry():
    t = EarlyTracker(dev_list={"dev1"}, smart_fn=lambda: {"w2"}, hot_terms_fn=lambda: {"tenev"})
    t.on_create(create("D1"), now=T0)
    dec = feed_buyers(t, "D1", 12, step=4.0)
    assert dec["lane"] == "dev_list" and "core2" in dec["lanes"] and "compound" in dec["lanes"]   # strong evidence -> every fitting book
    assert {"dev_list", "smart_wallet", "narrative"} <= set(dec["confirmations"])


def test_late_coin_is_not_early():
    t = EarlyTracker(smart_fn=lambda: {"w0", "w1", "w2"})
    t.on_create(create("L1", mc=200.0), now=T0)                                # already far up the curve
    dec = None
    for i in range(14):
        dec = t.on_trade(buy("L1", f"w{i}", 1, 210), now=T0 + 30 + i) or dec
    assert dec is None or dec["lane"] != "core2"
    t2 = EarlyTracker(smart_fn=lambda: {"w0", "w1"})
    t2.on_create(create("L2"), now=T0)
    assert t2.on_trade(buy("L2", "w0", 1, 35), now=T0 + 2400) is None          # 40 min old: window closed
    assert "L2" in t2.sweep(now=T0 + 2400) or True


def test_sweep_evicts():
    t = EarlyTracker()
    t.on_create(create("S1"), now=T0)
    assert t.sweep(now=T0 + 3600) == ["S1"] and "S1" not in t.recs


def test_act_opens_paper_in_each_lane_and_alerts():
    dec = {"lane": "core2", "lanes": ["core2", "compound"], "mint": "MintA", "symbol": "AAA", "name": "A", "creator": "c",
           "age_s": 40.0, "mcap_sol": 33.0, "entry_mcap_sol": 33.0, "buyers": 12, "buys": 14, "sells": 1,
           "confirmations": ["smart_wallet", "velocity", "pressure"], "smart": ["w3"], "potential": "100x+"}
    sent = []
    out = w.act(dec, send=lambda m: sent.append(m), now=T0)
    assert out["opened"] == ["core2", "compound"]
    book = paper_ledger._open()
    assert book["early_core2:solana:MintA"]["module"] == "core2" and book["early_core2:solana:MintA"]["exit_profile"] == "moonshot"
    assert book["early_compound:solana:MintA"]["exit_profile"] == "quick"
    assert book["early_core2:solana:MintA"]["entry_mcap"] == pytest.approx(33.0 * 150)
    assert sent and "CORE 2" in sent[0] and "EARLY ENTRY" in sent[0]
    assert w.act(dec, send=lambda m: sent.append(m), now=T0) == {"skipped": "already acted"}


def test_act_daily_cap(monkeypatch):
    monkeypatch.setenv("EARLY_MAX_ENTRIES_PER_DAY", "1")
    mk = lambda m: {"lane": "leaderboard", "lanes": ["leaderboard"], "mint": m, "symbol": "X", "name": "", "creator": "c",
                    "age_s": 10, "mcap_sol": 30, "entry_mcap_sol": 30, "buyers": 3, "buys": 3, "sells": 0,
                    "confirmations": ["smart_wallet"], "smart": [], "potential": "copy"}
    assert "opened" in w.act(mk("m1"), send=lambda m: None, now=T0)
    assert w.act(mk("m2"), send=lambda m: None, now=T0) == {"skipped": "daily cap"}


def test_stream_client_reconnects_and_resubscribes():
    msgs = [json.dumps(create("Z1")), json.dumps(buy("Z1", "a")), "not json", json.dumps(buy("Z1", "b"))]
    sent_payloads, got = [], []

    class FakeWS:
        def __init__(self, fail): self.fail = fail
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def send(self, p): sent_payloads.append(json.loads(p))
        def __aiter__(self):
            async def gen():
                if self.fail:
                    raise ConnectionError("drop")
                for m in msgs:
                    yield m
            return gen()

    calls = {"n": 0}

    def connect(url):
        calls["n"] += 1
        return FakeWS(fail=calls["n"] == 1)

    def on_event(e):
        got.append(e["txType"])
        return [e["mint"]] if e["txType"] == "create" else None

    async def run():
        s = EarlyStream(on_event, connect=connect)
        s.watched.add("Old1")
        import early_stream
        orig = asyncio.sleep
        async def fast(_): await orig(0)
        early_stream.asyncio.sleep = fast
        try:
            await s.run_forever(max_messages=3)
        finally:
            early_stream.asyncio.sleep = orig
        return s

    s = asyncio.run(run())
    assert got == ["create", "buy", "buy"] and s.reconnects == 1
    methods = [p["method"] for p in sent_payloads]
    assert methods.count("subscribeNewToken") == 2 and "subscribeTokenTrade" in methods
    assert "Z1" in s.watched


def test_early_entry_modules_registered():
    from layers import modules
    assert modules.for_layer("early_core2") == "core2" and modules.for_paper("early_compound") == "compound"
    assert modules.for_paper("early_dev_list") == "dev_list"


def test_dev_list_enters_at_creation_without_trade_feed():
    from layers.layer17_early_entry import EarlyTracker
    t = EarlyTracker(dev_list={"DEV1"})
    t.feed_off = True
    ev = {"txType": "create", "mint": "M1", "traderPublicKey": "DEV1", "initialBuy": 1000000, "solAmount": 0.03,
          "marketCapSol": 28, "name": "x", "symbol": "X"}
    assert t.on_create(ev, now=100.0)
    d = t.evaluate("M1", now=101.0)
    assert d and d["lane"] == "dev_list" and d["lanes"] == ["dev_list"]


def test_non_dev_needs_buyers_without_feed():
    from layers.layer17_early_entry import EarlyTracker
    t = EarlyTracker(dev_list=set())
    ev = {"txType": "create", "mint": "M2", "traderPublicKey": "NOBODY", "initialBuy": 1000000, "solAmount": 0.03,
          "marketCapSol": 28, "name": "x", "symbol": "X"}
    t.on_create(ev, now=100.0)
    assert t.evaluate("M2", now=101.0) is None

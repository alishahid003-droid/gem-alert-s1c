import pytest
import state
import multichain_sniper as m


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    monkeypatch.setattr(m, "evm_safe", lambda c, a: (True, "scanned ok"))


def _pool(addr="0xabc", created_ago=300, now=10_000.0, **kw):
    p = {"chain": "base", "addr": addr, "name": "X", "src": "gt", "dex": "uniswap", "mcap": 50_000.0, "liq": 9_000.0,
         "created": now - created_ago, "buys5": 30, "sells5": 10, "buyers5": 20, "vol5": 6000.0, "chg5": 40.0, "chg1h": 60.0}
    p.update(kw)
    return p


def test_parse_pools():
    body = {"data": [{"attributes": {"market_cap_usd": "12000", "reserve_in_usd": "4000", "pool_created_at": "2026-10-13T00:00:00Z",
                                       "name": "AA / WETH", "transactions": {"m5": {"buys": 5, "sells": 2, "buyers": 4}},
                                       "volume_usd": {"m5": "900"}, "price_change_percentage": {"m5": "20", "h1": "30"}},
                      "relationships": {"base_token": {"data": {"id": "base_0xabc"}}, "dex": {"data": {"id": "uniswap"}}}}]}
    out = m.parse_pools(body, "base")
    assert out[0]["addr"] == "0xabc" and out[0]["buys5"] == 5 and out[0]["chg5"] == 20.0


def test_strong_pool_enters_all_lanes_and_weak_does_not():
    sc = m.Scanner()
    d = sc.evaluate(_pool(), 10_000.0)
    assert d and "core2" in d["lanes"] and "sprint" in d["lanes"]
    assert sc.evaluate(_pool(addr="0xdead", buys5=3, buyers5=2, vol5=100.0, chg5=1.0, created_ago=3000), 10_000.0) is None
    assert sc.evaluate(_pool(addr="0xpump", chg5=900.0), 10_000.0) is None      # already exploded
    assert sc.evaluate(_pool(addr="0xdump", buys5=10, sells5=40), 10_000.0) is None


def test_stonkfun_needs_two_confirmations():
    sc = m.Scanner()
    p = {"chain": "solana", "addr": "MintStonk", "name": "S", "src": "stonkfun", "mcap": 20_000.0, "liq": 5000.0,
         "created": 9_900.0, "vol24": 5000.0, "grad": 0.4, "buys5": None, "sells5": None, "buyers5": None,
         "vol5": None, "chg5": None, "chg1h": None}
    d = sc.evaluate(p, 10_000.0)
    assert d and d["src"] == "stonkfun"


def test_act_opens_paper_positions_on_non_solana_chain():
    d = m.Scanner().evaluate(_pool(), 10_000.0)
    r = m.act(d, 10_000.0)
    assert r.get("opened"), r
    assert m.act(d, 10_001.0) == {"skipped": "already acted"}


def test_solana_momentum_bucket_above_cap_only_core1_and_needs_4_conf():
    sc = m.Scanner()
    big = _pool(addr="SolBig", chain="solana", src="gt", mcap=150_000.0)
    d = sc.evaluate(big, 10_000.0)
    assert d and d["lanes"] == ["core1"] and "momentum" in d["confirmations"]
    # EVM above the cap is still refused
    assert sc.evaluate(_pool(addr="0xbig", chain="bsc", mcap=150_000.0), 10_000.0) is None
    # above the momentum ceiling is refused too
    assert sc.evaluate(_pool(addr="SolHuge", chain="solana", mcap=900_000.0), 10_000.0) is None

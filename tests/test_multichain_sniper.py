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


def test_robinhood_chain_requires_50k_entry_mcap():
    """28 Oct 2026 (Ali): full systematic audit (every confirmation tag, every pairwise
    combination, bucketed per chain) of robinhood_chain's 163 all-time multichain-origin
    trades, by entry_mcap bucket:
      <5k: 0.0% win  5-10k: 5.6%  10-20k: 14.8%  20-50k: 39.1%  >=50k: 48.3% win, net +$230.87
    >=50k is the ONLY profitable filtered slice found anywhere in this audit for this chain
    (every confirmation-tag-based filter tried first turned out to be a base-chain effect with
    no edge on robinhood_chain once isolated -- see multichain_sniper.py's retired-gate
    comment). Below 50k entry mcap, robinhood_chain trades are refused outright."""
    sc = m.Scanner()
    assert sc.evaluate(_pool(addr="0xrh49k", chain="robinhood_chain", mcap=49_999.0), 10_000.0) is None
    d = sc.evaluate(_pool(addr="0xrh50k", chain="robinhood_chain", mcap=50_000.0), 10_000.0)
    assert d and d["chain"] == "robinhood_chain"


def test_robinhood_chain_mcap_floor_env_override(monkeypatch):
    monkeypatch.setenv("MC_RHC_MIN_MCAP_USD", "10000")
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh12k", chain="robinhood_chain", mcap=12_000.0), 10_000.0)
    assert d and d["chain"] == "robinhood_chain"


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


def test_act_records_raw_numeric_snapshot_on_the_position(monkeypatch, tmp_path):
    """28 Oct 2026 (Ali): today's full pattern audit hit a wall because the raw numeric fields
    (buys5/sells5/buyers5/vol5/chg5) that feed the entry decision never survived onto the
    closed trade record -- only the boolean confirmation tags did. act() now threads a 'raw'
    snapshot through tags so a future audit can test continuous values, not just booleans."""
    import executor.paper_ledger as pl
    monkeypatch.setattr(pl.state, "LOCAL_STATE_FILE", str(tmp_path / "pl.json"), raising=False)
    d = m.Scanner().evaluate(_pool(), 10_000.0)
    r = m.act(d, 10_000.0)
    assert r.get("opened"), r
    open_book = pl._open()
    pos = next(iter(open_book.values()))
    assert pos["tags"]["raw"] == {"buys5": 30, "sells5": 10, "buyers5": 20, "vol5": 6000.0,
                                   "vol24": None, "chg5": 40.0, "chg1h": 60.0}


def test_solana_momentum_bucket_above_cap_only_core1_and_needs_4_conf():
    sc = m.Scanner()
    big = _pool(addr="SolBig", chain="solana", src="gt", mcap=150_000.0)
    d = sc.evaluate(big, 10_000.0)
    assert d and d["lanes"] == ["core1"] and "momentum" in d["confirmations"]
    # EVM above the cap is still refused
    assert sc.evaluate(_pool(addr="0xbig", chain="bsc", mcap=150_000.0), 10_000.0) is None
    # above the momentum ceiling is refused too
    assert sc.evaluate(_pool(addr="SolHuge", chain="solana", mcap=1_500_000.0), 10_000.0) is None


def test_big_launch_and_robinhood_lanes_are_probe_only():
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="SolBig2", chain="solana", mcap=600_000.0), 10_000.0)
    assert d and d["lanes"] == ["core1"] and d["tier"] == "probe" and "biglaunch" in d["confirmations"]
    r = sc.evaluate(_pool(addr="0xrh", chain="robinhood", mcap=180_000.0), 10_000.0)
    assert r and r["lanes"] == ["core1"] and r["tier"] == "probe"
    assert sc.evaluate(_pool(addr="0xrh2", chain="robinhood", mcap=400_000.0), 10_000.0) is None
    assert sc.evaluate(_pool(addr="0xbsc", chain="bsc", mcap=180_000.0), 10_000.0) is None


def test_tier_of():
    assert m.tier_of(["a", "b", "c"]) == "probe"
    assert m.tier_of(["a", "b", "c", "d"]) == "standard"
    assert m.tier_of(["buyers", "pressure", "c", "d", "e", "f"]) == "conviction"
    assert m.tier_of(["a", "b", "c", "d", "e", "f"]) == "standard"


def test_disabled_chains_default_excludes_bsc_base():
    """28 Oct 2026 (Ali): full-history data (1,023 early-sniper trades) showed bsc at a flat 0%
    win rate (37/37 losses, -$1,073), and base losing money overall despite a higher win rate
    than solana because >55% of its trades die unpriced before ever getting a chance to win.
    robinhood_chain had the SAME dead-on-arrival problem but was re-enabled once the actual
    cause was fixed (layers/layer14_revival's GeckoTerminal fallback, same day) -- of the
    robinhood_chain trades that DID get priced, 57.6% won and the chain was net profitable. Only
    bsc/base remain disabled on their own merits."""
    assert m.DISABLED_CHAINS == {"bsc", "base"}
    slugs = {c: s for c, s in m.GT_SLUGS.items() if c not in m.DISABLED_CHAINS}
    assert slugs == {"solana": "solana"}


def test_disabled_chains_env_override(monkeypatch):
    """A chain can be re-enabled later (if its numbers improve) by removing it from the env
    override, without a code change."""
    monkeypatch.setenv("MC_DISABLED_CHAINS", "bsc")
    import importlib
    m2 = importlib.reload(m)
    try:
        assert m2.DISABLED_CHAINS == {"bsc"}
        slugs = {c: s for c, s in m2.GT_SLUGS.items() if c not in m2.DISABLED_CHAINS}
        assert slugs == {"solana": "solana", "base": "base"}
    finally:
        monkeypatch.delenv("MC_DISABLED_CHAINS", raising=False)
        importlib.reload(m)

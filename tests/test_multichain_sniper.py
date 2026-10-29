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


def _base_pool(addr="0xabc", now=10_000.0):
    """Helper: a base-chain pool that earns growth + volume -- the Oct 28 2026 base gate's
    requirement (see test_base_requires_growth_and_volume below). 'growth' needs the SAME
    scanner instance to have first seen this pool >=40s ago at a lower mcap (now - t0 >= 40
    and mcap >= 1.25 * m0), so this does two evaluate() calls on one Scanner."""
    sc = m.Scanner()
    first_seen = _pool(addr=addr, now=now, mcap=40_000.0)
    sc.evaluate(dict(first_seen), now)   # records self.first[k], too soon for growth, discarded
    later = dict(first_seen, mcap=60_000.0)   # 1.5x the first-seen mcap -> growth-eligible
    return sc, later, now + 45                # 45s later -> satisfies growth's time window


def test_parse_pools():
    body = {"data": [{"attributes": {"market_cap_usd": "12000", "reserve_in_usd": "4000", "pool_created_at": "2026-10-13T00:00:00Z",
                                       "name": "AA / WETH", "transactions": {"m5": {"buys": 5, "sells": 2, "buyers": 4}},
                                       "volume_usd": {"m5": "900"}, "price_change_percentage": {"m5": "20", "h1": "30"}},
                      "relationships": {"base_token": {"data": {"id": "base_0xabc"}}, "dex": {"data": {"id": "uniswap"}}}}]}
    out = m.parse_pools(body, "base")
    assert out[0]["addr"] == "0xabc" and out[0]["buys5"] == 5 and out[0]["chg5"] == 20.0


def test_strong_pool_enters_all_lanes_and_weak_does_not():
    sc, later, t2 = _base_pool()
    d = sc.evaluate(later, t2)
    assert d and "core2" in d["lanes"] and "sprint" in d["lanes"]
    sc2 = m.Scanner()
    assert sc2.evaluate(_pool(addr="0xdead", buys5=3, buyers5=2, vol5=100.0, chg5=1.0, created_ago=3000), 10_000.0) is None
    assert sc2.evaluate(_pool(addr="0xpump", chg5=900.0), 10_000.0) is None      # already exploded
    assert sc2.evaluate(_pool(addr="0xdump", buys5=10, sells5=40), 10_000.0) is None


def test_base_requires_growth_and_volume():
    """28 Oct 2026 (Ali): asked to implement base's identified pattern as a real gate rather
    than leave it disabled. growth+volume together on base is a clean, large-n signal (n=24
    all-time): 91.7% win rate, +$493.02 net -- vs everything else on base (n=48): 8.3% win
    rate, -$1,538.37 net. base is re-enabled (see test_disabled_chains_default_excludes_bsc)
    gated on this."""
    sc = m.Scanner()
    # Clears the old len(conf)>=3 bar easily but has no growth yet (single snapshot).
    assert sc.evaluate(_pool(), 10_000.0) is None


def test_robinhood_chain_requires_30k_entry_mcap():
    """28 Oct 2026 (Ali): re-audited after ~50hrs/185 trades/130 unique coins accumulated,
    because the original >=$50k floor's win rate was visibly eroding as more trades came in
    (56.3% -> 54.5% -> 40.0% across three checkpoints). Re-bucketing on the larger sample found
    the >=50k floor was cutting out a BETTER-performing band -- unique-coin win rate by mcap
    band, no-biglaunch, any age: $0-30k: 11.2% win (bad)  $30-50k: 55.0% win  $50-70k: 39.3% win.
    Floor lowered to $30k: n=71 trades/48 coins, 45.1% win trade-level, net +$220.82 -- bigger
    and more trustworthy than the eroded >=50k slice. Below 30k entry mcap, robinhood_chain
    trades are refused outright."""
    sc = m.Scanner()
    assert sc.evaluate(_pool(addr="0xrh29k", chain="robinhood_chain", mcap=29_999.0), 10_000.0) is None
    d = sc.evaluate(_pool(addr="0xrh30k", chain="robinhood_chain", mcap=30_000.0), 10_000.0)
    assert d and d["chain"] == "robinhood_chain"


def test_robinhood_chain_mcap_floor_env_override(monkeypatch):
    monkeypatch.setenv("MC_RHC_MIN_MCAP_USD", "10000")
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh12k", chain="robinhood_chain", mcap=12_000.0), 10_000.0)
    assert d and d["chain"] == "robinhood_chain"


def test_robinhood_chain_age_floor_disabled_by_default():
    """28 Oct 2026 (Ali, later same day): the age>=2min gate found earlier today was specific
    to the old $50k-only bucket and did NOT generalize once the mcap floor was corrected to
    $30k -- re-tested on the larger $30k+ bucket (n=48 coins): age>=0min (no gate) gives 45.8%
    win/+$220.82, age>=2min shrinks to n=34 coins for a lower 44.1% win/+$163.06. Age gating
    retired (default floor is now 0.0 = disabled) rather than deleted, since the mechanism
    (MC_RHC_MIN_AGE_MIN) stays available if a future, larger sample finds a real effect again.
    A very young robinhood_chain trade at the new $30k floor should pass through."""
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrhyoung", chain="robinhood_chain", mcap=60_000.0, created_ago=90), 10_000.0)
    assert d and d["chain"] == "robinhood_chain"


def test_robinhood_chain_age_floor_env_override(monkeypatch):
    monkeypatch.setenv("MC_RHC_MIN_AGE_MIN", "2.0")
    sc = m.Scanner()
    # With the override restoring a 2min floor, a 90s-old (1.5min) trade is refused again.
    assert sc.evaluate(_pool(addr="0xrhquick", chain="robinhood_chain", mcap=60_000.0, created_ago=90), 10_000.0) is None
    d = sc.evaluate(_pool(addr="0xrhold", chain="robinhood_chain", mcap=60_000.0, created_ago=130), 10_000.0)
    assert d and d["chain"] == "robinhood_chain"


def test_stonkfun_blocked_by_default_solana_gate(monkeypatch):
    """29 Oct 2026 (Ali): StonkFun is solana-sourced, so it's covered by the same blanket
    solana-off-by-default gate as every other solana source here -- it has never actually
    closed a trade through this file (0 ever recorded), so there's no evidence to exempt it.
    Confirms it clears its own 2-confirmation bar but is still refused by the chain gate; the
    env override test below confirms it WOULD pass if solana were re-enabled."""
    sc = m.Scanner()
    p = {"chain": "solana", "addr": "MintStonk", "name": "S", "src": "stonkfun", "mcap": 20_000.0, "liq": 5000.0,
         "created": 9_900.0, "vol24": 5000.0, "grad": 0.4, "buys5": None, "sells5": None, "buyers5": None,
         "vol5": None, "chg5": None, "chg1h": None}
    assert sc.evaluate(p, 10_000.0) is None
    monkeypatch.setenv("MC_SOLANA_ENABLED", "1")
    sc2 = m.Scanner()
    d = sc2.evaluate(p, 10_000.0)
    assert d and d["src"] == "stonkfun"


def test_act_opens_paper_positions_on_non_solana_chain():
    sc, later, t2 = _base_pool()
    d = sc.evaluate(later, t2)
    r = m.act(d, t2)
    assert r.get("opened"), r
    assert m.act(d, t2 + 1.0) == {"skipped": "already acted"}


def test_act_records_raw_numeric_snapshot_on_the_position(monkeypatch, tmp_path):
    """28 Oct 2026 (Ali): today's full pattern audit hit a wall because the raw numeric fields
    (buys5/sells5/buyers5/vol5/chg5) that feed the entry decision never survived onto the
    closed trade record -- only the boolean confirmation tags did. act() now threads a 'raw'
    snapshot through tags so a future audit can test continuous values, not just booleans."""
    import executor.paper_ledger as pl
    monkeypatch.setattr(pl.state, "LOCAL_STATE_FILE", str(tmp_path / "pl.json"), raising=False)
    sc, later, t2 = _base_pool()
    d = sc.evaluate(later, t2)
    r = m.act(d, t2)
    assert r.get("opened"), r
    open_book = pl._open()
    pos = next(iter(open_book.values()))
    assert pos["tags"]["raw"] == {"buys5": 30, "sells5": 10, "buyers5": 20, "vol5": 6000.0,
                                   "vol24": None, "chg5": 40.0, "chg1h": 60.0}


def test_solana_momentum_bucket_above_cap_only_core1_and_needs_4_conf(monkeypatch):
    """29 Oct 2026 (Ali): solana is now blocked by default in this file (see the chain gate's
    own comment), so the momentum-bucket lane logic below only runs under the explicit env
    override -- this test now proves both: (1) solana is refused by default even in this
    bucket, (2) the override restores the original momentum-lane behavior unchanged."""
    sc = m.Scanner()
    big = _pool(addr="SolBig", chain="solana", src="gt", mcap=150_000.0)
    assert sc.evaluate(big, 10_000.0) is None
    monkeypatch.setenv("MC_SOLANA_ENABLED", "1")
    sc2 = m.Scanner()
    big2 = _pool(addr="SolBig", chain="solana", src="gt", mcap=150_000.0)
    d = sc2.evaluate(big2, 10_000.0)
    assert d and d["lanes"] == ["core1"] and "momentum" in d["confirmations"]
    # EVM above the cap is still refused regardless
    assert sc2.evaluate(_pool(addr="0xbig", chain="bsc", mcap=150_000.0), 10_000.0) is None
    # above the momentum ceiling is refused too
    assert sc2.evaluate(_pool(addr="SolHuge", chain="solana", mcap=1_500_000.0), 10_000.0) is None


def test_big_launch_and_robinhood_lanes_are_probe_only(monkeypatch):
    """29 Oct 2026 (Ali): same as above -- solana's biglaunch lane is only reachable with the
    gate's env override now; robinhood/bsc above-cap refusals are unaffected (their own gates,
    not the solana one)."""
    sc = m.Scanner()
    assert sc.evaluate(_pool(addr="SolBig2", chain="solana", mcap=600_000.0), 10_000.0) is None
    monkeypatch.setenv("MC_SOLANA_ENABLED", "1")
    sc2 = m.Scanner()
    d = sc2.evaluate(_pool(addr="SolBig2", chain="solana", mcap=600_000.0), 10_000.0)
    assert d and d["lanes"] == ["core1"] and d["tier"] == "probe" and "biglaunch" in d["confirmations"]
    assert sc2.evaluate(_pool(addr="0xrh2", chain="robinhood", mcap=400_000.0), 10_000.0) is None
    assert sc2.evaluate(_pool(addr="0xbsc", chain="bsc", mcap=180_000.0), 10_000.0) is None


def test_solana_blocked_by_default_in_multichain_sniper():
    """29 Oct 2026 (Ali): "every other coin which does not even fulfill the criteria should be
    thrown away ... why is it being shipped to any of the modules?" Audited and found solana
    had NO chain-level quality gate in this file at all (unlike base/robinhood_chain, which both
    return None before lane assignment when their gate fails) -- every solana coin clearing the
    generic 3-confirmation mechanical floor was trading into core1/core2/sprint/compound
    unfiltered. Real damage: 972 solana multichain-origin trades, all-time, 12.1% win rate, net
    -$17,404.56 -- the single largest money-loser in the system, and the real driver of sprint/
    core1/core2/compound's chronic busts. No profitable multichain-origin solana pattern has been
    found and evidenced (the separate layers/layer17_early_entry.py pipeline's repeat_grad lead
    is a different feed and not yet proven either). Blocked the same way bsc already is -- every
    solana coin refused before lane assignment, by default, until a real pattern is found."""
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="SolAny", chain="solana", mcap=15_000.0), 10_000.0)
    assert d is None


def test_solana_enabled_env_override_restores_old_behavior(monkeypatch):
    monkeypatch.setenv("MC_SOLANA_ENABLED", "1")
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="SolAny2", chain="solana", mcap=15_000.0), 10_000.0)
    assert d and d["chain"] == "solana"


def test_robinhood_biglaunch_lane_is_blocked():
    """28 Oct 2026 (Ali): asked to combine filters on robinhood_chain's >=50k mcap slice to
    push the win rate higher. Found the chain's ENTIRE biglaunch history (n=2, not a
    cherry-picked slice) lost both times (0% win, -$15.60). The biglaunch lane (this test's
    old name: 'robinhood lanes are probe only') now returns None for robinhood_chain instead
    of a probe-tier trade; lifts the >=50k win rate from 48.3% to 51.9% (n=27)."""
    sc = m.Scanner()
    assert sc.evaluate(_pool(addr="0xrh", chain="robinhood", mcap=180_000.0), 10_000.0) is None


def test_tier_of():
    assert m.tier_of(["a", "b", "c"]) == "probe"
    assert m.tier_of(["a", "b", "c", "d"]) == "standard"
    assert m.tier_of(["buyers", "pressure", "c", "d", "e", "f"]) == "conviction"
    assert m.tier_of(["a", "b", "c", "d", "e", "f"]) == "standard"


def test_disabled_chains_default_excludes_bsc():
    """28 Oct 2026 (Ali): bsc stays disabled -- flat 0% win rate all-time (37/37 losses,
    -$1,073.49), no filtered slice found anywhere in this audit that flips it. base was
    re-enabled the same day once a real chain-specific gate was found (growth+volume,
    91.7% win/+$493.02 net, see test_base_requires_growth_and_volume) and robinhood_chain
    the same day prior (GeckoTerminal fallback fix + its own mcap-floor gate)."""
    assert m.DISABLED_CHAINS == {"bsc"}
    # robinhood_chain is resolved dynamically at runtime (resolve_rhc()), not in GT_SLUGS.
    slugs = {c: s for c, s in m.GT_SLUGS.items() if c not in m.DISABLED_CHAINS}
    assert slugs == {"solana": "solana", "base": "base"}


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


def test_smart_wallet_hit_bypasses_chain_gate_into_dev_list(monkeypatch):
    """29 Oct 2026 (Ali): "if any of these [smart wallet, dev reputation, verified virality]
    should highlight for that particular coin ... that module should be working on this, on
    that coin specifically, not bifurcating it to any other module." A coin with a confirmed
    smart_wallet hit (evm_wallets.hit_n>=1) must route into dev_list regardless of whether it
    would pass/fail the chain's own gate -- here, a robinhood_chain pool well BELOW the $30k
    mcap floor (which alone would return None) still produces a dev_list-only result once the
    smart-wallet hit is recorded in state. Mirrors 6 of the real 8 historical smart_wallet hits,
    which sat below this exact floor and were being discarded with zero evidence captured."""
    import evm_wallets
    now = 10_000.0
    pool_addr = "0xsmart_pool"
    state.set_value(evm_wallets.KEY_HITS, {f"robinhood_chain|{pool_addr}": {"n": 3, "ts": now}})
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh_below_floor", chain="robinhood_chain", mcap=7_000.0,
                          pool=pool_addr, created_ago=120), now)
    assert d is not None
    assert d["lanes"] == ["dev_list"] and d["lane"] == "dev_list"
    assert d["confirmations"] == ["smart_wallet"]
    assert d["tier"] == "probe"


def test_no_smart_wallet_hit_falls_through_to_normal_chain_gate():
    """Control for the above: with no recorded smart_wallet hit, the same below-floor
    robinhood_chain pool is refused exactly as before (floor still applies to everyone else)."""
    now = 10_000.0
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh_below_floor2", chain="robinhood_chain", mcap=7_000.0,
                          pool="0xno_hit_pool", created_ago=120), now)
    assert d is None


def test_smart_wallet_hit_also_bypasses_solana_block(monkeypatch):
    """The solana chain-level block (MC_SOLANA_ENABLED, default off) is a different decision
    from the smart_wallet override and must not suppress it either -- a solana coin with a
    recorded smart-wallet hit still routes to dev_list even though plain solana trades are
    blocked by default."""
    import evm_wallets
    now = 10_000.0
    pool_addr = "0xsmart_sol_pool"
    state.set_value(evm_wallets.KEY_HITS, {f"solana|{pool_addr}": {"n": 4, "ts": now}})
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xsol_smart", chain="solana", mcap=12_000.0,
                          pool=pool_addr, created_ago=60), now)
    assert d is not None
    assert d["lanes"] == ["dev_list"]
    assert d["confirmations"] == ["smart_wallet"]


def test_narrative_first_hit_bypasses_chain_gate_into_dev_list(monkeypatch):
    """29 Oct 2026 (Ali): second quality-signal source wired into the same dev_list bypass
    module as smart_wallet -- trend_feeds.py's virality detection (already feeding layer17's
    narrative_first tag via worker_early_watch.hot_terms/claim_trend_term) now also checked
    here, before any chain gate. A coin whose name matches a live trending phrase, and which
    wins the "first coin anywhere" claim, routes to dev_list regardless of chain-gate outcome
    -- here, a robinhood_chain pool below the $30k mcap floor."""
    import worker_early_watch as wew
    monkeypatch.setattr(wew, "hot_terms", lambda: {"moonshot capital partners"})
    monkeypatch.setattr(wew, "claim_trend_term", lambda term, mint: True)
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh_narrative", chain="robinhood_chain", mcap=7_000.0,
                          name="MOONSHOTCAPITALPARTNERS", created_ago=120), 10_000.0)
    assert d is not None
    assert d["lanes"] == ["dev_list"] and d["lane"] == "dev_list"
    assert d["confirmations"] == ["narrative_first"]


def test_narrative_first_not_first_claim_falls_through_to_normal_gate(monkeypatch):
    """Control: the name matches a trending term, but claim_trend_term says another coin
    already claimed it first -- this coin must NOT get the bypass, and falls through to the
    normal (failing, below-floor) chain gate exactly as before."""
    import worker_early_watch as wew
    monkeypatch.setattr(wew, "hot_terms", lambda: {"moonshot capital partners"})
    monkeypatch.setattr(wew, "claim_trend_term", lambda term, mint: False)
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh_narrative2", chain="robinhood_chain", mcap=7_000.0,
                          name="MOONSHOTCAPITALPARTNERS", created_ago=120), 10_000.0)
    assert d is None


def test_no_trending_terms_has_no_effect():
    """Control: with no trending terms available at all (the normal, usually-quiet case),
    evaluate() behaves exactly as before the narrative_first wiring."""
    sc = m.Scanner()
    d = sc.evaluate(_pool(addr="0xrh_quiet", chain="robinhood_chain", mcap=7_000.0,
                          created_ago=120), 10_000.0)
    assert d is None

"""
Tests for the bankroll-scaling benchmarks added to executor/config.py
(Ali, Sept 23, 2026: "set benchmarks for this system of how we should
utilize the funds if... our account grows"). Covers: tier boundaries land
where documented, position sizing scales as a % of the CURRENT wallet
(not a stale fixed $ amount), an explicit env override still wins over the
tier (same philosophy as every other field in this config), and the
default $100 wallet is byte-for-byte unchanged from before this feature
existed.
"""
import importlib
import os

from executor.config import bankroll_tier, ExecutorConfig, BANKROLL_TIERS


def test_bankroll_tier_boundaries_match_the_documented_table():
    assert bankroll_tier(0)["tier"] == "seed"
    assert bankroll_tier(199.99)["tier"] == "seed"
    assert bankroll_tier(200)["tier"] == "growth"
    assert bankroll_tier(999.99)["tier"] == "growth"
    assert bankroll_tier(1_000)["tier"] == "scale"
    assert bankroll_tier(4_999.99)["tier"] == "scale"
    assert bankroll_tier(5_000)["tier"] == "compound"
    assert bankroll_tier(24_999.99)["tier"] == "compound"
    assert bankroll_tier(25_000)["tier"] == "preserve"
    assert bankroll_tier(1_000_000)["tier"] == "preserve"


def test_bankroll_tier_position_pct_decreases_as_wallet_grows():
    # Position sizing should get MORE conservative (lower %) as the account
    # grows -- protecting compounded gains matters more than it did at seed.
    pcts = [bankroll_tier(w)["position_pct"] for w in (50, 500, 2_500, 10_000, 50_000)]
    assert pcts == sorted(pcts, reverse=True)


def test_preserve_tier_gates_on_conviction_score():
    # Only the top tier requires a minimum moonshot conviction score to
    # fire -- late-stage capital shouldn't go into noise trades.
    tier = bankroll_tier(30_000)
    assert tier["min_conviction_score"] == 4
    for w in (50, 500, 2_500, 10_000):
        assert bankroll_tier(w)["min_conviction_score"] == 0


def test_default_50_wallet_sizes_at_the_real_seed_tier_rate():
    # Real starting capital is $50 (Ali), seed tier is 30% (Ali, Sept 23
    # 2026 -- bumped from 20%/$10 to 30%/$15, wants bigger per-trade
    # swings while there's little capital to protect yet).
    cfg = ExecutorConfig()
    assert cfg.total_wallet_usd == 50.0
    assert cfg.stage1_position_usd == 15.0  # 50 * 0.30
    assert cfg.stage2_position_usd == 12.75  # round(15 * 0.85, 2)
    assert cfg.bankroll_tier_info["tier"] == "seed"
    assert cfg.bankroll_tier_info["position_pct"] == 0.30


def test_position_sizing_scales_with_total_wallet_usd(monkeypatch):
    monkeypatch.setenv("TOTAL_WALLET_USD", "2500")
    monkeypatch.delenv("STAGE1_POSITION_USD", raising=False)
    monkeypatch.delenv("STAGE2_POSITION_USD", raising=False)
    cfg = ExecutorConfig()
    assert cfg.bankroll_tier_info["tier"] == "scale"
    assert cfg.stage1_position_usd == 175.0  # 2500 * 0.07
    assert cfg.stage2_position_usd == round(175.0 * 0.85, 2)


def test_explicit_env_override_still_wins_over_the_tier(monkeypatch):
    # Same override philosophy as every other field in this file -- a hand-
    # set STAGE1_POSITION_USD must never be silently replaced by the tier.
    monkeypatch.setenv("TOTAL_WALLET_USD", "50000")
    monkeypatch.setenv("STAGE1_POSITION_USD", "42.5")
    monkeypatch.delenv("STAGE2_POSITION_USD", raising=False)
    cfg = ExecutorConfig()
    assert cfg.stage1_position_usd == 42.5  # explicit value, NOT 50000*0.04=2000
    assert cfg.stage2_position_usd == round(42.5 * 0.85, 2)  # stage2 still derives from the real stage1


def test_bankroll_tiers_table_has_no_gaps_or_overlaps():
    # Every tier's max must equal the next tier's min exactly, and the
    # table must start at 0 and end open-ended (None) -- otherwise some
    # wallet size would silently match no tier or two tiers.
    sorted_tiers = sorted(BANKROLL_TIERS, key=lambda t: t[1])
    assert sorted_tiers[0][1] == 0
    assert sorted_tiers[-1][2] is None
    for (name_a, lo_a, hi_a, *_), (name_b, lo_b, hi_b, *_) in zip(sorted_tiers, sorted_tiers[1:]):
        assert hi_a == lo_b, f"gap/overlap between {name_a} (ends {hi_a}) and {name_b} (starts {lo_b})"

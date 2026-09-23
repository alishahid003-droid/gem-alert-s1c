from layers.layer0_scoring import RawSignals
from layers.layer6_exit_realizable import detect_exit_risk, get_exit_thresholds, compute_realizable_gain


def test_lp_withdrawal_detected():
    prev = RawSignals(liquidity_usd=10000)
    curr = RawSignals(liquidity_usd=6000)  # 40% drop, over the 30% normal threshold
    reasons = detect_exit_risk(prev, curr)
    assert any("LP withdrawal" in r for r in reasons)


def test_no_false_positive_on_small_liquidity_move():
    prev = RawSignals(liquidity_usd=10000)
    curr = RawSignals(liquidity_usd=9200)  # 8% drop, under threshold
    reasons = detect_exit_risk(prev, curr)
    assert reasons == []


def test_mint_authority_reenabled_detected():
    prev = RawSignals(mint_authority_revoked=True)
    curr = RawSignals(mint_authority_revoked=False)
    reasons = detect_exit_risk(prev, curr)
    assert any("Mint authority" in r for r in reasons)


def test_tightened_thresholds_are_stricter_when_high_risk_momentum():
    normal = get_exit_thresholds(False)
    tight = get_exit_thresholds(True)
    assert tight.liquidity_drop_pct < normal.liquidity_drop_pct
    assert tight.top_holder_jump_pct < normal.top_holder_jump_pct


def test_tightened_threshold_catches_a_move_normal_would_miss():
    prev = RawSignals(liquidity_usd=10000)
    curr = RawSignals(liquidity_usd=8200)  # 18% drop: under normal (30%), over tightened (15%)
    assert detect_exit_risk(prev, curr, is_high_risk_momentum=False) == []
    assert len(detect_exit_risk(prev, curr, is_high_risk_momentum=True)) == 1


def test_realizable_gain_fails_closed_without_price():
    result = compute_realizable_gain("base", "evm:8453", "0xTOKEN", 1000, None, "0xWALLET", "0xNATIVE")
    assert result["ok"] is False
    assert "price" in result["reason"]

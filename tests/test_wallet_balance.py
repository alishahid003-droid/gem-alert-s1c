from layers.wallet_balance import fetch_wallets_portfolio, extract_balances_for_pairs


def test_fetch_batches_every_address_into_one_call(monkeypatch):
    import layers.wallet_balance as wb

    captured = {}

    def fake_get_json(url, headers=None, params=None, timeout=20):
        captured["params"] = params
        return {"ok": True, "status_code": 200, "url": url, "json": {"data": []}}

    monkeypatch.setattr(wb, "get_json", fake_get_json)
    monkeypatch.setattr(wb.CONFIG, "mobula_api_key", "mobula_test")

    result = fetch_wallets_portfolio(["W1", "W2", "W3", "W1"], blockchain="solana")
    assert result["ok"] is True
    # de-duped, comma-joined -- ONE call covers all wallets
    assert captured["params"]["wallets"] == "W1,W2,W3"


def test_fetch_fails_closed_without_key_or_addresses(monkeypatch):
    import layers.wallet_balance as wb
    monkeypatch.setattr(wb.CONFIG, "mobula_api_key", "mobula_test")
    result = fetch_wallets_portfolio([], blockchain="solana")
    assert result["ok"] is False


def test_extract_balances_shape_dict_keyed_by_wallet():
    payload = {"data": {
        "W1": {"assets": [{"asset": {"contracts_balances": "TOKEN_A", "symbol": "GEM"}, "token_balance": 10.0}]},
        "W2": {"assets": [{"asset": {"contracts_balances": "TOKEN_A", "symbol": "GEM"}, "token_balance": 4.0}]},
    }}
    pairs = [("W1", "TOKEN_A"), ("W2", "TOKEN_A"), ("W3", "TOKEN_A")]
    result = extract_balances_for_pairs(payload, pairs)
    assert result == {("W1", "TOKEN_A"): 10.0, ("W2", "TOKEN_A"): 4.0}
    assert ("W3", "TOKEN_A") not in result  # unmatched wallet -- fails closed, no guess


def test_extract_balances_shape_single_wallet_flat_assets():
    payload = {"data": [{"asset": {"contracts_balances": "TOKEN_A"}, "token_balance": 7.5}]}
    result = extract_balances_for_pairs(payload, [("W1", "TOKEN_A")])
    assert result == {("W1", "TOKEN_A"): 7.5}


def test_extract_balances_missing_token_in_assets_is_not_guessed():
    payload = {"data": {"W1": {"assets": [{"asset": {"contracts_balances": "TOKEN_B"}, "token_balance": 1.0}]}}}
    result = extract_balances_for_pairs(payload, [("W1", "TOKEN_A")])
    assert result == {}

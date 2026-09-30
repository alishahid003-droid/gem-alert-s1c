"""Checklist 3.2 (pre-buy sell-ability), 5.2 (priority fees), 6.3 (test trade dry run)."""
import pytest

import executor.sellability as sb
import executor.swap_executor as sx


def gp(data):
    return lambda chain, token: {"ok": True, "data": data}


def test_evm_honeypot_refused():
    ok, why = sb.check_sellable("bsc", "0xabc", goplus_fn=gp({"is_honeypot": "1"}))
    assert not ok and "honeypot" in why


def test_evm_high_sell_tax_refused():
    ok, why = sb.check_sellable("bsc", "0xabc", goplus_fn=gp({"sell_tax": "0.35", "buy_tax": "0.01"}))
    assert not ok and "sell tax 35%" in why


def test_evm_clean_allowed():
    assert sb.check_sellable("bsc", "0xabc", goplus_fn=gp({"is_honeypot": "0", "sell_tax": "0.02"}))[0]


def test_goplus_outage_does_not_freeze_trading():
    assert sb.check_sellable("bsc", "0xabc", goplus_fn=lambda c, t: {"ok": False})[0]


def test_solana_token2022_red_flags():
    ok, why = sb.check_sellable("solana", "M", goplus_fn=gp({"transfer_hook": [{"address": "x"}]}))
    assert not ok and "transfer hook" in why
    assert not sb.check_sellable("solana", "M", goplus_fn=gp({"non_transferable": "1"}))[0]
    assert not sb.check_sellable("solana", "M", goplus_fn=gp({"transfer_fee": {"fee_rate": "30"}}))[0]


def test_solana_no_sell_route_refused():
    ok, why = sb.check_sellable("solana", "M", lamports_in=10_000_000, buy_quote={"outAmount": "5000"},
                                goplus_fn=gp({}), quote_fn=lambda p: {"ok": False, "status_code": 400})
    assert not ok and "no sell route" in why


def test_solana_heavy_round_trip_loss_refused():
    ok, why = sb.check_sellable("solana", "M", lamports_in=10_000_000, buy_quote={"outAmount": "5000"},
                                goplus_fn=gp({}), quote_fn=lambda p: {"ok": True, "json": {"outAmount": "6000000"}})
    assert not ok and "40%" in why


def test_solana_normal_round_trip_ok():
    ok, why = sb.check_sellable("solana", "M", lamports_in=10_000_000, buy_quote={"outAmount": "5000"},
                                goplus_fn=gp({}), quote_fn=lambda p: {"ok": True, "json": {"outAmount": "9700000"}})
    assert ok and "3.0%" in why


def test_solana_quote_outage_not_blocking():
    ok, _ = sb.check_sellable("solana", "M", lamports_in=10_000_000, buy_quote={"outAmount": "5000"},
                              goplus_fn=gp({}), quote_fn=lambda p: {"ok": False, "status_code": 503})
    assert ok


def test_priority_fee_params(monkeypatch):
    p = sx.solana_swap_speed_params()
    assert p["dynamicComputeUnitLimit"] is True
    assert p["prioritizationFeeLamports"]["priorityLevelWithMaxLamports"]["maxLamports"] == 1_000_000
    monkeypatch.setenv("SOLANA_PRIORITY_MAX_LAMPORTS", "250000")
    assert sx.solana_swap_speed_params()["prioritizationFeeLamports"]["priorityLevelWithMaxLamports"]["maxLamports"] == 250000


def test_buy_refused_before_signing_when_unsellable(monkeypatch):
    monkeypatch.setattr(sx, "_refuse_unless_ready", lambda chain: None)
    monkeypatch.setattr(sx, "_sol_price_usd", lambda: 150.0)
    monkeypatch.setattr(sx, "get_json", lambda url, params=None, **kw: {"ok": True, "json": {"outAmount": "100"}})
    monkeypatch.setattr("executor.sellability.check_sellable", lambda *a, **k: (False, "honeypot (GoPlus)"))
    sent = []
    monkeypatch.setattr(sx, "post_json", lambda *a, **k: sent.append(1) or {"ok": False})
    r = sx.execute_buy_solana("Mint", 2.0)
    assert not r.ok and "refused before buying" in r.reason and sent == []


def test_test_trade_dry_run(monkeypatch, capsys):
    import sys
    import test_trade
    monkeypatch.setattr(sys, "argv", ["test_trade.py", "--dry-run"])
    monkeypatch.setattr(sx, "_sol_price_usd", lambda: 150.0)
    import utils.http as http
    monkeypatch.setattr(http, "get_json", lambda url, params=None, **kw: {"ok": True, "json": {"outAmount": "2000000"}})
    monkeypatch.setattr("executor.sellability.check_sellable", lambda *a, **k: (True, "sell route OK"))
    assert test_trade.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN complete" in out and "Nothing was signed" in out


def test_test_trade_refuses_big_size(monkeypatch):
    import sys
    import test_trade
    monkeypatch.setattr(sys, "argv", ["test_trade.py", "--usd", "50", "--dry-run"])
    assert test_trade.main() == 2


def test_price_impact_refusal():
    from executor.swap_executor import price_impact_refusal
    assert price_impact_refusal("0.05") and "5.0%" in price_impact_refusal("0.05")
    assert price_impact_refusal("0.01") is None
    assert price_impact_refusal(None) is None
    assert price_impact_refusal("junk") is None

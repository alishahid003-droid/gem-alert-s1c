import socket

import dns_fallback


def test_falls_back_to_doh_for_blocked_host(monkeypatch):
    calls = []

    def boom(host, port, *a, **k):
        raise socket.gaierror(11001, "getaddrinfo failed")
    monkeypatch.setattr(dns_fallback, "_orig", boom)
    monkeypatch.setattr(dns_fallback, "doh_lookup", lambda h: calls.append(h) or ["149.154.166.110"])
    out = dns_fallback._patched("api.telegram.org", 443)
    assert out[0][4] == ("149.154.166.110", 443) and calls == ["api.telegram.org"]


def test_other_hosts_still_fail(monkeypatch):
    def boom(host, port, *a, **k):
        raise socket.gaierror(11001, "x")
    monkeypatch.setattr(dns_fallback, "_orig", boom)
    try:
        dns_fallback._patched("example.invalid", 443)
        assert False
    except socket.gaierror:
        pass


def test_dashboard_sniper_desk_and_telegram_off(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    import telegram_alert
    assert "disabled" in telegram_alert.send_telegram_message("x")["reason"]
    import dashboard
    from executor import paper_ledger
    paper_ledger.open_paper("solana", "MintZ", "early_core2", "early_core2", 10, 5000, strategy="early",
                            exit_profile="core2", now=1000.0)
    paper_ledger.manage(lambda c, t: {"mcap_usd": 7500, "liquidity_usd": 9999}, now=1010.0, source_prefix="early_")
    d = dashboard._sniper_desk([{"ts": 1000, "layer": "early_core2", "token_symbol": "Z", "token_address": "MintZ",
                                 "headline": "h", "tags": {"Module": "core2", "Confirmations": "velocity"}}])
    assert d["open"][0]["mult"] == 1.5 and d["recent"][0]["links"]["GMGN"].endswith("MintZ")
    assert d["live_on"] is False

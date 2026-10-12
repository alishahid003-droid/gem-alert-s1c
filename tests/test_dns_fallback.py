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

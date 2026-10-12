"""DNS-block workaround (Oct 12 2026). Ali's ISP DNS answers "non-existent domain" for
api.telegram.org (nslookup via 1.1.1.1 works, so it is a DNS-only block). When the normal
lookup fails for a listed host, ask Cloudflare over HTTPS (DoH, by IP, so it needs no DNS)
and connect to that address. TLS still verifies the real hostname. Cached 10 min."""
import socket
import time

HOSTS = {"api.telegram.org"}
_cache = {}
_orig = socket.getaddrinfo
_installed = False


def doh_lookup(host: str):
    hit = _cache.get(host)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    import requests
    ips = []
    for url in ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve"):      # Cloudflare, then Google
        try:
            r = requests.get(url, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"}, timeout=6)
            ips = [a["data"] for a in (r.json().get("Answer") or []) if a.get("type") == 1]
        except Exception:  # noqa: BLE001
            continue
        if ips:
            break
    if ips:
        _cache[host] = (time.time(), ips)
    return ips


def _patched(host, port, *a, **kw):
    try:
        return _orig(host, port, *a, **kw)
    except socket.gaierror:
        if host not in HOSTS:
            raise
        ips = doh_lookup(host)
        if not ips:
            raise
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


def install():
    global _installed
    if not _installed:
        socket.getaddrinfo = _patched
        _installed = True

"""Prints your Fomo API plan/credit/reset info from GET /v2/me (the docs say /v2/me shows the balance).
Never prints the key. Usage: python fomo_me.py"""
import json
import os
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass
key = (os.environ.get("FOMOAPI_API_KEY") or "").strip()
if not key:
    raise SystemExit("FOMOAPI_API_KEY not found in .env")
r = requests.get("https://api.fomoapi.io/v2/me", headers={"Authorization": f"Bearer {key}"}, timeout=20)
print("HTTP", r.status_code)
for h in ("x-credits-cost", "x-credits-remaining", "x-credits-limit", "x-credits-reset", "retry-after"):
    if h in r.headers:
        print(f"header {h}: {r.headers[h]}")
try:
    print(json.dumps(r.json(), indent=2)[:3000])
except ValueError:
    print(r.text[:1500])

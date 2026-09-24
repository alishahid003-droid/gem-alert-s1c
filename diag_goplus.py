"""One-off diagnostic (Sept 25 2026) -- NOT part of the live system.
Pulls the live Mobula BSC Pulse snapshot, finds Ali's two named coins
(和平熊猫 / 友谊使者), and calls fetch_goplus_security directly against
their REAL contract addresses so we can see exactly what GoPlus returns
for a real token -- the earlier test used a burn address (0x...dEaD),
which correctly came back "not present" since it isn't a real token
contract, not because GoPlus/auth is broken.
"""
from dotenv import load_dotenv
load_dotenv()
import json
from layers.layer0_scoring import fetch_mobula_pulse, flatten_mobula_pulse_response, fetch_goplus_security

TARGET_NAMES = ["和平熊猫", "友谊使者"]

raw = fetch_mobula_pulse("evm:56")
if not raw.get("ok"):
    print("Mobula fetch failed:", raw.get("reason"))
    raise SystemExit(1)

items = flatten_mobula_pulse_response(raw.get("json"))
print(f"Got {len(items)} BSC items from Mobula Pulse\n")

for item in items:
    nm = (item.get("name") or item.get("symbol") or "").strip()
    if nm not in TARGET_NAMES:
        continue
    addr = item.get("address")
    print(f"=== {nm} / {addr} ===")
    print("Mobula 'security' key present:", "security" in item, "-- value:", item.get("security"))
    gp = fetch_goplus_security("bsc", addr)
    print("GoPlus result:", json.dumps(gp, indent=2, default=str))
    print()

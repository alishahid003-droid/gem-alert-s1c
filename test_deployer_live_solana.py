"""Same live test as test_deployer_live.py, but forces the Solana side of
Layer 1's deployer-hunter endpoint instead of whatever chain_for_cycle
would pick right now -- so both endpoint families (Solana and Robinhood
Chain) get proven live, not just whichever one the clock happened to land
on. Run: python test_deployer_live_solana.py"""
from dotenv import load_dotenv
load_dotenv()

from layers.layer1_deployer import poll_layer1
import json

result = poll_layer1(chain="solana")
print("chain: solana")
print("ok:", result["ok"])

if not result["ok"]:
    print("reason:", result.get("reason"))
else:
    alerts = result.get("alerts", [])
    print("alerts found:", len(alerts))
    print(json.dumps(alerts[:3], indent=2))

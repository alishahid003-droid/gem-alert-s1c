"""One-off live test: does Layer 1 deployer tracking actually reach
MadeOnSol and get back real data? Run this directly (python
test_deployer_live.py) -- Ali, Sept 30 2026, network to madeonsol.com is
blocked from Claude's own environments, so this has to run from your own
machine to mean anything."""
from dotenv import load_dotenv
load_dotenv()

from layers.layer1_deployer import poll_layer1, chain_for_cycle
import time
import json

chain = chain_for_cycle(time.time())
print("chain:", chain)

result = poll_layer1(chain=chain)
print("ok:", result["ok"])

if not result["ok"]:
    print("reason:", result.get("reason"))
else:
    alerts = result.get("alerts", [])
    print("alerts found:", len(alerts))
    print(json.dumps(alerts[:3], indent=2))

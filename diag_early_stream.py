"""Run on your PC: python diag_early_stream.py
Connects to the free PumpPortal stream for ~30 s and prints the first raw events
so we can confirm the field names the early-entry engine reads."""
import asyncio
import json
import time

from early_stream import EarlyStream

printed = {"n": 0, "create": 0, "trade": 0}
t0 = time.time()
stream = None


def on_event(e):
    kind = (e.get("txType") or "?")
    printed["n"] += 1
    if kind == "create":
        printed["create"] += 1
        if printed["create"] <= 3:
            print("CREATE:", json.dumps(e)[:600])
        return [e.get("mint")] if e.get("mint") and printed["create"] <= 5 else None
    printed["trade"] += 1
    if printed["trade"] <= 5:
        print("TRADE :", json.dumps(e)[:600])
    if time.time() - t0 > 30:
        stream.stop = True
    return None


async def main():
    global stream
    stream = EarlyStream(on_event)
    await asyncio.wait_for(stream.run_forever(max_messages=400), timeout=45)


try:
    asyncio.run(main())
except Exception as ex:  # noqa: BLE001
    print("stopped:", type(ex).__name__, ex)
print("counts:", printed, "| last_error:", stream.last_error if stream else None)
print("Expected fields on CREATE: mint, traderPublicKey, initialBuy, marketCapSol, name, symbol. On TRADE: txType buy/sell, solAmount, tokenAmount, marketCapSol.")

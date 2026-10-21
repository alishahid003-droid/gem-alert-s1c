"""One-off: checks whether the candidate Kimchi wallet (2yu3iNjH6zLa8Uqf1s4yKJ1kB9MHYPhMkDbpu8tLmBMC --
sourced from a news article, UNVERIFIED) actually shows up as a top holder of any of the known $Kimchi-named
pump.fun coins. This sandbox's network can't reach Solana RPC directly -- run this on your own PC where the
Helius-backed RPC pool already works. Prints every top-10 holder's owner + % for each mint; if the candidate
wallet shows up with a big %, that confirms the mechanism for real and this becomes notable_wallets.json's
first "confirmed": true entry.

Run: python verify_kimchi_wallet.py
"""
import sys
sys.path.insert(0, ".")
from executor.rpc_pool import rpc_call

MINTS = {
    "B69fvpeHS4CU3QTSMVRjedhAYtckYrF69xYV2H2zpump": "Kimchi/SOL (dexscreener)",
    "GZQZ4r6SQcNZLQXBuuWcEbyiGsxbDaE172DJRduKpump": "Kimchi/SOL キムチ (dextools)",
    "AMLGPnAQaWPNded32PFkRZRzqPGnTqxaUkS4pY2pump": "kimchi-puppy (solflare)",
    "Bg76TvSMemkuxYysHtwEVPQHq7o9ktzpyTnc6ZBPpump": "Kimchicoin (dextools)",
}
CANDIDATE_WALLET = "2yu3iNjH6zLa8Uqf1s4yKJ1kB9MHYPhMkDbpu8tLmBMC"

for mint, label in MINTS.items():
    print(f"\n=== {label}  {mint} ===")
    largest = rpc_call("solana", "getTokenLargestAccounts", [mint])
    if not largest.get("ok"):
        print("  FAILED:", largest.get("reason") or largest)
        continue
    accounts = (largest.get("result") or {}).get("value") or []
    supply_resp = rpc_call("solana", "getTokenSupply", [mint])
    total = ((supply_resp.get("result") or {}).get("value") or {}).get("uiAmount") if supply_resp.get("ok") else None
    for a in accounts[:10]:
        acct = a.get("address")
        amt = float(a.get("uiAmount") or 0)
        pct = (amt / total * 100) if total else None
        info = rpc_call("solana", "getAccountInfo", [acct, {"encoding": "jsonParsed"}])
        owner = None
        if info.get("ok"):
            parsed = (((info.get("result") or {}).get("value") or {}).get("data") or {}).get("parsed") or {}
            owner = (parsed.get("info") or {}).get("owner")
        flag = "  <<<<<<<< MATCHES CANDIDATE KIMCHI WALLET" if owner == CANDIDATE_WALLET else ""
        print(f"  owner={owner}  pct={pct:.1f}%" if pct is not None else f"  owner={owner}  pct=?", flag)

print("\nIf none matched: either this isn't his real wallet, or the actual coin Ali saw isn't one of these 4")
print("mints -- send the real mint/ticker and this script can be pointed at it directly.")

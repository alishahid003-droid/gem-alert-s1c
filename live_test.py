"""One tiny PumpPortal buy+sell test. YOU run it:  python live_test.py <MINT> [SOL_AMOUNT]
Spends about 0.003 SOL (~$0.30) plus fees. Logs every reply to logs/live_exec.log (no key)."""
import os, sys, time, json
import requests

def load_env():
    try:
        for line in open(".env", encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass

def call(key, data):
    r = requests.post("https://pumpportal.fun/api/trade", params={"api-key": key}, data=data, timeout=20)
    try:
        j = r.json()
    except ValueError:
        j = {"raw": r.text[:300]}
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} TEST {data['action']} {data['mint'][:10]} status={r.status_code} resp={str(j)[:300]}"
    print(line)
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "live_exec.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return r.status_code, j

def main():
    load_env()
    key = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
    if not key:
        print("No PUMPPORTAL_API_KEY in .env"); return
    if len(sys.argv) < 2:
        print("Usage: python live_test.py <MINT> [SOL_AMOUNT]"); return
    mint = sys.argv[1].strip()
    amt = float(sys.argv[2]) if len(sys.argv) > 2 else 0.003
    if amt > 0.01:
        print("Refusing: test amount above 0.01 SOL"); return
    if input(f"This will REALLY buy {amt} SOL of {mint[:10]}... then sell it. Type YES: ").strip().upper() != "YES":
        print("Cancelled"); return
    common = {"mint": mint, "slippage": 25, "priorityFee": 0.0005, "pool": "auto"}
    s, j = call(key, {"action": "buy", "amount": amt, "denominatedInSol": "true", **common})
    if not (s == 200 and isinstance(j, dict) and j.get("signature")):
        print("BUY DID NOT RETURN A SIGNATURE -> failed. Not selling."); return
    print("BUY signature:", j["signature"], "-> check it on solscan.io/tx/<signature>")
    time.sleep(10)
    s, j = call(key, {"action": "sell", "amount": "100%", "denominatedInSol": "false", **common})
    print("SELL signature:", j.get("signature") if isinstance(j, dict) else j)
    print("Done. Now check the wallet on solscan.io: SOL back close to start, no leftover token.")

main()

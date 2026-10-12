"""
verify_dev_wallets.py -- checks the dev wallets in pumpfun_top_devs_oct2026.json ON-CHAIN.
Run from the repo folder:   python verify_dev_wallets.py
Needs only Python (no pip). Free public Solana RPC; ~3 calls per wallet, paced.

For each address it checks:
  1. EXISTS    the account exists on mainnet (a mistyped address won't)
  2. ACTIVE    it has recent transactions (last-seen age, tx per day from last 100 sigs)
  3. PUMPFUN   at least one of its 5 newest transactions touches the pump.fun program
Verdict: VERIFIED (exists + pump.fun activity), EXISTS_ONLY, or NOT_FOUND (likely a typo
in the screenshot transcription). It does NOT confirm the poster's PnL / deploy numbers.
Writes dev_wallet_verification.json next to this script.
"""
import json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RPCS = [os.environ.get("SOLANA_RPC_URL", "").strip(), "https://api.mainnet-beta.solana.com",
        "https://solana-rpc.publicnode.com", "https://rpc.ankr.com/solana"]
RPCS = [r for r in RPCS if r]


def rpc(method, params):
    last = None
    for url in RPCS:
        for _ in range(2):
            try:
                req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                             headers={"content-type": "application/json", "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    j = json.loads(r.read())
                if "error" in j:
                    last = j["error"]; time.sleep(1.5); continue
                return j.get("result")
            except Exception as e:
                last = e; time.sleep(1.5)
    raise RuntimeError(f"all RPCs failed: {last}")


def check(addr):
    out = {"address": addr, "exists": False, "sol": None, "last_seen_hours": None, "tx_per_day": None, "pumpfun_seen": False}
    acc = rpc("getAccountInfo", [addr, {"encoding": "base64"}])
    if not acc or not acc.get("value"):
        out["verdict"] = "NOT_FOUND"; return out
    out["exists"] = True; out["sol"] = round(acc["value"]["lamports"] / 1e9, 3)
    sigs = rpc("getSignaturesForAddress", [addr, {"limit": 100}]) or []
    if sigs:
        now = time.time(); ts = [s["blockTime"] for s in sigs if s.get("blockTime")]
        if ts:
            out["last_seen_hours"] = round((now - max(ts)) / 3600, 1)
            span = max(max(ts) - min(ts), 1)
            out["tx_per_day"] = round(len(ts) / span * 86400)
        for s in sigs[:5]:
            tx = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            keys = (((tx or {}).get("transaction") or {}).get("message") or {}).get("accountKeys") or []
            if PUMP in keys:
                out["pumpfun_seen"] = True; break
            time.sleep(0.3)
    out["verdict"] = "VERIFIED" if out["pumpfun_seen"] else "EXISTS_ONLY"
    return out


def main():
    data = json.load(open(os.path.join(HERE, "pumpfun_top_devs_oct2026.json")))
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(data["wallets"])
    res = []
    print(f"{'address':46}{'verdict':13}{'SOL':>10}{'last seen h':>12}{'tx/day':>8}")
    for w in data["wallets"][:n]:
        try:
            r = check(w["address"])
        except Exception as e:
            r = {"address": w["address"], "verdict": "ERROR", "error": str(e)}
        res.append(r)
        print(f"{r['address']:46}{r['verdict']:13}{str(r.get('sol')):>10}{str(r.get('last_seen_hours')):>12}{str(r.get('tx_per_day')):>8}")
        time.sleep(0.5)
    json.dump(res, open(os.path.join(HERE, "dev_wallet_verification.json"), "w"), indent=1)
    c = {}
    for r in res: c[r["verdict"]] = c.get(r["verdict"], 0) + 1
    print("\nSummary:", c, "\nsaved dev_wallet_verification.json")


if __name__ == "__main__":
    main()

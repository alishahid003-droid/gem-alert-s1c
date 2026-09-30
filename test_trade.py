"""
One-click Solana test trade -- GO_LIVE_CHECKLIST 6.3/6.4 (Sept 30 2026).

The go/no-go gate before real money: buy a small amount of a very liquid
token and sell it straight back through the EXACT live code path
(executor.swap_executor: Jupiter quote -> sell-ability check -> priority
fee -> sign -> send -> confirm -> real fill amount), then print both
Solscan links and the real round-trip cost.

  --dry-run (default in the workflow until a key exists): SOL price, buy
            quote, and the pre-buy sell-ability check against live APIs --
            nothing is signed or sent, no wallet key needed.
  live:     requires EXECUTION_SOLANA_PRIVATE_KEY. Enables execution for
            THIS process only (the scheduled system stays off until the
            EXECUTION_ENABLED secret is set -- checklist 6.5).

Default token: USDC (deep liquidity, SPL token, fill parsing identical to
a memecoin). Run from GitHub Actions: "Test trade (Solana)" -> Run workflow.
"""
import argparse
import os
import sys
import time

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL = "So11111111111111111111111111111111111111112"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=USDC)
    ap.add_argument("--usd", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.usd <= 0 or args.usd > 10:
        print("REFUSED: test size must be between $0 and $10")
        return 2

    if not args.dry_run:
        os.environ["EXECUTION_ENABLED"] = "true"   # this process only
    from utils.http import get_json
    from config import CONFIG
    import executor.swap_executor as sx
    from executor.sellability import check_sellable
    from executor.rpc_pool import rpc_call

    print(f"== Test trade: {'DRY RUN' if args.dry_run else 'LIVE'} | token {args.token} | ${args.usd:.2f}")
    sol = sx._sol_price_usd()
    if not sol:
        print("FAIL: could not fetch SOL/USD price")
        return 1
    lamports = int(args.usd * 1_000_000_000 / sol)
    print(f"SOL price ${sol:,.2f} -> {lamports:,} lamports")
    q = get_json(f"{CONFIG.jupiter_quote_base_url}/quote",
                 params={"inputMint": WSOL, "outputMint": args.token, "amount": lamports, "slippageBps": 100})
    if not q.get("ok"):
        print(f"FAIL: Jupiter buy quote HTTP {q.get('status_code')}")
        return 1
    print(f"buy quote: {int((q.get('json') or {}).get('outAmount', 0)):,} base units out")
    ok, why = check_sellable("solana", args.token, lamports_in=lamports, buy_quote=q.get("json"))
    print(f"sell-ability check: {'PASS' if ok else 'FAIL'} -- {why}")
    print(f"priority fee settings: {sx.solana_swap_speed_params()}")
    if args.dry_run:
        print("DRY RUN complete: live APIs reachable, quotes and checks pass. Nothing was signed or sent.")
        return 0 if ok else 1

    if not os.environ.get("EXECUTION_SOLANA_PRIVATE_KEY"):
        print("FAIL: EXECUTION_SOLANA_PRIVATE_KEY is not set (checklist 6.2)")
        return 1
    pubkey = sx._solana_pubkey_from_private_key()
    bal = rpc_call("solana", "getBalance", [pubkey])
    lamports_have = ((bal.get("result") or {}).get("value")) if bal.get("ok") else None
    print(f"wallet {pubkey} balance: {lamports_have / 1e9 if lamports_have else '?'} SOL")
    if lamports_have is not None and lamports_have < lamports + 10_000_000:
        print("FAIL: not enough SOL for the test amount plus fees (~0.01 SOL)")
        return 1

    buy = sx.execute_buy_solana(args.token, args.usd)
    print(f"BUY: ok={buy.ok} {buy.reason}")
    if buy.tx_signature:
        print(f"  https://solscan.io/tx/{buy.tx_signature}")
    if not buy.ok or not buy.filled_amount_tokens:
        print("FAIL: buy did not confirm with a real fill -- stopping (nothing to sell)")
        return 1
    print(f"  filled {buy.filled_amount_tokens} tokens")
    time.sleep(5)
    sell = sx.execute_sell("solana", args.token, buy.filled_amount_tokens, reason="test trade round trip")
    print(f"SELL: ok={sell.ok} {sell.reason}")
    if sell.tx_signature:
        print(f"  https://solscan.io/tx/{sell.tx_signature}")
    if sell.ok and sell.filled_usd is not None:
        cost = (args.usd - sell.filled_usd) / args.usd * 100
        print(f"ROUND TRIP: ${args.usd:.2f} in -> ${sell.filled_usd:.2f} back ({cost:.1f}% cost incl. fees)")
        print("PASS: live buy + sell confirmed on-chain. Checklist 6.4 can be ticked.")
        return 0
    print("FAIL: sell did not confirm -- check the Solscan links above; tokens may still be in the wallet")
    return 1


if __name__ == "__main__":
    sys.exit(main())

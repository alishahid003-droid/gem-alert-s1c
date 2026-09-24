# S1c Gem-Alert — Next Steps Checklist

_Last updated: Sept 25, 2026, ~2:50 AM PKT — see git log for full history_

## Done and committed

- [x] Mobula Pulse Bearer-header, params, chain-id format, response parsing, field names, security fields
- [x] GoPlus Security fallback, per-field trigger
- [x] Vol/liq ratio curve + score_token reweight (momentum-focused)
- [x] Stale git locks + `__pycache__` cleanup
- [x] Alert vs. Stage1 trigger thresholds confirmed
- [x] Solana buy: real SOL/USD price (Jupiter quote), real sign-and-send, confirmation polling — tested against mocked RPC
- [x] BSC buy: real swap via PancakeSwap V2, real sign-and-send, confirmation polling — tested against mocked RPC
- [x] Solana sell: real sign-and-send, real on-chain decimals lookup (not assumed) — tested
- [x] BSC sell: real approve+swap two-step flow, allowance-aware — tested
- [x] Fixed invalid PancakeSwap router + WBNB addresses (1 hex char short in the existing repo — verified real values against PancakeSwap's own npm package source)
- [x] Fixed a real web3.py v7+ compatibility break (`encodeABI` removed, added fallback)
- [x] Fixed a real address-casing bug in `to_checksum_address` usage

## Open — genuinely blocked on more groundwork, not just more typing

- [ ] Robinhood Chain buy AND sell — Uniswap v4's Universal Router needs pool key data (fee tier, tick spacing, hooks address) this system doesn't currently fetch for a given token. Needs either a real RHC pool lookup or one confirmed real swap's parameters to copy from before this can be written safely.
- [ ] Re-run Solana/Robinhood-Chain backtest once MadeOnSol's rate limit clears
- [ ] 和平熊猫 "LP/curve unknown" — likely duplicate contracts sharing a display name

## Open — recommendations for best-in-class (unstarted)

1. **Speed** — switch Layer 0b from REST polling to Mobula's real-time Pulse WebSocket stream
2. **Backtest depth** — build a real multi-coin backtest set (dozens of gems + rugs, not just 2 coins)
3. **Coverage ceiling** — MadeOnSol free-tier IP-rotation limit; decide on paid tier or second data source

## Before this touches real money

Solana and BSC buy/sell paths have never been run against a real live network — only against mocked RPC responses in this session (success, on-chain revert, disabled-execution, and for BSC sell, both allowance branches). `EXECUTION_ENABLED` should stay `false` until at least one small, real test swap succeeds on each chain, on each side (buy and sell).

# S1c Gem-Alert — Next Steps Checklist

_Last updated: Sept 25, 2026, 2:36 AM PKT — see git log for full history_

## Done and committed

- [x] Mobula Pulse Bearer-header, params, chain-id format, response parsing, field names, security fields
- [x] GoPlus Security fallback, per-field trigger
- [x] Vol/liq ratio curve + score_token reweight (momentum-focused)
- [x] Stale git locks + `__pycache__` cleanup
- [x] Alert vs. Stage1 trigger thresholds confirmed
- [x] Solana buy-sizing: real SOL/USD price via Jupiter quote (`31f770e`)
- [x] Solana sign-and-send: real tx signing (solders) + submission via RPC failover + confirmation polling (`d43187c`) — tested against mocked RPC (success/revert/disabled cases)
- [x] BSC sign-and-send: real swap via PancakeSwap V2 + real signing/submission (`7b3c662`) — tested the same way
- [x] Fixed a real invalid-address bug: PancakeSwap router + WBNB addresses were 1 hex char short in the existing repo (verified real values against PancakeSwap's own npm package source)
- [x] Fixed a real web3.py v7+ compatibility break (`encodeABI` removed, added fallback)
- [x] Fixed a real address-casing bug: `to_checksum_address` would wrongly reject a valid mixed-case token address from Mobula

## Open — genuinely blocked on more groundwork, not just more typing

- [ ] Robinhood Chain sign-and-send — Uniswap v4's Universal Router needs pool key data (fee tier, tick spacing, hooks address) this system doesn't currently fetch for a given token; needs either a real RHC pool lookup or one confirmed real swap's parameters to copy from before this can be written safely
- [ ] `execute_sell()` — currently a stub on all 3 chains; buy paths are done, sell/exit is not, and defensive_sell.py depends on it for rug protection
- [ ] Re-run Solana/Robinhood-Chain backtest once MadeOnSol's rate limit clears
- [ ] 和平熊猫 "LP/curve unknown" — likely duplicate contracts sharing a display name

## Open — recommendations for best-in-class (unstarted)

1. **Speed** — switch Layer 0b from REST polling to Mobula's real-time Pulse WebSocket stream
2. **Backtest depth** — build a real multi-coin backtest set (dozens of gems + rugs, not just 2 coins)
3. **Coverage ceiling** — MadeOnSol free-tier IP-rotation limit; decide on paid tier or second data source

## Before this touches real money

Solana and BSC buy paths have never been run against a real live network — only against mocked RPC responses in this session. `EXECUTION_ENABLED` should stay `false` until at least one small, real test swap succeeds on each chain.

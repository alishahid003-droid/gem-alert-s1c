# S1c Gem-Alert — Next Steps Checklist

_Last updated: Sept 25, 2026, 1:58 AM PKT — see git log for full history_

## Done and committed (this session)

- [x] Mobula Pulse Bearer-header bug
- [x] Mobula Pulse `assetMode`/`model` params + correct chain-id format (`evm:56`, `evm:8453`)
- [x] Response-parsing bug (`flatten_mobula_pulse_response`)
- [x] Wrong per-token field names (`holdersCount`, `volume_24h`)
- [x] Wrong security field names (`renounced`/`isProxy`, not `noMintAuthority`/`isBlacklisted`/`balanceMutable`)
- [x] GoPlus Security fallback for LP/mint/freeze, per-field trigger
- [x] Vol/liq ratio curve recalibrated for real FOMO-pump ratios
- [x] `score_token` reweighted toward momentum, away from long-hold security posture
- [x] Stale git locks + `__pycache__` cleanup
- [x] Alert vs. Stage1 trigger thresholds confirmed (alert: any band except D; Stage1: A/B only)
- [x] Solana buy-sizing `NotImplementedError` fixed — real SOL/USD price via Jupiter quote (`31f770e`)

## Open — build/verify

- [ ] Solana sign-and-send (real tx signing/submission)
- [ ] BSC sign-and-send via PancakeSwap V2 router
- [ ] Robinhood Chain sign-and-send via Uniswap v4 Universal Router
- [ ] `swap_executor` buy paths untested on all 3 chains until the above is done and run once for real, small
- [ ] Re-run Solana/Robinhood-Chain backtest once MadeOnSol's rate limit clears
- [ ] 和平熊猫 "LP/curve unknown" — likely duplicate contracts sharing a display name; `score_bsc_by_name` may need to go address-based

## Open — 4 recommendations for best-in-class

1. **Speed** — switch Layer 0b from REST polling to Mobula's real-time Pulse WebSocket stream
2. **Backtest depth** — build a real multi-coin backtest set (dozens of gems + rugs, not just 2 coins)
3. **Execution gap** — close alert→trade gap (same as sign-and-send work above)
4. **Coverage ceiling** — MadeOnSol free-tier IP-rotation limit; decide on paid tier or second data source

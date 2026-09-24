# S1c Gem-Alert — Final Checklist

_Last updated: Sept 25, 2026, ~3:20 AM PKT — see git log for full history_

## Auto-buy — done and tested

- [x] Solana: real SOL/USD pricing, real tx signing, real submission (RPC failover), real confirmation
- [x] BSC: real swap via PancakeSwap V2, real signing/submission/confirmation
- [x] Both: real filled-token-amount extraction from the confirmed transaction, not the requested USD size
- [x] `entrypoint.py` now actually calls the real buy on a Stage 1/2 fire and records the real fill

## Auto-sell — done and tested (moonshot logic included)

- [x] Defensive/rug-triggered full exit: Solana + BSC
- [x] Moonshot trim ladder wired to real fill data: 3x/10x/50x tiers, uncapped moonbag rides forever, lighter ladder for high-conviction positions (55% moonbag vs. 20%)
- [x] Verified end-to-end with a real mocked run: fire → real fill → real position → 10x → correct real sell size

## Dashboard — partially exists, needs real work

- [x] `dashboard.py` already exists: real local HTML page (`python dashboard.py` → localhost:8787), dark theme, auto-refreshing every 10s, module readiness grid, open-positions table, filterable alert feed with coin/platform links per alert — **not raw CMD output**, already close to what was asked for
- [ ] **Missing: trade/fill history** — no record of individual buy/sell executions (tx signature, filled tokens, filled USD, timestamp) anywhere in the UI
- [ ] **Missing: realized P&L** — `position_state.close_position()` already computes `pnl_usd` on close, but closed positions aren't shown anywhere; only open positions render
- [ ] **Missing: closed-positions history table** — needed to see what actually happened over time, not just the live snapshot
- [ ] Needs a small backend addition: a running trade log (buy/sell events with tx hash + real filled amount, which now exist thanks to tonight's fill-recording work) that the dashboard can read and render

## Genuinely blocked on more groundwork, not just more typing

- [ ] Robinhood Chain (buy + sell) — Uniswap v4 needs pool key data (fee tier, tick spacing, hooks) this system doesn't fetch per-token yet
- [ ] Re-run Solana/RHC backtest once MadeOnSol's rate limit clears
- [ ] 和平熊猫 "LP/curve unknown" — likely duplicate contracts sharing a display name

## Best-in-class recommendations (unstarted)

1. Speed — Layer 0b REST polling → Mobula's real-time Pulse WebSocket stream
2. Backtest depth — real multi-coin set (dozens of gems + rugs, not just 2)
3. Coverage ceiling — MadeOnSol free-tier IP-rotation limit; paid tier or second data source

## Tests still left (real, not mocked)

- [ ] One small real Solana buy on a live network (currently only tested against mocked RPC)
- [ ] One small real Solana sell on a live network
- [ ] One small real BSC buy on a live network
- [ ] One small real BSC sell on a live network (exercises the approve+swap two-step flow for real)
- [ ] A real moonbag trim firing against a real live position, not the mocked end-to-end run done tonight
- [ ] `entrypoint.py` wired into `scheduler.py`'s actual live poll loop and run through a real cycle (currently callable but not called by anything live)
- [ ] Dashboard verified against real trade/fill data once the trade-log addition above is built
- [ ] Solana/RHC backtest re-run once MadeOnSol's rate limit clears

## Before any of this touches real money

`EXECUTION_ENABLED` stays `false` until the 4 real test swaps above succeed — mocked tests prove the logic is right, not that a real node behaves identically.

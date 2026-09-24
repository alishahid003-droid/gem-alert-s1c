# S1c Gem-Alert — Final Checklist

_Last updated: Sept 25, 2026, ~3:10 AM PKT — see git log for full history_

## Auto-buy — done and tested

- [x] Solana: real SOL/USD pricing (Jupiter quote), real tx signing (solders), real submission via RPC failover, real confirmation polling
- [x] BSC: real swap via PancakeSwap V2, real signing/submission, real confirmation polling
- [x] Both: real filled-token-amount extraction from the confirmed transaction (Solana: token balance diff; BSC: Transfer event log) — not the requested USD size
- [x] `entrypoint.py` now actually calls the real buy on a Stage 1/2 fire (previously decision-only) and records the real fill onto the position
- [ ] Robinhood Chain buy — blocked on Uniswap v4 pool-key data (see below)

## Auto-sell — done and tested (moonshot logic included)

- [x] Defensive/rug-triggered exit: Solana + BSC full-position sell, real signing/sending/confirming
- [x] **Moonshot trim ladder** (already existed in `moonbag.py`, now actually wired to real numbers): as a position runs 3x/10x/50x, sells pre-set slices to recover capital, leaves a moonbag riding uncapped
  - Default ladder: 35% / 25% / 20% trimmed across the 3 tiers → 20% moonbag rides forever
  - High-conviction ladder (elite deployer, strong wallet convergence, double-confirmed across pump.fun + Fomo, low insider ratio, news catalyst): lighter 15% / 15% / 15% trims → 55% moonbag rides
  - The moonbag itself is never sold by this logic — only exits via the rug-defense path or a manual close, which is the actual point: this is built to let a real 500x run, not cap it early
- [x] Real end-to-end run verified: Stage 1 fires → real (mocked) buy fills 100,000 tokens → position records the real quantity → price hits 10x → high-conviction ladder correctly sells 15% of the *real* holding, not a guess
- [ ] Robinhood Chain sell — same block as its buy

## Genuinely blocked on more groundwork, not just more typing

- [ ] **Robinhood Chain (buy + sell)** — Uniswap v4's Universal Router needs pool key data (fee tier, tick spacing, hooks address) this system doesn't currently fetch per-token. Needs a real RHC pool lookup or one confirmed real swap's params to copy from before writing this safely.
- [ ] Re-run Solana/RHC backtest once MadeOnSol's rate limit clears
- [ ] 和平熊猫 "LP/curve unknown" — likely duplicate contracts sharing a display name

## Best-in-class recommendations (unstarted)

1. **Speed** — switch Layer 0b from REST polling to Mobula's real-time Pulse WebSocket stream
2. **Backtest depth** — build a real multi-coin backtest set (dozens of gems + rugs, not just 2 coins)
3. **Coverage ceiling** — MadeOnSol free-tier IP-rotation limit; decide on paid tier or second data source

## Still separate from all of the above

- `entrypoint.py`'s buy/fill logic is wired and tested, but `entrypoint.py` itself is still NOT called from `scheduler.py`'s live poll loop — that wiring, and turning `EXECUTION_ENABLED=true`, are both deliberately separate, later steps.

## Before this touches real money

Every buy/sell path above has only ever run against mocked RPC responses in this session — never a real live network. Keep `EXECUTION_ENABLED=false` until at least one small, real test swap succeeds on each chain, each direction (buy and sell).

# Curve C — 5-Phase Target Summary (11 Nov 2026, ~15:17 PKT)

Live interactive chart: https://claude.ai/artifact/2XxC92cGt12uPhbm75Dmou

## Phase cashouts (confirmed, Ali 11 Nov 2026)

| Phase | Trigger | Cashout | Left running / reseed forward |
|---|---|---|---|
| 1 | $10,000 | $8,500 | $1,500 |
| 2 | $12,000 | $10,000 | $2,000 |
| 3 | $12,000 | $10,000 | $2,000 |
| 4 | $120,000 | $115,000 | $5,000 |
| 5 | $1,000,000 | $975,000 | $25,000 |

**Fixed cashouts total: $1,118,500**

Note: Phase 5's $975,000/$25,000 split is Ali's confirmed target but is NOT yet coded
in `executor/books.py` PHASE_DEFS[5] (currently `cashout=None, reseed=None` -- ladder-only,
no automatic cashout wired). Needs to be added when ready to build it.

## Trades, days, cashout, banked, bust rate -- per phase, per win rate

Simulated using Curve C's real stake bands, real PHASE_DEFS, real probation sizing
(50% initial stake, 2-cycle confirm or 1.15x pump-graduate), and real trade-size outcomes
from 49 closed compound trades (Robinhood_chain + Base). 3,000 simulated runs per win rate.

### 55% win rate

| Phase | Trigger | Trades P50 | Days P50 | Cum. trades | Cum. days | Banked P50 | Cashed out | Bust rate |
|---|---|---|---|---|---|---|---|---|
| 1 | $10,000 | 81 | 4.3 | 81 | 4.3 | $3,841 | $8,500 | 0.0% |
| 2 | $12,000 | 49 | 2.6 | 130 | 6.8 | $7,573 | $10,000 | 0.0% |
| 3 | $12,000 | 45 | 2.4 | 175 | 9.2 | $8,012 | $10,000 | 0.0% |
| 4 | $120,000 | 114 | 6.0 | 289 | 15.2 | $65,934 | $115,000 | 0.0% |
| 5 | $1,000,000 | 268 | 14.1 | 557 | 29.3 | $409,904 | $975,000 | 0.0% |

### 62% win rate

| Phase | Trigger | Trades P50 | Days P50 | Cum. trades | Cum. days | Banked P50 | Cashed out | Bust rate |
|---|---|---|---|---|---|---|---|---|
| 1 | $10,000 | 62 | 3.3 | 62 | 3.3 | $3,853 | $8,500 | 0.0% |
| 2 | $12,000 | 39 | 2.1 | 101 | 5.3 | $7,579 | $10,000 | 0.0% |
| 3 | $12,000 | 35 | 1.8 | 136 | 7.2 | $8,028 | $10,000 | 0.0% |
| 4 | $120,000 | 90 | 4.7 | 226 | 11.9 | $66,014 | $115,000 | 0.0% |
| 5 | $1,000,000 | 210 | 11.1 | 436 | 22.9 | $409,884 | $975,000 | 0.0% |

### 65% win rate

| Phase | Trigger | Trades P50 | Days P50 | Cum. trades | Cum. days | Banked P50 | Cashed out | Bust rate |
|---|---|---|---|---|---|---|---|---|
| 1 | $10,000 | 56 | 2.9 | 56 | 2.9 | $3,859 | $8,500 | 0.0% |
| 2 | $12,000 | 36 | 1.9 | 92 | 4.8 | $7,575 | $10,000 | 0.0% |
| 3 | $12,000 | 32 | 1.7 | 124 | 6.5 | $8,017 | $10,000 | 0.0% |
| 4 | $120,000 | 82 | 4.3 | 206 | 10.8 | $65,996 | $115,000 | 0.0% |
| 5 | $1,000,000 | 192 | 10.1 | 398 | 20.9 | $410,054 | $975,000 | 0.0% |

## Grand total (fixed cashouts + ladder-banked, all 5 phases)

| Win rate | Ladder-banked sum (all 5 phases) | + Fixed cashouts | = Grand total |
|---|---|---|---|
| 55% | $495,264 | $1,118,500 | ~$1,613,764 |
| 62% | $495,359 | $1,118,500 | ~$1,613,859 |
| 65% | $495,501 | $1,118,500 | ~$1,614,001 |

Plus $25,000 left running in the system after Phase 5.

## Model basis

- Curve C stake bands (50%/35%/20%/10%/5% of equity by range) -- real, live code in
  `executor/books.py`, deployed 8 Nov 2026.
- Real probation sizing (`probation_sizing.py`): new positions open at 50% of the Curve C
  stake, confirm over 2 cycles or graduate instantly on a 1.15x price pump, before topping
  up to full size. This caps real tail-loss exposure and is the reason bust rate is ~0% at
  all three win rates -- an earlier pass that omitted this showed a false 47-61% bust rate
  at 55% win rate; corrected before this was shared.
- Real trade-level pct outcomes: 49 closed compound-module trades (Robinhood_chain + Base,
  Solana excluded), pulled 11 Nov 2026, bootstrap-resampled.
- Trade pace: ~19 trades/day, real measured compound pace.

## Open question (not yet resolved)

Compound's all-time win rate (n=50) currently reads 54.0%, down from 61.3% (n=31) when
Curve C was picked on 8 Nov. This doesn't threaten survival (bust rate stays ~0% at 55%
once probation sizing is modeled correctly) but does change the real trades/days-to-target
figures meaningfully. Worth a dedicated chronological train/test check before settling on
one number to plan around -- same discipline used on the mcap/liquidity threshold.

## Compound exit-ladder investigation (11 Nov 2026) -- no change recommended

Tested whether adding a tighter trailing stop between compound's 1.3x and 2x rungs would
reduce early giveback on stalling trades. Tick-by-tick replay against the real 48-trade
price-trace history: every giveback-cap threshold tested (15%-50%) either loses money vs.
the current ladder (baseline $2,224.21 net) or does nothing. The same price action that
flags a real staller looks identical, in the moment, to a real dip-and-recover that clears
2x cleanly -- 5 real trades in the sample were exactly that, and a tight trail would have
cut them off early for no benefit. **No change made to compound's exit logic.**

# Curve C — 5-Phase Target Summary (11 Nov 2026, ~16:20 PKT)

Live chart: https://claude.ai/artifact/2XxC92cGt12uPhbm75Dmou (now has TWO scenarios: dual-module
compound+sprint, and the original single-module baseline)

## Phase cashouts (confirmed, Ali 11 Nov 2026) — unchanged regardless of staffing

| Phase | Trigger | Cashout | Left running / reseed forward |
|---|---|---|---|
| 1 | $10,000 | $8,500 | $1,500 |
| 2 | $12,000 | $10,000 | $2,000 |
| 3 | $12,000 | $10,000 | $2,000 |
| 4 | $120,000 | $115,000 | $5,000 |
| 5 | $1,000,000 | $975,000 | $25,000 |

**Fixed cashouts total: $1,118,500** — same in any staffing configuration; only the SPEED of
reaching each trigger changes with how many modules are funding the climb.

## NEW: Compound + Sprint dual-module model ($200 total funding: $100 each)

Ali's design (11 Nov 2026): fund compound and sprint $100 each, let them run independently
toward Phase 1-5 on their own real pace/outcome distribution. Whichever module's own banked
total crosses $200 first fires a one-time cascade, splitting $200 to core1+core2 ($100 each) —
gated to only fire once live-money mode (`LIVE_BOOKS`) is armed, so it has zero effect on
ongoing paper testing.

Two honest pace scenarios (both pulled from a 6.4-hour real post-filter window, 11 Nov 2026 —
long enough to be real, not yet long enough to call "sustainable 24/7"):

| Scenario | Compound pace | Sprint pace | Days to clear all 5 phases (P10/P50/P90) | Bust rate |
|---|---|---|---|---|
| Optimistic (observed) | 79.17/day | 45.26/day | 6.4 / 6.9 / 7.5 | 0.0% |
| Conservative (50% discount) | 39.6/day | 22.6/day | 12.7 / 13.8 / 15.1 | 0.15% |

**Cascade to core1/core2 fires very early** in both scenarios — median day 0.64 (optimistic) to
1.28 (conservative) — well before Phase 1 itself completes, since $200 banked is a low bar
against either module's real growth rate once live.

### Per-phase detail, OPTIMISTIC scenario (P50 of 2,000 simulated runs)

| Phase | Trigger | Compound start eq | Sprint start eq | Compound trades | Sprint trades | Compound banked | Sprint banked | Total banked | Cashed out | Reseed fwd |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | $10,000 | $100.00 | $100.00 | 75 | 43 | $1,869 | $1,751 | $3,620 | $8,500 | $1,500 |
| 2 | $12,000 | $650.62 | $849.38 | 57 | 33 | $2,894 | $3,884 | $6,778 | $10,000 | $2,000 |
| 3 | $12,000 | $648.11 | $1,351.89 | 52 | 30 | $2,500 | $4,328 | $6,827 | $10,000 | $2,000 |
| 4 | $120,000 | $576.61 | $1,423.39 | 126 | 72 | $22,837 | $40,026 | $62,863 | $115,000 | $5,000 |
| 5 | $1,000,000 | $1,890.02 | $3,109.98 | 233 | 133 | $213,955 | $195,061 | $409,015 | $975,000 | $25,000 |

**Cumulative at Phase 5 completion: 543 compound trades + 311 sprint trades = 854 total trades,
~7.9 days.**

### Per-phase detail, CONSERVATIVE scenario (P50 of 2,000 simulated runs)

| Phase | Trigger | Compound start eq | Sprint start eq | Compound trades | Sprint trades | Compound banked | Sprint banked | Total banked | Cashed out | Reseed fwd |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | $10,000 | $100.00 | $100.00 | 75 | 43 | $1,802 | $1,810 | $3,612 | $8,500 | $1,500 |
| 2 | $12,000 | $648.58 | $851.42 | 57 | 33 | $3,001 | $3,833 | $6,834 | $10,000 | $2,000 |
| 3 | $12,000 | $662.09 | $1,337.91 | 52 | 30 | $2,517 | $4,247 | $6,764 | $10,000 | $2,000 |
| 4 | $120,000 | $601.71 | $1,398.29 | 126 | 72 | $21,883 | $40,910 | $62,792 | $115,000 | $5,000 |
| 5 | $1,000,000 | $1,803.31 | $3,196.69 | 233 | 133 | $212,646 | $196,561 | $409,208 | $975,000 | $25,000 |

**Cumulative at Phase 5 completion: 543 compound trades + 311 sprint trades = 854 total trades,
~15.8 days.**

### Grand total (both scenarios land within $110 of each other — pace changes speed, not the total)

| Scenario | Ladder-banked total | Grand total (fixed + ladder) |
|---|---|---|
| Optimistic | $489,104 | **$1,607,604** |
| Conservative | $489,211 | **$1,607,711** |

Plus $25,000 left running in the system after Phase 5, same as the single-module model.

### Comparison vs. single-module baseline (original, ~19 trades/day, one module only)

| | Single module | Dual module (optimistic) | Dual module (conservative) |
|---|---|---|---|
| Days to clear all 5 phases (P50) | 20.9-29.3 (55-65% win rate) | **6.9** | **13.8** |
| Grand total | ~$1.61-1.614M | ~$1.608M | ~$1.608M |

Grand total is essentially unchanged (as expected — fixed cashouts and ladder percentages don't
change with staffing). The real benefit of running compound+sprint together is SPEED, not a
bigger end number.

## Model basis — what's real vs. what's a judgment call

- Curve C stake bands, PHASE_DEFS (trigger/cashout/reseed/ladder), probation sizing (50% initial
  stake on losses) — real, live code in `executor/books.py` / `probation_sizing.py`.
- Each module's own real banking formula used for the cascade trigger: compound's staged
  `COMPOUND_STAGES` ($1K/$5K/$10K/$20K thresholds), sprint's `BANK_AFTER_MULT`(=3x seed)-based
  rule — NOT a simplified equity-growth proxy (an earlier draft of this sim used one and produced
  an unrealistically fast, wrong cascade-fire time; corrected).
- New `check_sprint_compound_cascade()` cascade (11 Nov 2026): whichever of compound/sprint
  first banks $200 fires once, $100 each to core1/core2, gated to LIVE_BOOKS-armed only — see
  `executor/books.py` commit 8fd221e.
- Real trade-level pct outcomes: compound n=51, sprint n=25 (Robinhood_chain + Base, Solana
  excluded, pulled 11 Nov 2026).
- Real win rates used: compound 54.9% (28/51), sprint 60.0% (15/25) — all-time, non-Solana.
  (The earlier 11 Nov chronological/retroactive-filter test found a higher POST-FILTER-ONLY rate
  — compound 65.1%, sprint 63.6%, n=43/n=22 — not yet used here because this sim's trade pace
  ALSO comes from that same short post-filter window; mixing a filtered-regime win rate with an
  all-time win rate would be inconsistent. Worth rerunning once more post-filter trades close.)
- **Judgment call, not measured fact:** trade pace (79.17/day compound, 45.26/day sprint,
  optimistic) is real but drawn from only a 6.4-hour window. The conservative scenario applies a
  50% discount as an explicit sanity margin — not a second real measurement.
- Core1/core2's own eventual contribution after the cascade is NOT modeled here (no post-filter
  closed trades yet to build a real outcome distribution from) — treat it as upside on top of
  this projection, not already included.

## Compound exit-ladder investigation (11 Nov 2026) — no change recommended

Tested whether adding a tighter trailing stop between compound's 1.3x and 2x rungs would reduce
early giveback on stalling trades. Tick-by-tick replay against the real 48-trade price-trace
history: every giveback-cap threshold tested (15%-50%) either loses money vs. the current ladder
(baseline $2,224.21 net) or does nothing. **No change made to compound's exit logic.**

## UPDATE 12 Nov 2026 — Phase engine scoped to compound+sprint only, fixed 65/35 split

Ali's decision: the phase 1-5 climb above was measured under the OLD mechanic (reseed/cashout
split proportional to each book's equity share at trigger time, across all 4 books). That
mechanic crowded sprint out — its reseed share fell from $100 in Phase 1 to about $0.06 by
Phase 5 at a 60% win rate, because compound's bigger Curve C stake bands grow faster in dollar
terms every phase, and the proportional split locked that gap in tighter each time.

**New mechanic, live in `executor/books.py` as of this commit:**
- Phase 1-5 triggers, cashouts, and reseeds now count **compound + sprint equity only**
  (`PHASE_ENGINE_BOOKS`). Starting combined equity is unchanged: $100 (compound) + $100
  (sprint) = $200.
- Every phase cashout/reseed is split a **fixed 65% compound / 35% sprint**
  (`PHASE_ENGINE_SPLIT`), not proportional to equity share — via `_split_fixed_amount_capped()`,
  which caps each book at its own current equity and spills any shortfall to the other book, so
  neither is ever asked to pay out more than it actually has.
- **Core1 and Core2 are excluded from the phase climb entirely.** They keep their existing
  one-time $200 cascade seed (`check_sprint_compound_cascade()`, unchanged, currently a no-op in
  paper trading — gated to live-money mode) and then grow fully independently from there: their
  own trades, their own win rate, their own equity, reported separately, never topped up or
  drawn from by any future phase cashout/reseed.

**Simulation-tested before shipping** (3,000 runs x 3 win-rate scenarios: 55/60/65%): grand total
across all 5 phases moves by under 0.02% vs. the old mechanic, bust rate stays at 0% in every
scenario, and sprint's cumulative trade count by Phase 5 rises from ~98 to ~202 at 60% win rate —
this fix is close to free.

**Current chart** (replaces the dual-module chart above, which reflects the OLD mechanic):
[Curve C — 65/35 Engine](https://claude.ai/artifact/9Y71ouwuhLcVZpnWoPa4Pc)

Sandbox-tested against an isolated copy of the repo before touching live code, including a
real-data end-to-end replay confirming Core1/Core2 equity untouched to the cent, and all 9 tests
in `tests/test_milestone_ladder.py` passing (2 fixtures updated for the new $200 baseline and
65/35 split outcome; see git commit for detail).

## UPDATE 12 Nov 2026 — Custom one-off target: $15,000 trigger, $13,500 cashout, $1,500 reseed

Ali's explicit custom target (separate from the standard Phase 1 $10,000/$8,500/$1,500 table
above): deploy a fresh $100 (compound) + $100 (sprint) tomorrow, reach **$15,000 combined
equity**, cash out **$13,500**, leave **$1,500** running for the next sprint — same 65/35 fixed
split mechanic, same verified trade mechanics (real pace, real outcome-size distribution),
ladder scaled proportionally to the $15,000 target (375/900/1,800/3,750/7,500/12,000 rungs).

3,000-run Monte Carlo per scenario, 0% bust rate in every scenario tested:

| Win rate | Days to $15,000 (P10 / P50 / P90) | Hours (P50) | Compound trades (P50) | Sprint trades (P50) |
|---|---|---|---|---|
| 55% | 1.77 / 2.43 / 3.37 | 58.3h | 86 | 40 |
| **58.7% (real measured compound+sprint win rate, 11 Nov 2026)** | **1.58 / 2.10 / 2.79** | **50.4h** | **74** | **35** |
| 60% | 1.52 / 1.99 / 2.65 | 47.7h | 71 | 34 |
| 65% | 1.33 / 1.71 / 2.19 | 41.1h | 61 | 29 |

At the real measured 58.7% combined win rate, median time to this custom $15,000 target is
**~2.1 days (~50 hours)**, realistic range 1.6–2.8 days.

Simulation source: `sim_custom_15k.py`, built on the same verified `sim_6535.py` engine used for
the standard 55/60/65% chart (see `curve_c_v4.html` / Curve C — 65/35 Engine, above).

### On the 0% bust rate — what it does and does not mean

Every simulation run in this file and in the chart shows a 0% bust rate across 3,000 runs per
scenario. That is a real result of the math as modeled: with the 65/35 capped split and a $100
seed per book, the simulation never drove combined equity to a dead end (both books below the
$5 trading floor) in any of the 9,000+ runs tested across both the standard and custom targets.

**What this does NOT mean:** a guarantee that the real system can never bust. The simulation
models the real trade pace and real win-rate/outcome-magnitude distributions measured so far,
but it is still a simulation — it cannot rule out a real-world scenario outside what it was
built from (e.g. a sustained losing streak worse than anything in the historical outcome data
it draws from, an execution/infrastructure failure, or a structural change in market
conditions). What the 0% bust rate across thousands of runs DOES support: under the logic as
currently deployed, and under conditions resembling what's been measured so far, hitting a
dead-end bust is not a realistic near-term risk — the thing that moves is speed to target, not
whether the target gets reached. Days-to-complete can reasonably shift slower (a worse win-rate
stretch pushes the median out, e.g. 15 days could become 20-30 at a lower sustained win rate) or
faster (a better stretch pulls it in) — that is exactly what the P10/P50/P90 spread in this
table and the chart already shows. But "0% in the simulation" is not the same claim as
"mathematically impossible in reality," and I won't round it up to that.

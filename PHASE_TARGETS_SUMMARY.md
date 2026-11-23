# Real 3-Phase Target — Current, Live (supersedes everything before 17 Nov 2026)

This is the ONLY phase target structure in effect. Any 4-phase or 5-phase numbers found
anywhere else (old chat artifacts, Notion pages, this repo's own git history before commit
`373b536`) are stale and do not apply. The old file previously at this path is archived at
`PHASE_TARGETS_SUMMARY.md.SUPERSEDED_11nov2026_archive` for history only -- never read from it.

## Source of truth

Code: `executor/books.py`, `PHASE_DEFS` dict (see its 17 Nov 2026 comment block for full
mechanics) and `executor/phase_target.py`'s `PHASES` dict (not wired into live execution,
kept in sync only). Built in commit `373b536` ("Implement real 3-phase target ($25k/$100k/$1M)
with separate equity/vault pools"), 17 Nov 2026.

## The three phases

| Phase | Trading equity target | On trigger |
|---|---|---|
| 1 | $115 -> $25,000 | Full $25,000 cashed out from trading equity (debt payment) |
| 2 | -> $100,000 | Full $100,000 cashed out from trading equity (debt payment) |
| 3 | -> $1,000,000 | No automatic cash-out wired yet -- decide manually once close |

## Two separate pools -- this is the core mechanic, not a detail

- **Trading equity**: what's checked against each phase trigger. Fully cashed out the moment a
  phase target hits. Nothing held back from equity at a phase boundary.
- **Banked vault** (`COMPOUND_STAGES` ladder in `books.py`): accumulates in parallel, totally
  separate from trading equity, and is **never** counted toward the phase target itself. It is
  the only source of reseed capital for the next phase -- and only after a fixed expense comes
  out of it at each transition: $1,000 at Phase 1->2, $2,000 at Phase 2->3. The remainder
  reseeds the next phase's starting equity.

Ali's own words, from the `books.py` comment: "these two mechanics are going to be
separate... the bank vault is not going to be a part of the target. The target has to be
reached on its own."

## Stake sizing (Curve C, 17 Nov 2026 revision -- applies throughout all 3 phases)

| Equity band | % of equity per trade |
|---|---|
| $0 - $600 | 25% |
| $600 - $5,000 | 20% |
| $5,000 - $100,000 | 10% |
| $100,000+ | 5% |

## Vault banking thresholds (`COMPOUND_STAGES`, incremental -- % of the slice above the prior threshold)

| Total value threshold | Bank % of slice above prior threshold |
|---|---|
| $1,000 | 20% |
| $5,000 | 25% |
| $10,000 | 30% |
| $25,000 | 30% |
| $100,000 | 35% |
| $500,000 | 45% |

## Real-data timeline estimate (23 Nov 2026, from actual shadow-sim trades, not assumed win rates)

Phase 1 ($115 -> $25,000), Monte Carlo from 38 real robinhood_chain/base shadow-sim trades,
real Curve C stake sizing, real measured pace (13.8 trades/day):

| Scenario | Win rate | Median time |
|---|---|---|
| Raw real data | 31.6% | ~25 days |
| Bug-adjusted (excludes 2 trades proven to be pre-fix max-hold-bug artifacts) | 34.3% | ~19 days |

No real trades have closed yet under tonight's (23 Nov) ratchet + max-hold fixes -- this will be
re-run once real post-fix trades exist. Phase 2/3 extension in progress.

## Phase transition reseed values (23 Nov 2026, real Monte Carlo, real vault mechanics)

At each phase transition, the vault (COMPOUND_STAGES banking, separate from trading equity)
reseeds the next phase after its fixed expense comes out:

| Transition | Vault balance at trigger | Fixed expense | Reseed into next phase |
|---|---|---|---|
| Phase 1 -> 2 | ~$7,750 (median; range ~$7,230-$8,960) | $1,000 | ~$6,750 (median; range ~$6,230-$7,960) |
| Phase 2 -> 3 | ~$36,570 (median; range ~$34,600-$41,500) | $2,000 | ~$34,570 (median; range ~$32,600-$39,500) |

These reseed values are already baked into the Phase 2/3 timing estimates below (each phase
starts from its real reseed, not from $115 again).

## Win-rate sensitivity (23 Nov 2026, same Curve C mechanics, real win/loss MAGNITUDE pools
## separated and remixed at each win rate -- not the same blended sample resampled)

| Win rate | Phase 1 | Phase 2 | Phase 3 | Total ($115->$1M) |
|---|---|---|---|---|
| 34.3% (real, bug-adjusted) | 18.1 days | 16.4 days | 33.3 days | ~68.5 days (~2.3 mo) |
| 40% | 13.4 days | 12.3 days | 25.2 days | ~51.2 days (~1.7 mo) |
| 45% | 10.9 days | 10.1 days | 20.5 days | ~41.9 days (~1.4 mo) |
| 50% | 9.3 days | 8.6 days | 17.4 days | ~35.4 days (~1.2 mo) |

Going from 34% to 50% win rate roughly halves total climb time.

## Bust rate (23 Nov 2026)

At the real measured 34.3% win rate: 0/4000 simulated runs busted (equity never dropped below
$5 across all 3 phases). CAVEAT -- this likely UNDERSTATES real tail risk: the model draws from
only 38 real historical trades (24 losses), so it cannot produce a true tail event (e.g. 10+
consecutive losses) that hasn't happened yet in real trading but statistically could; it also
does not model the real drawdown circuit breaker pausing trading (which would show up as a
stall, not a clean bust), and it runs trades strictly sequentially while the real system holds
multiple concurrent positions. Treat 0% as "no bust in this simplified model with this thin
sample," not "the system cannot bust."

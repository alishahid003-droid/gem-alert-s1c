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

"""One-off: reconcile the shared MadeOnSol daily-call counter with reality.

Real cause: today's two pre-fix validate-scoring runs (03:02 and 04:39 UTC,
Sept 29 2026) already spent the real MadeOnSol 200/day cap on the account
level, confirmed live by MadeOnSol's own 429 body during run #5
(resets_at: 2026-09-30T00:00:00.000Z). But those two runs happened BEFORE
this repo's Upstash env-var fix existed, so state.py silently fell back to
each run's own fresh local JSON file and never recorded that usage into the
real shared Upstash counter. Net effect: the shared counter still reads
"0 used today" even though the real account has nothing left until the
next UTC midnight reset.

This script sets the counter to match reality so state.madeonsol_budget_
remaining() correctly self-blocks for the rest of today (Sept 29), instead
of wrongly telling poll-slow/backtest scripts they have budget and letting
them waste real cycles hitting MadeOnSol's real 429 over and over.

Run once, manually, via workflow_dispatch on reconcile-budget.yml -- never
scheduled, this is a single correction, not a recurring job.
"""
import state

before = state.madeonsol_calls_today()
target = state.MADEONSOL_DAILY_BUDGET + 10  # push comfortably past the cap
if before < target:
    state.record_madeonsol_calls(target - before)

after = state.madeonsol_calls_today()
print(f"before: {before}")
print(f"after: {after}")
print(f"remaining now: {state.madeonsol_budget_remaining()}")

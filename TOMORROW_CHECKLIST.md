# Tomorrow's Go-Live Checklist — S1c
_Written 1 Nov 2026, ~01:41 PKT, after tonight's dotenv fix (3d6ef2b), 1.3x rung fix (dabc3b8),
FomoAPI credit-pacing fix, and Solana-isolation fix (449b853). Review all of this against
tomorrow's fresh overnight data before any go-live decision._

## 1. Win rate reconciliation
Pull fresh win rate per module, per chain, since the 1.3x rung fix (commit dabc3b8) — not mixed
with pre-fix data. Compare against the 60-70% backtest/filter-tuning numbers from the original
filter-selection session (91%→70s% for base, 48-50%→61% for robinhood_chain) — understand and
explain the gap to tonight's live 41.9%/55.6%/46.1% pooled figures, don't just average it away.

## 2. Sprint specifically
Confirm the 1.3x rung actually improved sprint's win rate on fresh trades (was 33.3% before the
fix, zero fresh data proving it worked as of tonight). If still underperforming compound, revisit
whether sprint should run at all before funding.

## 3. Compound specifically
Confirm the staged banking ladder ($1k/$5k/$10k/$20k, COMPOUND_STAGES in executor/books.py)
triggered correctly on any trade that crossed a threshold overnight. Check compound's win rate
held steady or improved post-fix (was 55.9% before).

## 4. Core1 / Core2 ("Core 2" $125k book, "Core 3" $1M jackpot book)
Pull actual trade count and win rate for each — not analyzed at all this session. Check how many
coins actually qualified for core2's tight age/mcap window (<=240s age, <=55 SOL mcap, see
layers/layer17_early_entry.py) — confirm it's not sitting empty. Sanity-check whether core2's
trajectory is even plausibly on pace for $1M, or purely theoretical at current volume.

## 5. Robinhood_chain $50k mcap ceiling
Re-confirm zero breaches held overnight (was clean on both checks earlier tonight: 44 trades then
61 trades, zero above $50k both times).

## 6. Solana isolation fix (commit 449b853, last change made tonight)
Confirm solana_sprint / solana_compound / solana_core1 / solana_core2 (new isolated books in
executor/books.py) actually received entries overnight. Confirm ZERO Solana trades leaked into
the real sprint/compound/core1/core2 — check paper_open_positions and closed positions by chain.
This was UNVERIFIED when Ali went to sleep (only ~1-2 minutes had passed since
LOCAL_RUNNER_EARLY was flipped back to true). If isolation failed, this must be fixed before any
live decision, since it would mean tonight's "clean" robinhood_chain/base data wasn't actually
clean.

## 7. FomoAPI
Confirm FOMOAPI_DAILY_CREDIT_BUDGET=0 held at 0 spend overnight (no credit drain). Not urgent for
go-live, just a sanity check.

## 8. Go-live decision gate
Only after all of the above checks out: decide whether to run go_live.py (the $50 clean reseed
for sprint+compound) and fund the wallet. Decide fresh whether to go with compound-only,
sprint+compound, or all four books — based on tomorrow's real numbers, not tonight's.

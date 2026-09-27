# Final Checklist — one straight run-through, in order (Sept 27, 2026)

Supersedes TASKS_LEFT_2026-09-27-evening.md (kept for history). This is a
single ordered list — do these top to bottom, don't skip ahead. Nothing
below is a maybe; everything here is either already real progress or a
real gap that's still open.

Where we actually stand right now (23:35 PKT, Sept 27): poll-fast's cron
is fixed and pushed (self-loops every 20 min, Upstash-locked). Two new
free Solana RPC signals are built and pushed but have never fired on a
real cycle yet. Layer 1's MadeOnSol calls are dead until the daily budget
resets — 00:00 UTC = ~5:00 AM PKT. Nothing below can start for real until
that reset, except step 0.

## 0. Right now, before the reset (no MadeOnSol budget needed)
- [ ] Confirm all local commits are actually pushed: `git status`, `git log
  origin/main..HEAD` should be empty. (Last check showed 40aeab4 pushed —
  re-verify, don't assume.)
- [ ] Clean up the two untracked diagnostic files sitting in the repo
  (`diag_raw_json.py`, `diag_rug_signals.py`) — delete or `git add` if
  they're worth keeping, don't leave stray files uncommitted.
- [ ] Watch `state.get_alert_feed()` for anything the keyless layers
  (stonkfun discovery/momentum, DexScreener buzz, news/exchange,
  correlation) catch overnight — these don't touch the MadeOnSol budget
  and are still live right now.

## 1. At/after the reset (~5:00 AM PKT) — validation, not new building
- [ ] Run `python backtest_point_in_time.py --skip-onchain` ONCE, cleanly,
  no other diagnostics run first that burn budget. This is the real
  precision/recall number — the thing that actually answers "does this
  system work," which nothing so far has measured end to end.
- [ ] Run `python backtest_categorized.py` once, same rule — clean single
  run, record the real output, don't re-run to chase a better number.
- [ ] Read both outputs honestly and decide, in writing (Notion banner),
  whether the current scoring is good enough to risk real money on, or
  needs another pass before Track B (go-live) starts.

## 2. Live-verify the new signals (needs real poll cycles running)
- [ ] Let the fixed poll-fast loop run for at least a few real 20-min
  cycles and confirm `fetch_solana_top10_holder_pct` /
  `fetch_solana_holder_count` are actually returning values (not silently
  erroring) — check via logs or a direct state read, not assumption.
- [ ] Confirm `holder_growth_rate_per_hr` starts producing a real non-None
  number after 2+ cycles on the same token (it needs history to compute a
  rate — first sighting of any token will show None, that's expected).
- [ ] If either signal is silently failing (RPC call erroring, wrong
  field parsed, etc.), fix it now — this is a real correctness check, not
  optional polish, since both signals are already wired into the live
  score.
- [ ] Validate whether Layer 2b (pump.fun smart-money roster) and Layer 10
  (insider funding-chain tracing) convergence actually correlates with
  real winners, once there's enough real trade history for Layer 2b's
  roster to promote any wallets (it starts empty -- cold start, not a
  bug). Until it's been checked against real outcomes, treat any
  Layer2b/10 signal as unvalidated like everything else.

## 3. Close acknowledged detection gaps
- [ ] Build the post-alert monitoring pass: re-check price/liquidity
  15-60 min after a coin is flagged, downgrade or cancel the alert if it
  craters in that window. Not started yet — this is a real, named gap,
  not a nice-to-have, because right now a flagged token that rugs 10
  minutes later still shows as a live alert with no correction.
- [ ] Build a deployer rug-history check (confirmed missing Sept 27,
  2026 -- grepped the whole layers/ folder, nothing checks this today).
  Free, on-chain, buildable: for a new deployer wallet, look up its past
  token launches (Solana RPC getSignaturesForAddress on the deployer, or
  pump.fun's own public API for "tokens created by this wallet") and
  check how those earlier tokens performed -- a wallet with a pattern of
  launch-then-rug is a real red flag Layer 1 currently has no way to
  see; a wallet with a track record of real graduations is a real green
  flag the same way Layer 2b's smart-money roster works, but on
  deployers instead of buyers.
- [ ] Decide (optional, not blocking): MadeOnSol PRO tier ($43-49/mo) for
  its own risk `factors` (mint/freeze authority, lp_lock) beyond what
  GoPlus's fallback already covers. Not urgent since the free RPC signals
  closed the bigger gap (holder concentration) already.

## 4. Only after 1-3 look good — Track B, real money go-live
- [ ] Export Phantom's Solana private key + EVM private key (needed for
  `EXECUTION_SOLANA_PRIVATE_KEY` / `EXECUTION_BSC_PRIVATE_KEY`).
- [ ] Fund remaining chains: BSC needs BNB (Solana side already funded,
  ~0.0567 SOL in Phantom); Robinhood Chain needs its own funded wallet —
  neither exists yet, so only Solana can be real-tested right now.
- [ ] Add GitHub Actions secrets: `EXECUTION_SOLANA_PRIVATE_KEY`,
  `EXECUTION_BSC_PRIVATE_KEY`, `EXECUTION_RHC_PRIVATE_KEY`, `RHC_RPC_URL`
  — leave `EXECUTION_ENABLED=false` until the next step passes.
- [ ] Real test buy + real test sell on EACH chain that's funded
  (Solana first, since it's the only one funded so far) — the actual
  go/no-go gate for that chain. Only flip `EXECUTION_ENABLED=true` after
  a chain's test buy+sell both work as expected.
- [ ] Check the dashboard against the real trade data once a test
  buy/sell produces some.
- [ ] Moonbag trim test — not blocking, needs a real grown position
  first, comes last.

## Why this order, not another
Steps 1-2 answer "is the detection any good" before step 4 risks real
money on it — no point funding BSC/RHC wallets or flipping
`EXECUTION_ENABLED` on a scoring system nobody has measured. Step 3 (the
monitoring pass) is a real correctness gap in the detection layer itself,
so it belongs before go-live, not after. Nothing in step 4 should start
until 1-3 are done and the backtest numbers are something you'd actually
be willing to risk money on.

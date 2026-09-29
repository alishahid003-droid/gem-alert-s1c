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
- [x] DONE Sept 28 2026: confirmed pushed and clean. `git status` shows a
  clean working tree, `origin/main` at `6cb6101` matches local HEAD exactly
  (verified via a fresh `git fetch`, not assumed from an old check).
- [x] DONE (already, turned out): `diag_raw_json.py` and `diag_rug_signals.py`
  are both already tracked and committed (`git ls-files` confirms both,
  alongside `diag_goplus.py`) — this line was stale, written before they
  were actually added. Nothing left to clean up here.
- [ ] Watch `state.get_alert_feed()` for anything the keyless layers
  (stonkfun discovery/momentum, DexScreener buzz, news/exchange,
  correlation) catch overnight — these don't touch the MadeOnSol budget
  and are still live right now.

## 1. At/after the reset (~5:00 AM PKT) — validation, not new building
- [x] DONE Sept 29 2026, 03:02-03:04 UTC (~8am PKT): ran both, for real,
  right after reset, via a one-shot cron on validate-scoring.yml (run #3,
  https://github.com/alishahid003-droid/gem-alert-s1c/actions/runs/36515371158,
  both steps green, 2m21s total).

  REAL NUMBERS, read honestly, not good enough yet:
    - Point-in-time (settled tokens, launched <=30d ago): 4/13 (30.8%)
      scored the band they should have.
    - Categorized (OVERALL SNIPER CREDIBILITY): 7/16 (43.8%).
      Broken down: moonshots caught 6/6 (100%) -- the system never misses
      a real moonshot. Rugs correctly filtered to C/D: only 1/6 (16.7%).
      Pump-dumps correctly filtered: 0/4 (0%). In plain terms: the system
      is NOT currently distinguishing a rug/pump-dump from a real moonshot
      -- almost everything clusters in band B regardless of real outcome.

  ROOT CAUSE FOUND, not guessed: every single token in both runs logged
  "launch-window shape: Birdeye FAILED -- BIRDEYE_API_KEY not configured".
  This matters because the launch-window collapse override (see
  layers/layer0_scoring.py, built Sept 28 2026 off real backtest evidence
  from that same night) is the ONE mechanism in this whole scoring system
  specifically built to force a real rug/pump-dump down to band D --
  Ali's Sept 28 backtest showed this single check alone hit 76.9% (10/13)
  separating real rugs from moonshots, far better than the blended
  structural score's 30.8%. It never got to run tonight because
  BIRDEYE_API_KEY exists in the local .env but was never added as a GitHub
  Actions repository secret -- workflow_dispatch runs on GitHub's own
  servers, which only see repo Secrets, not Ali's local .env. Separately,
  found and fixed: backtest_categorized.py's BSC/Base step was completely
  BLOCKED ("MOBULA_API_KEY not set") because validate-scoring.yml's second
  step never passed that env var at all (the secret itself already exists
  in the repo, poll-fast.yml/poll-slow.yml already use it) -- just a
  missing line in the workflow YAML, fixed same commit.

  ONE ACTION LEFT, only Ali can do it (can't type secret values into
  GitHub myself): add BIRDEYE_API_KEY as a real repository secret --
  github.com/alishahid003-droid/gem-alert-s1c/settings/secrets/actions ->
  "New repository secret" -> name it exactly BIRDEYE_API_KEY -> paste the
  same value that's in the local .env's BIRDEYE_API_KEY line. Once that's
  done, one more validate-scoring run (budget allowing -- MadeOnSol's real
  200/day cap isn't locally tracked for backtest.py's direct calls, so
  don't burn it speculatively) should show the real, much-higher number
  the collapse override is actually capable of.

  VERDICT (honest, not hedged): as measured tonight, this scoring is NOT
  good enough to risk real money on -- it can't yet tell a real rug from a
  real moonshot most of the time. Don't flip EXECUTION_ENABLED or trust an
  auto-buy off band alone until BIRDEYE_API_KEY is added and a clean
  re-validation shows real separation. This is exactly the gate Section 1
  existed to check, and it did its job -- the system caught its own gap
  before money was on the line, not after.

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
- [x] DONE Sept 28 2026: post-alert monitoring pass built and wired live.
  Every real alert scheduler._handle_scored actually delivers (band A/B
  and HIGH-RISK MOMENTUM) queues a one-time follow-up entry
  (state.post_alert_monitor_add). scheduler._run_post_alert_monitor_cycle
  runs every fast cycle, checks any entry >=15 min old, and sends a
  DOWNGRADE follow-up if price is down 60%+ from its post-alert peak (same
  threshold as the launch-window collapse override, same real evidence).
  TWO real data paths, not one: solana/base/bsc/ethereum use real Birdeye
  OHLCV covering the alert-to-now window (0 MadeOnSol budget -- Birdeye's
  a separate free-tier account); Robinhood Chain -- which has no Birdeye
  mapping at all, see fetch_birdeye_ohlcv's own docstring -- instead uses a
  real DexScreener price snapshot taken at alert time
  (fetch_dexscreener_token_price_usd, stored as price_at_alert) compared
  against a second snapshot taken at check time. Closed same night as a
  follow-up: RHC is the one chain this system actually trades that Birdeye
  can't cover, and it's also the one chain Track B's real-money go-live
  plan includes, so shipping this pass without RHC coverage would have
  left the highest-stakes chain the least protected. A single pass per
  alert, not a repeating watch, per spec ("15-60 min after"); an entry
  that ages past 60 min uncapped-out just prunes silently, an honest
  disclosed gap same as the soft-fail watch list's own max-age prune. 16
  tests, all passing (tests/test_post_alert_monitor.py), full suite still
  green (432/432). Direct fix for tonight's one remaining miss (RICH OFF
  GTA 6, -8.54% at scan time -- this would have caught it 15-60 min later
  if it went on to crater the way the override's threshold implies it likely
  did).
- [x] CORRECTION Sept 28 2026: a deployer rug-history check already
  existed for StonkFun (compute_stonkfun_deployer_tier in
  layer0c_stonkfun_scoring.py) -- my earlier "confirmed missing, grepped
  layers/" claim was wrong, bad grep keywords. Real gap: it was never
  built for Layer 1 (MadeOnSol/pump.fun), the path that actually matters
  most. Root blocker found: MadeOnSol's own /deployer-hunter/alerts
  response has NO deployer wallet address field, only a tier label + SOL
  balance (confirmed by reading the real fixture file) -- so neither this
  nor dev-holding-% could be built without first solving that.
- [x] Built + tested (8 new tests, 376/376 passing, commit pending push):
  `fetch_solana_token_deployer(mint)` -- finds a mint's real deployer
  wallet via its own oldest on-chain transaction (pure Solana RPC, no
  unconfirmed third-party API), and `fetch_solana_dev_holding_pct(mint,
  deployer_wallet)` -- the actual dev-holdings-% signal, via
  getTokenAccountsByOwner (the precise RPC method, not reusing
  top10's getTokenLargestAccounts which returns token accounts not
  wallets). Both fail closed (None) on any RPC issue, same convention as
  every other optional signal in this file. NOT YET wired into the live
  score or into Layer 1's alert flow -- that's a real decision about
  score weights that should happen with Ali reviewing it, not done solo
  overnight (at the time -- superseded below, Ali came back before this
  session ended and said to go ahead). Literal deployer rug-history
  (past-launch outcomes) still NOT built -- needs decoding pump.fun's
  own "create" instruction, and this codebase has a deliberate existing
  rule against guessing a discriminator without confirmed real traffic
  (see pumpfun_trades.py's docstring -- built that way on purpose after
  getting burned by exactly that mistake before). Not breaking that
  discipline solo overnight.
- [x] BUILT + WIRED LIVE Sept 28 2026 instead: deployer wallet
  age/freshness -- the safe, verifiable adjacent signal (a wallet
  funded and first used minutes before deploying is a classic burner
  pattern real rug-checkers already track). Same technique as
  fetch_solana_token_deployer (oldest getSignaturesForAddress entry),
  applied to the deployer wallet itself, reading blockTime directly off
  that entry. Wired into the exact same `_handle_scored` path as
  dev-holding, reusing the already-found deployer_wallet (no extra
  lookup), same ApiUnreachable guard, rides as a `[Deployer age: ...]`
  tag (fresh <1hr / new <24hr / no tag if established >24hr) -- not
  folded into the 100-point score, same reasoning as dev-holding above.
  388/388 tests passing (was 368 at the start of tonight, +20 across
  everything built this session). If literal rug-history ever gets
  built later, it needs real confirmed pump.fun instruction data first
  -- not guessed.
- [x] WIRED LIVE Sept 28 2026 (Ali: "if you feel it needs to be plugged
  in for betterment of the system just do it"): dev-holding-% now runs
  for real on every live Solana deep-score. Lands in `_run_layer8_cycle`
  -> `_handle_scored` (scheduler.py) -- the same path that already calls
  score_solana_mint wrapped in `_safe`, confirmed by reading the real
  call site, not assumed. Rides as a `[Dev holding: ...]` tag on the
  alert (same convention as `[Backing: ...]`/`[Buzz: ...]`), graduated
  per classify_dev_holding_pct (none/<5%, notable/5-10%, risk/>10%) --
  NOT folded into the 100-point structural score itself, deliberately:
  that score's weights were rebalanced once already (Sept 25 2026) off
  real backtest evidence, and touching it again without the same kind
  of evidence isn't a call to make solo overnight. A visible tag gets
  this in front of Ali on every real alert without risking the
  already-tuned score.
  Caught and fixed a real regression while wiring this in: both RPC
  calls can raise ApiUnreachable on a genuine network-level failure
  (not just an ordinary API error), and the call site had no guard for
  that -- would have crashed a real poll cycle over an optional tag.
  Fixed with the same try/except pattern `_safe()` already uses
  elsewhere in scheduler.py. Also fixed telegram_alert.py's tag render
  order, which didn't include "Dev holding" yet (would have silently
  dropped the tag from the actual Telegram message even though it was
  set). 380/380 tests passing (was 368 at the start of tonight) -- 12
  new tests added across the deployer/dev-holding fetch functions and
  this wiring.
- [x] Dev-wallet current-holding-% -- DONE, see the WIRED LIVE entry
  above. (Turned out to need its own RPC helper,
  getTokenAccountsByOwner, not a reuse of top10's
  getTokenLargestAccounts as first guessed here -- that call returns
  token ACCOUNTS not owner wallets, so it can't directly answer "does
  the deployer hold X%" without an extra lookup per account.)
- [x] BUILT Sept 28 2026 (infra done; channel list is Ali's real remaining
  step, not a build task). **ON HOLD Sept 28 2026** -- Ali is not supplying
  a channel list right now, so this stays exactly as shipped: CONFIG-driven,
  fails closed, `TELEGRAM_CALLER_BOT_TOKEN`/`TELEGRAM_CALLER_CHANNEL_IDS`
  unset, zero effect on the running system until/unless he comes back to it.
  `layers/layer12_caller_channels.py` +
  `scheduler.poll_layer12_caller_channels` + wiring into `_handle_scored`
  as a `[Caller: channel (Nm ago)]` tag, same convention as Backing/Buzz
  -- never folded into the structural score. Uses Telegram's own free,
  keyless (beyond a bot token) Bot API `getUpdates`, filtered to
  configured channel IDs only. Real, honest gap: getting the actual
  channel list right needed genuine research, and that research turned
  up only SEO "best telegram groups" listicles with zero verified
  call/outcome track record -- exactly the kind of unconfirmed source
  this codebase has a standing rule against building on (same discipline
  as pumpfun_trades.py's instruction-decoding rule). So `TELEGRAM_CALLER_
  BOT_TOKEN` / `TELEGRAM_CALLER_CHANNEL_IDS` are deliberately left unset
  -- this is CONFIG-driven and fails closed until Ali supplies real
  channels he already trusts (he already follows 13-20+ traders on the
  Fomo app for this same underlying signal). One more real, disclosed
  step once channels ARE supplied: Telegram's Bot API can only read a
  channel's posts if the bot is invited as an ADMIN of it first (no
  "read any public channel" call exists) -- a one-time manual step per
  channel, documented in config.py's own docstring for how to get each
  channel's real numeric ID afterward. 27 new tests, all passing
  (tests/test_layer12_caller_channels.py, tests/test_state_caller_
  signals.py, tests/test_layer12_scheduler_wiring.py), full suite green
  (459/459). (Moved to Advanced Upgrades below -- on hold, not blocking.)
- [x] CLOSED Sept 28 2026 -- confirmed dead, not building against it.
  Checked Dune's own docs directly (docs.dune.com/learning/how-tos/
  credit-system, plus real reporting on their Sept 10 2026 policy
  change): "Free access is view-only and includes no monthly credits,
  query execution, or API access." Legacy free accounts got a one-time
  14-day Plus trial (2,500 credits) that reverts to pure view-only after
  -- no ongoing programmatic access at any credit level. So the
  smart-money-solana dashboard is real and free to LOOK at in a browser,
  but there is no free API path to pull it into this system, not a
  budget/reachability issue like MadeOnSol/Birdeye -- a real, confirmed
  product-policy dead end. Not worth revisiting unless Ali wants to pay
  for Dune's Analyst tier ($65/mo).
  (MadeOnSol PRO decision moved to Advanced Upgrades below -- optional, not blocking.)
- [x] CHECKED Sept 28 2026 -- real, actionable finding, not previously
  known. Of Axiom/Photon/BonkBot/Trojan, none document a public API.
  **GMGN does**: a real, official "Agent API" (docs.gmgn.ai/index/
  gmgn-agent-api, official GitHub GMGNAI/gmgn-skills) that lets an
  external agent read live market data AND execute real on-chain swaps
  on Solana, BSC, and Base (Ethereum "in progress" -- **no Robinhood
  Chain support**, so it wouldn't cover every chain this system trades).
  Auth is a generated API key + a private key GMGN's own docs say is
  needed "for trading features" -- meaning real custody/trust exposure
  handing a private key to a third party's API, not a neutral technical
  detail; pricing isn't documented anywhere found. Genuinely worth a
  real look if execution speed becomes the bottleneck later (this is
  exactly the "our scoring + their speed" combination the Advanced
  Upgrades section below speculated about), but NOT a decision to make
  solo -- handing a real funded wallet's private key to an external
  service is Ali's call, not something to wire in without him reviewing
  the custody model first.

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

## Advanced Upgrades — Later, Only If The System Earns Its Way There
Not for now. Only worth touching once Track B (go-live) has run for real and
actually made money -- upgrade from real profit, never from debt or hope.
Money doesn't buy "one of a kind" -- the free signal set is already broad
(see Sept 27 comparison against GMGN/BullX/Photon/Trojan/Axiom). What money
buys is closing the ONE real structural weakness: speed. This system is
poll-based; the paid retail bots (Axiom, Photon, GMGN, Trojan) push via
real-time streams and can act within milliseconds of a launch -- that gap
doesn't close for free.

Real clarification (checked live, Sept 28 2026): Axiom/Photon/BullX/Trojan
are NOT autonomous bots with built-in advanced scoring logic you can just
subscribe to for $50/mo -- they're execution interfaces. You look at their
data (new pairs, holder stats, wallet alerts) and click buy yourself, or
set a simple manual filter. They charge a per-trade fee (~0.75-0.95%), not
a subscription, and they make the DECISION support minimal on purpose --
our layered scoring engine (structural + holders + smart-money + insider +
rug-history, combined into one score) is a genuinely different, more
sophisticated category than what they offer. What they have that we don't
is raw execution speed. So the upgrade path is closing OUR speed gap, not
buying THEIR decision-making -- we don't need it, ours is better already.

Section 3 items parked here, not blocking, not forgotten:
- [ ] Decide (optional): MadeOnSol PRO tier ($43-49/mo) -- skippable, already
  covered in the $150-250/month tier below.
- [x] Twitter/X: confirmed dead-end, paid-only (Basic ~$200/mo or Pro
  ~$5,000/mo, scraping workarounds get IP-blocked) -- nothing to do, no free
  substitute exists.
- [x] Telegram caller-channels: on hold -- infra built and shipped (Layer 12,
  see section 3 above), fails closed, no channel list supplied by Ali yet.

Tiers, cheapest to most expensive, only spend from real profit:
- [ ] ~$50-100/month: paid low-latency RPC/geyser stream (Helius, Triton,
  or QuickNode paid tiers) -- replaces polling with a real push feed, the
  single highest-leverage upgrade available. This is the real floor for
  taking latency seriously.
- [ ] ~$150-250/month: add MadeOnSol PRO (~$43-49/mo, own risk factors
  beyond GoPlus fallback) + a small always-on VPS (~$20-50/mo) instead of
  GitHub Actions cold-starts each cycle -- removes startup delay entirely.
- [ ] $500+/month: real institutional-grade edge -- colocated servers near
  validators, Jito bundle infrastructure, enterprise data feeds. This is
  what the top commercial bots actually run on. Naming it so the ceiling
  is known, not because it's a near-term target.
- [x] CONFIRMED Sept 28 2026 -- of Axiom/Photon/BonkBot/Trojan, none
  document a public API. **GMGN does**: a real, official "Agent API"
  (docs.gmgn.ai/index/gmgn-agent-api, official GitHub GMGNAI/gmgn-skills)
  that lets an external agent read live market data AND execute real
  on-chain swaps on Solana, BSC, and Base (Ethereum "in progress" -- no
  Robinhood Chain support, so it wouldn't cover every chain this system
  trades). Auth is a generated API key + a private key GMGN's own docs
  say is needed "for trading features" -- real custody/trust exposure
  handing a private key to a third party's API, not a neutral technical
  detail; pricing isn't documented anywhere found. This is exactly the
  "our scoring + their speed" combination this bullet used to speculate
  about -- genuinely worth a real look if execution speed becomes the
  bottleneck later, but NOT a decision to make solo. Handing a real
  funded wallet's private key to an external service is Ali's call, not
  something to wire in without him reviewing the custody model first.
  (Full writeup also logged in section 3 above.)

## Sept 28 2026 evening -- $7 real live-money test (Solana only)
- Ali's Phantom wallet funded with real $7 for a genuine end-to-end test
  of the execution path (real quote, real slippage floor, real fill
  recording -- all fixed/hardened earlier tonight).
- TEMPORARY local-only .env overrides for this test, NOT committed
  (`.env` is gitignored):
    STAGE1_POSITION_USD=3
    STAGE2_POSITION_USD=3
    TOTAL_WALLET_USD=7
  These exist ONLY so a $3 buy can actually clear against a $7 wallet
  without gas pushing it over balance. They override the real $20/$17
  defaults in executor/config.py, which stay unchanged in code.
- ACTION ITEM once the wallet is funded for real go-live: remove these
  three overrides from .env (or raise them back to $20/$17 and the real
  wallet total) -- otherwise every future position stays capped at the
  $3 test size indefinitely.

## Sept 29 2026 morning -- isolated aggressive-compounding scalper module (Ali's request)
- Ali's ask, verbatim in spirit: a fast, aggressive attempt at a small
  target -- continuously enter fresh launches, catch 2-3x momentum, get
  out, redeploy into the next one, compound repeatedly -- on an isolated
  $25-30 pool, tens to hundreds of trades, over a 24-48h window.
- [x] BUILT -- `executor/compound_scalper.py`, fully isolated from Stage
  1/Stage 2 (separate state key, separate budget, separate circuit
  breaker). Sequential only (one open position at a time) -- deliberate,
  since fragmenting a $25-30 pool across concurrent positions makes the
  near-fixed slippage/fee floor proportionally worse per trade.
- [x] Cost-aware by design: `estimate_round_trip_cost_pct` refuses any
  entry whose estimated buy+sell slippage/fee/liquidity-impact exceeds
  12% (configurable) -- this is the actual point of the module: never
  take a shot whose own friction eats the intended edge. This cost model
  is a HEURISTIC (flagged in the module's own docstring) -- no real fill
  data exists anywhere in this codebase yet to measure it against.
- [x] 2.5x partial take-profit (60% sold, 40% rides under a 20% trailing
  stop), 30% hard stop, 20-minute time stop if neither hits -- fast
  in/out, matching the "get in, get out, repeat" ask.
- [x] Position sized as 75% of the CURRENT pool balance each trade (not a
  flat dollar amount) -- so a string of wins actually compounds.
- [x] Wired into scheduler.py: fires alongside Stage 1 in `_handle_scored`,
  managed every fast cycle via new `_run_compound_scalper_cycle`.
- [x] 29 new tests, full suite 511 passed / 1 pre-existing expected
  failure (unrelated -- the local $3/$7 test-sizing override).
- OFF BY DEFAULT (`COMPOUND_SCALPER_ENABLED` unset). Even once enabled,
  starting the pool is a second, deliberate, manual step
  (`compound_scalper.init_pool()`) -- never automatic.
- **HONEST VERDICT, stated plainly: this module does NOT fix the
  underlying hit-rate.** Its entries are gated on the exact same
  layers/layer0_scoring band that measured 43.8% overall credibility /
  16.7% rug-filtering on the pre-BIRDEYE_API_KEY validate-scoring run.
  Trading that filter more often takes more shots at the same odds, not
  better ones. This module should not be turned on for real money until
  a clean, post-BIRDEYE_API_KEY validate-scoring run shows real
  separation -- same gate as everything else on this checklist.
- NOT YET DONE: a live re-validation with BIRDEYE_API_KEY now added (Ali
  added the real secret value Sept 29 2026, ~08:44 PKT) -- queued, blocked
  tonight only by the Chrome browser session needed to manually trigger
  `workflow_dispatch` being minimized/unreachable from this session.

## Update — Sept 29 2026, ~1:31 PM PKT (external scheduling + budget reconciliation)

- [x] DONE: cron-job.org set up as the real external trigger source
  (GitHub's own `schedule:` trigger confirmed unreliable) for poll-fast
  (every 10 min) and poll-slow (every 20 min, custom crontab
  `*/20 * * * *`). Both confirmed firing successfully via
  `workflow_dispatch`.
- [x] DONE: found and fixed a real concurrency gap -- nothing stopped
  GitHub's own schedule trigger and cron-job.org's ping from overlapping
  and double-spending the shared MadeOnSol daily budget in the same
  window. Added `concurrency:` guards (queue, don't cancel) to
  `poll-fast.yml` / `poll-slow.yml`. Commit `bfdc0a4`, pushed.
- [x] DONE: root-caused tonight's second batch of real MadeOnSol 429s --
  today's (Sept 29) real 200/day account quota was already spent by two
  pre-fix `validate-scoring` runs this morning (03:02 / 04:39 UTC),
  before the Upstash env-var fix existed. Confirmed live via MadeOnSol's
  own 429 body (`resets_at: 2026-09-30T00:00:00.000Z`). Not a new bug.
- [x] DONE: found a second, separate bug during that investigation -- the
  Claude session's own local (`device_bash`) environment cannot reach
  Upstash's REST API at all (proxy blocks `*.upstash.io`), so every prior
  local "budget check" was silently failing and returning false default
  data (0 used / 190 remaining) instead of real state. This produced a
  false "verified 190 remaining, clear to trigger" claim given to Ali
  before a run that then hit real 429s -- acknowledged and corrected to
  him directly, same night.
- [x] DONE: built `reconcile_budget.py` + one-off `reconcile-budget.yml`
  GitHub Actions workflow (only environment with real Upstash network
  access) to push the shared daily counter to reflect today's real
  exhausted state. Ali pushed commit `e01208c` and manually ran the
  workflow -- run completed `conclusion: success`.
- [x] DONE: third cron-job.org job created -- "gem-alert validate-scoring
  (post-reset)" -- POSTs to `workflow_dispatch` on `validate-scoring.yml`
  automatically every day at 5:05 AM PKT (00:05 UTC), right after
  MadeOnSol's real daily reset. Headers (Content-Type, Accept,
  Authorization with Ali's own token) confirmed set and saved.
- [ ] NEXT: tomorrow's ~00:05 UTC automatic validate-scoring run is the
  next real, honest read of sniper accuracy -- off a genuinely fresh
  MadeOnSol quota, with the concurrency guard and reconciled counter both
  in place. Still the blocking gate before any `EXECUTION_ENABLED`
  discussion (Section 4 boundary unchanged -- Ali physically present
  only).

## Next: raise rug/pump-dump precision toward 80%+ (Sept 29, ~1:53 PM PKT)

Goal: moonshot recall is already 100% (6/6) -- the gap is precision (rugs/
pump-dumps scoring as moonshots). These are the concrete engineering steps
to close it, plus real research on what actually distinguishes a rug at
launch time, plus a free/fast API to add as a pre-filter.

- [ ] Re-validate tomorrow's post-Birdeye number on a BIGGER sample before
  trusting it. The last categorized backtest was 16 tokens -- a jump from
  43.8% to anything higher on 16 tokens isn't statistically trustworthy on
  its own. Pull a wider, out-of-sample token set (more launches, wider
  date range) once the Birdeye-enabled number comes back, before sizing
  any real trade against it.
- [ ] Require 2+ independent signals to agree before promoting a token to
  a tradeable band, instead of one composite score threshold (e.g.
  collapse-window override AND deployer-tier AND holder-distribution all
  pointing the same way). Trades fewer total signals for much higher
  precision on the ones that do fire -- the right tradeoff with only
  10-20 real trades to spend.
  [x] DONE Sept 29 2026 ~6:55 PM PKT: implemented as a band-A gate in
  score_token -- real failure mode closed: several UNKNOWN signals each
  contributing neutral-default partial credit could blend up to a band-A
  numeric score with ZERO signals actually confirmed favorable. Now
  requires >=2 of the 4 strongest, most rug-diagnostic signals (both
  mint+freeze authority confirmed revoked, LP locked/curve healthy
  confirmed, launch-window drawdown confirmed <=-10% from peak,
  bundler/sniper % confirmed <15%) to be independently CONFIRMED (real
  data, not a None-default) before a token can hold band A -- otherwise
  demoted to B, never lower. Only touches band A; B/C/D unaffected. 3 new
  tests (holds A with 2+ confirmed, demotes to B with only 1 confirmed
  despite score >=80, never fires below band A). Full suite: 518 passed /
  1 pre-existing unrelated failure. Committed locally, not yet pushed.
- [ ] Pull the categorized backtest's per-token breakdown for every false
  positive (scored high, was actually a rug/pump-dump) and find the
  common thread -- same deployer pattern, same liquidity-lock gap, same
  social-signal spoof. Targeted fix from real data, not a guess.

### Real research: what actually distinguishes a rug at launch time
(arXiv 2603.24625, "From Hype to Collapse: Investigating Rug Pull Scams
on Solana" -- measured, not anecdotal)
- **Lifecycle**: rug median lifespan 0.0116 days vs. 375 days legitimate --
  almost all rug activity happens same-day as creation.
- **Holders**: median 9 holders (rug) vs. 41,026 (legitimate) at the point
  measured.
- **Liquidity growth ratio**: 7.6% (rug) vs. 4,636% (legitimate) -- real
  projects show sustained liquidity growth, rugs don't.
- **Transaction rate**: 0.08/hour (rug) vs. 299/hour (legitimate).
- **Authority retention**: rugs typically KEEP freeze authority and LP
  tokens; legitimate projects renounce freeze authority at creation --
  this is a binary, checkable-at-launch signal, not a pattern that needs
  time to develop.
- **Timing**: rug frequency correlates with SOL price surges -- opportunistic,
  not organic, launch timing.
- **Off-chain**: 81% of rugs have no X account, 86% no website; where a
  website exists, 70%+ are invalid or just redirect to a social page.
- Actionable: freeze/LP-authority-retained is a same-block, zero-cost
  check (no MadeOnSol call needed) and should gate BEFORE any paid call
  is spent on a token, not after.

### Free/fast alternative found for speed + budget conservation
- **RugCheck.xyz public API** (`api.rugcheck.xyz`) has a genuinely free,
  no-auth `tokenSummary()` endpoint (confirmed via its open-source CLI
  docs) returning `score_normalised` and `lpLockedPct` per mint, fast
  enough for a pre-filter pass.
- Proposed use: run RugCheck's free summary as a same-block pre-filter
  BEFORE spending a paid MadeOnSol call -- if freeze/LP authority isn't
  renounced or `lpLockedPct` is near zero, reject immediately without
  touching the 200/day MadeOnSol budget. This both speeds up rejection of
  obvious rugs and conserves the scarce paid-call budget for tokens that
  clear the free first pass.
- Needs: a small `layers/` integration (fetch + parse RugCheck summary,
  gate before `fetch_madeonsol_token_risk()`), plus a fallback path if
  RugCheck itself rate-limits or is down (don't let it become a hard
  blocker -- degrade to current behavior, not a crash).

- [x] DONE Sept 29 2026 ~2:15 PM PKT: built using GoPlus instead of
  RugCheck -- GoPlus's Solana security scan was ALREADY integrated in this
  codebase (fetch_goplus_security / parse_goplus_solana_security, used as
  a post-failure fallback) and already returns exactly the strongest
  measured rug signal (freeze authority renounced, LP locked). Moved it to
  run FIRST in score_solana_mint, free, before fetch_madeonsol_token_risk
  -- rejects a confirmed-bad token (freeze_authority_revoked is False, or
  lp_locked is False) without spending any of the 200/day MadeOnSol
  budget. Only fires when a real MadeOnSol call would otherwise be spent
  (madeonsol_api_key configured), never blocks on unknown/missing GoPlus
  data, and degrades gracefully to the normal flow on any GoPlus failure.
  4 new tests added (confirmed-rug rejection with zero MadeOnSol calls
  made, unknown-data non-rejection, GoPlus-failure fallthrough, no-API-key
  skip) plus 5 existing tests updated for the new call order. Full suite:
  515 passed / 1 pre-existing unrelated failure (same one noted above,
  untouched by this change). Committed locally, not yet pushed.

## Queued for AFTER tomorrow's ~5:05 AM PKT validate-scoring run (Sept 29, ~7:51 PM PKT)

Deliberately held until tomorrow's post-Birdeye, post-pre-filter, post-band-A-gate
number lands -- adding more signals before that number is honest data would make
it impossible to tell which change actually moved precision. Order below is
priority order, not build order.

- [ ] Deployer wallet rug-history via Solana Tracker's documented `/search`
  endpoint (`GET https://data.solanatracker.io/search?deployer=<wallet>`,
  `x-api-key` header, free tier ~2,500 req/month). Returns every token a
  wallet has deployed with live `liquidityUsd`/`marketCapUsd`/`status` per
  token -- lets us compute a real rug ratio (past tokens now near-zero
  liquidity vs. still active) for a deployer, without decoding pump.fun's
  raw undocumented instruction format (a prior session deliberately
  declined that approach -- documented in fetch_solana_wallet_first_seen_ts's
  docstring -- after getting burned guessing at an unconfirmed binary
  layout before). This is the clean, non-guessed alternative. Needs: Ali
  to sign up for a free Solana Tracker API key and add it to GitHub
  Secrets (never handled directly by Claude, per the hard credential
  rule). Free-tier applicability to this exact `deployer=` filter still
  needs final confirmation before wiring it in.
- [ ] Recalibrate compound_scalper's slippage/fee model (currently a
  12%-cap heuristic, flagged in its own code as unmeasured) against real
  fills once real trades start -- first few live trades tell us if 12% is
  too loose or too tight, and the compounding math (~10 consecutive wins
  needed for the $100 target) is only as honest as this number.
- [ ] Minute-level holder growth signal, not hourly. The current
  holder_growth_rate_per_hr is too coarse to catch memecoin launches,
  which move in minutes, not hours -- same category of fix as the
  arXiv-measured "0.08 tx/hr rug vs 299 tx/hr legitimate" signal, just at
  finer time resolution.

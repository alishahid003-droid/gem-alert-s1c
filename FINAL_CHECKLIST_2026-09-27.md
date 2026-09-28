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

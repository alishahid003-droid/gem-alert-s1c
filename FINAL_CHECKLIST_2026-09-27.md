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
- [x] DONE Sept 28 2026: post-alert monitoring pass built and wired live.
  Every real alert scheduler._handle_scored actually delivers (band A/B
  and HIGH-RISK MOMENTUM, on Birdeye-supported chains -- solana/base/bsc/
  ethereum, not Robinhood Chain, same disclosed gap as the launch-window
  collapse override) queues a one-time follow-up entry
  (state.post_alert_monitor_add). scheduler._run_post_alert_monitor_cycle
  runs every fast cycle (0 MadeOnSol budget -- Birdeye's a separate
  free-tier account), checks any entry >=15 min old against real Birdeye
  OHLCV covering the alert-to-now window, and sends a DOWNGRADE follow-up
  if price is down 60%+ from its post-alert peak (same threshold as the
  collapse override, same real evidence). A single pass per alert, not a
  repeating watch, per spec ("15-60 min after"); an entry that ages past
  60 min uncapped-out just prunes silently, an honest disclosed gap same
  as the soft-fail watch list's own max-age prune. 12 new tests, all
  passing (tests/test_post_alert_monitor.py), full suite still green
  (428/428). Direct fix for tonight's one remaining miss (RICH OFF GTA 6,
  -8.54% at scan time -- this would have caught it 15-60 min later if it
  went on to crater the way the override's threshold implies it likely
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
- [ ] Twitter/X itself confirmed genuinely paid-only (checked twice,
  Sept 27 2026): free tier is a one-time ~100-request trial or
  restricted to government/public-service accounts; real read/search
  needs Basic ~$200/mo (no search) or Pro ~$5,000/mo; scraping
  workarounds get IP-blocked within hours and break on every frontend
  change -- not building that. This item stays paid-only, no free
  substitute for Twitter specifically.
- [ ] Build instead (Ali's push Sept 27 2026 -- right call, there IS a
  free path for the same underlying goal, just not via Twitter):
  monitor public Telegram channels via Telegram's own official, free
  Bot API or Telethon -- this is where most real pump.fun
  "caller"/signal activity actually lives, more than Twitter for this
  specific niche, and it's ToS-compliant (unlike scraping X). Track
  which known caller channels post a buy call and which wallets act
  right after -- same purpose as "KOL activity," different free
  source. Needs: pick 3-5 real, established Solana/pump.fun caller
  channels to start with (not guessed at -- find ones with an actual
  track record), a Telegram bot/client reading their public messages,
  and a way to correlate a post to the token's contract address.
- [ ] Check Dune Analytics' free API tier for pulling community-built
  Solana "smart money" wallet-labeling dashboards programmatically
  (one exists live: dune.com/wallet_dig/smart-money-solana) -- free to
  view in-browser, exact free API credit limit not yet confirmed live
  (Dune isn't reachable from this session's sandbox, same restriction
  as MadeOnSol/Birdeye/DexScreener -- needs checking from Ali's own PC
  or browser). If the free API tier covers enough query volume, this
  is a free, no-build-required source of wallet labels rather than
  something we'd have to construct ourselves.
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
- [ ] If ever worth revisiting: check whether any retail bot (Axiom,
  Trojan, etc.) exposes an API/webhook that lets an external script
  trigger a trade through THEIR execution infra -- most are closed
  consumer apps and don't, but if one does, that combination (our
  scoring + their speed) could be cheaper than building our own
  low-latency infra from scratch. Not confirmed either way yet.

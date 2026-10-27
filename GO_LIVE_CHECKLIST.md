# S1c MASTER CHECKLIST - THE ONLY SOURCE OF TRUTH
Same text lives in: this file (git) = Notion "S1c MASTER CHECKLIST" page. Nothing else is a checklist.
Old versions are archived in docs/archive/ (history only - never read them for status).
UPDATE RULE (Claude, after every change): rewrite THIS file, copy it to the Notion master page, then commit. Ali pushes.
Legend: [x] done & running | [~] built, under test | [ ] to do | (A)=Ali | (C)=Claude

## 0. STATUS -- 25 Oct 2026, 03:00 PKT (CURRENT -- sections 1-1S below are 13-16 Oct history, kept for the record, not current)
- Live trading: EXECUTION_ENABLED=true, EARLY_LIVE_LANES=sprint, LIVE_MAX_STAKE_USD=5, LIVE_MAX_OPEN=3, LIVE_DAILY_LOSS_USD=17. BUT logs/live_exec.log's last real fill is 14 Oct -- Sprint has NOT actually fired a live trade in 11 days.
- ROOT CAUSE found tonight: PumpPortal's per-coin trade-feed subscription needs its linked wallet funded >= 0.02 SOL (~$3.60-4.00 at current prices). Ali confirmed that wallet holds only ~$2 -- under the threshold -- so PumpPortal's own server has been rejecting subscribeTokenTrade/subscribeAccountTrade every cycle (trades_blocked=True in early_stream.py), starving Sprint of the live per-coin confirmations it needs. The API KEY itself is fine (present, correct length, loads OK) -- this is purely a funding-threshold gap, not a key problem. ACTION NEEDED (A): top up that linked PumpPortal wallet to ~0.04 SOL (0.02 threshold + buffer for $5 stakes + tx fees). This is the single blocker between the code and Sprint actually firing live.
- e2e_test.py --loop had been SILENTLY DEAD for 3+ days (crashed hard on a GeckoTerminal 500 error, confirmed via Ali's screenshot). FIXED tonight (commit 42f10ca): replay_coin.py now retries 5xx like 429 instead of crashing; e2e_test.py's loop() also catches any future per-cycle exception instead of dying outright. Confirmed alive and still writing to logs/e2e_cache.json as of tonight.
- Layer 20 (notable-wallet impersonation watch, built 21 Oct after the Kimchi incident) and Layer 21 (celebrity/known-person Telegram signal watch, built tonight) were committed/pushed days ago but had NEVER actually executed once in production: they sat after layer18/19 in the poll-radar cycle, and layer19's slow wallet checks always consumed the full 540s kill-budget first. FIXED (commit 1bbc8ac): reordered to run right after layer16, before layer18/19.
- That reorder fix immediately exposed a SECOND real bug: layer20 had no internal time budget, and on its first cold run tried to RPC-check every holder of every candidate mint from the last 240 minutes in one pass -- hanging the ENTIRE poll-radar cycle (confirmed: every cycle since 1bbc8ac landed printed only [layer16] then "timed out after 540s -- killed" -- layer18/19 got ZERO turns, worse than before the reorder). FIXED tonight (commit 95a8173): layer20 now bails out after a 45s internal budget (NOTABLE_WATCH_TIME_BUDGET_S) and defers the rest to next cycle instead of hanging the shared cycle. 13/13 layer20+21 tests still pass.
- ACTION NEEDED (A): the live local_runner.py process is still running code from BEFORE all three fixes above. Run start_local_mode.bat again (Admin CMD: taskkill, then start_local_mode.bat -- it `git pull`s first) so layer20/21 actually start executing and the 540s-hang regression clears. The e2e loop and wallet_edge_builder do NOT need restarting -- confirmed alive and unaffected by any of tonight's fixes.
- FOMO credits: still 0 of 250,000 remaining, last_error=credits_exhausted -- unchanged, over a month now, no published reset date. Auto-resume check every 30 min stays in place; nothing more to do here until FOMO's own limit resets on their side.
- Architecture clarity (Ali's "six sides" framing, 25 Oct): the system already covers all six as separate FINDER modules feeding one router (layers/router.py), each coin routed to exactly ONE book -- no new modules needed, the gaps are DATA (FOMO exhausted, PumpPortal underfunded) and PROVEN EDGE (no lane has cleared the paper go/no-go gate except Sprint, which is live but funding-starved), not missing architecture:
  1. FOMO side -> fomo_followers / fomo_new finders (blocked on exhausted credits, above)
  2. Compounding -> compound book (2-3x/day target via repeat small wins)
  3. Moonshots -> core2/sprint moonshot lanes (own-analysis + radar finders)
  4. Copy-trading known traders -> leaderboard + notable_wallet (layer20) finders
  5. Dev launches -> dev_list finder
  6. Known wallets/virality/influencers -> layer18 (virality), layer19 (sniper-wallet), layer21 (celeb Telegram) finders
  All feed layers/router.py which assigns each coin to core1/core2/compound/sprint/watch, never double-booked.
- $125,000 target -- honest math (25 Oct): the compounding path needs a sustained ~2.5-3x/day pace to reach $125k on a realistic timeline, which only works starting from ~$4,500-5,000 of working capital, not from $50-100. The moonshot path (one 500x-800x hit) is the only route from near-zero capital, but the one dataset example found (crack.fun, x797.7) is the SAME mint already flagged for GeckoTerminal data corruption, so it is NOT counted as a confirmed system catch. Highest-probability concrete next steps right now: (a) get Sprint's live lane actually firing by clearing the PumpPortal wallet-funding gap above -- it is the only lane with a working, already-live execution path; (b) keep the e2e and wallet_edge loops running uninterrupted now that both are fixed, since every clean day of data moves the compounding lane closer to a provable edge instead of a guess.

## 0A. FINAL CONSOLIDATED CHECKLIST -- path to live, as of 25 Oct 2026, 03:30 PKT (supersedes the older "NEXT ACTIONS" ordering in section 5 below where they conflict; section 5 still holds for anything not repeated here)

**What tonight's deep-check actually found (verified against real data, not restated):**
- The e2e_test.py "GO" verdict (backtest replay on historical candles, own disclaimer: "candle fills are optimistic") and the REAL live paper-trading ledger (actual simulated execution against live data) DISAGREE. Real ledger tonight: 93 closed trades, only 10 wins (11%), net loss across every active book. The backtest GO is not sufficient proof by itself -- confirmed by checking, not assumed.
- Entry SPEED is not the bottleneck right now: checked real entry-age data for tonight's 93 trades -- median 53s from coin launch, winners and losers entered at essentially the same average age (65.5s vs 67.8s). The older "fast-entry is the missing ingredient" conclusion (16 Oct backtest study) does not hold up against tonight's real execution data.
- The real, still-unclosed gap is DATA KIND, not data speed: every free-data feature tested over weeks (16 Oct, 998-coin study, clean re-test) has no proven edge. PumpPortal's paid trade feed (real buyer wallets, dev-dump, bundling detection) is the one kind of signal never yet tested against real outcomes, because it has been funding-blocked since it was built.
- No $10-20/month subscription fixes this -- checked current pricing on Helius/QuickNode/Chainstack: real-time Solana websocket data starts ~$49/month everywhere. The actual fix is PumpPortal's own metered, pay-per-use feed (pennies/day) behind a one-time ~0.04 SOL (~$4-7) wallet top-up -- already underway.

**FINAL CHECKLIST TO MAKE S1c LIVE, IN ORDER:**

ALI:
1. [ ] Fund the PumpPortal-linked wallet to ~0.04 SOL (0.02 SOL threshold + buffer) -- the single highest-leverage action open right now. Unblocks the one untested kind of signal (real wallet/buyer data).
2. [ ] Keep `python e2e_test.py --loop` and `python wallet_edge_builder.py` running uninterrupted (both confirmed fixed and alive tonight) -- every clean day strengthens or kills the evidence.
3. [ ] One-time, free: my.telegram.org signup -> `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` in `.env` -> run `python telegram_login_setup.py` once, to activate Layer 21 (currently fails closed by design, not broken).
4. [ ] `git push` after every session Claude commits (standing workflow -- Claude has no push credentials here).
5. [ ] ~24-48h after PumpPortal funding: tell Claude it's been running -- that's the trigger for the next real test (below).

CLAUDE (once PumpPortal has been flowing 24-48h):
6. [x] DONE tonight: fixed layer20/21 never-executing bug (ordering), layer20 cycle-hang bug (time budget), e2e_test.py silent-crash bug (5xx retry).
7. [ ] Re-run the entry-feature-vs-outcome study WITH real PumpPortal wallet/buyer data included -- the one test never done with this data, since it was always blocked. This is the actual next research step, not a new layer.
8. [ ] Re-run the cost-vs-edge math (10-25% round-trip) on that new data. Only a result that clears break-even AFTER cost, on the REAL ledger, counts.
9. [ ] Hold off on any new detection layers until 7+8 produce a verdict -- layers 18-21 stay shadow-mode (alert + log only) until each has enough real outcome data to prove or kill itself on its own.
10. [ ] Decide what to do with the near-empty paper books (core1 $1.62, sprint $0.20, compound $0.00 as of tonight) -- either reseed for continued paper testing, or treat the drain itself as evidence the current free-data rules don't have an edge. Report the choice, don't make it silently.
11. [ ] GO-LIVE GATE, clarified: >=30 fresh trades, mean P&L above x1.0 after costs, dropping the best trade, measured on the REAL PAPER LEDGER -- never the e2e backtest alone. The backtest is a cheap pre-screen, not proof. No lane's live cap (LIVE_MAX_STAKE_USD / LIVE_MAX_OPEN) increases beyond today's tiny test size until this is met on real-ledger data with the new PumpPortal signal included.

Everything else in section 5 ("NEXT ACTIONS") still stands for anything not repeated above (per-lane 50-entry go/no-go, second pool-discovery source, etc.).

## 0B. NOV 1 LIVE DEPLOYMENT PLAN (Ali, 25 Oct 2026 14:55 PKT) -- $200 capital, target $107,000 in 15 days from go-live

- **Ali's plan:** deploy $200 live on 1 Nov 2026; the system has to compound/aggregate across FOMO, compound, moonshot and copy/dev lanes toward $107,000 within 15 days of going live (~by 16 Nov).
- **Honest math (stated to Ali plainly):** $200 -> $107,000 = 535x. That needs ~52% compounding growth per day, sustained for all 15 days with no bad day. Recorded here so the plan is built against the real number, not an assumed one.
- **Proposed $200 allocation across existing books (not yet applied to .env -- final split depends on the PumpPortal-informed edge test below):**
  - Sprint (96h moonshot, cash-out+reseed): $80 (40%)
  - Core1 (30-day moonshot): $50 (25%)
  - Compound (small steady 2-3x stacking): $30 (15%)
  - Core2/Core3 (180-day moonshot, repurposed as tail-shot): $30 (15%)
  - Copy/dev_list/leaderboard (kept live for real-trade logging, low stake): $10 (5%)
  - FOMO books: $0 (still fully credit-blocked; auto-activates once credits return)
- **7-day pre-launch timeline (today -> 1 Nov):**
  1. PumpPortal wallet funded (Ali, in progress) -> real buyer/wallet data starts flowing for the first time.
  2. ~27-28 Oct: 24-48h of real PumpPortal data in hand -> Claude re-runs the entry-feature-vs-outcome test WITH it (never possible before tonight).
  3. ~29-30 Oct: real-ledger verdict on genuine edge, if any, with the new data -> this tunes the actual per-lane stake sizing, not a guess.
  4. 31 Oct: final `.env` config locked for the $200 split above, tuned by whatever the verdict shows.
  5. 1 Nov: go live with real circuit breakers tested (daily loss cap, kill switch) so a bad stretch early on doesn't wipe the $200 before the full 15-day window plays out.
- **Standing from here:** this supersedes nothing in 0/0A -- it is the concrete target those steps feed into. The PumpPortal-informed re-test (0A, Claude step 7) is now explicitly gated to land before 31 Oct so there's real evidence behind the 1 Nov config, not just the backtest.

**Full context (Ali, 26 Oct 2026 02:41 PKT) -- how S1c fits the bigger picture, not an isolated bet:**
- Two parallel tracks: (1) ~$18,500 Ali is arranging separately to clear immediate liabilities and fund S1B going live -- S1B is technically ready (16 accounts, $950,000 funded capital, both compliance checks closed as of Oct 21, confirmed in Notion). Once live, S1B's monthly $5,000-$8,000+ income covers ongoing expenses. (2) $200-250 into S1C, deployed 1-5 Nov, given a 10-15 day window to compound/aggregate toward $107,000 using every module in parallel (FOMO, compound, copy/dev-trading, own-analysis, virality, moonshot/sprint) -- not a single pick, many positions running at once, reached either via several compounding together or one real moonshot landing inside the window.
- S1C's deployment plan (the allocation above) already matches this shape and does not need to change structurally. What it needs is the PumpPortal data gap closed (see 0A) so the modules are working with real signal instead of the free-data rules already shown to have no edge.


## 1. STATUS - 14 Oct 2026, 01:15 PKT
- Everything is PAPER (EXECUTION_ENABLED=false). No live trades anywhere. 927 tests pass.
- Running since the 14:12 PKT restart: all items in section 3. Dashboard: http://localhost:8787
- Last pushed commit 344b944. NOT PUSHED YET (18:15): PumpPortal spending guard (early_stream.py, worker_early_watch.py, new test file) + this checklist -> Ali: git add -A, git commit, git push.
- RUNNING NOW in its own CMD window: `python e2e_test.py --loop` (end-to-end replay test, no CoinGecko key yet = slow pace). First coins become replay-ready ~20:00 PKT; verdict (needs >= 30 entries) expected tonight/overnight. Its first batch (14:31) was useless (coins fetched minutes after launch) and was fixed.
- Helius key is in .env (SOLANA_RPC_URL, verified format). PumpPortal key NOT yet added: metered-feed guard built first (see section 3). Needs one restart (kills the e2e loop - restart it after).
- e2e_test.py has no unit tests yet (checked by hand with a mocked HTTP call).

## 1D. LIVE SETTINGS FROM 14 OCT 15:25 PKT (restart done by Ali)
- LIVE: only the Bills stream (profile fast_sprint -> lane sprint). `.env`: EXECUTION_ENABLED=true, EARLY_LIVE_LANES=sprint, EARLY_FREE_CONFIRM=live, LIVE_MAX_STAKE_USD=5, LIVE_MAX_OPEN=3, LIVE_DAILY_LOSS_USD=12. Backup of the previous .env: `.env.SAFE_BACKUP_PRELIVE_20261014` (never commit; ignored by git).
- PAPER (keep running 24-48 h for tuning data): Core 2 (fast_core), Core 3 (fast_moon), Compound. They cannot go live (gate says "lane not live").
- STOP INSTANTLY: `python kill_switch.py` or create an empty file KILL_SWITCH in the project folder (new buys stop, sells still work).
- UNDO LIVE: set EARLY_FREE_CONFIRM=paper in .env and restart, or run restore_safe.bat.
- CHECK: `python streams.py` (four streams), `logs\live_exec.log` (every live reply with tx signature), Solscan for the PumpPortal wallet.
- A stream goes live only after 30+ closed paper trades, positive after costs and without the best coin. Core/Compound go live later on that rule.
- Next review: ~6 h after restart (live fills, fees vs paper, first 30 Bills closes), then ~24 h (snapshot data -> tune the entry filter).

## 1C. FOUR MONEY STREAMS + FAST ROUTER (14 Oct, 15:00 PKT) - THE STRUCTURE
Ali's aim: the system works toward four money targets automatically, each with its own balance, entry window and exits.
| Stream (book id) | Aim | Entry | Exits (profile) | Live? |
|---|---|---|---|---|
| BILLS = Sprint 96h (sprint) | $16,000 ($6k now + $10k in 72h); book target $100 -> $10,000 in 96h | coin <=120 s old, <=55 SOL, free confirmations | fast_sprint: sell 30% at 1.4x, 20% at 2.5x, 15% at 6x, 10% at 15x, 25% runner, stop 0.55, trail 0.55 from 1.4x | the ONLY stream allowed live (EARLY_FREE_CONFIRM=live + caps) |
| Core 2 (core1) | $125,000-150,000 in 30 days | coin 2-20 min old, <=130 SOL, momentum | fast_core: 1.5x/3x/8x slices, stop 0.45, trail 0.50, 50% runner | paper |
| Core 3 (core2) | $1M-1.5M in 180 days | <=60 s, <=35 SOL, famous name + 3 confirmations | fast_moon: 10% at 5x, 10% at 20x, 80% runner | paper |
| Compound (compound) | in/out small runners, 2x-3x, repeat | any coin with >=2 confirmations, <=130 SOL | compound: 60% at 2x, 40% at 3x, stop 0.70 | paper |
- ROUTING (layers/fast_lane.py): one coin gets AT MOST ONE moonshot book (moon > sprint > core) plus one Compound position. No more identical copies in 3 books. Positions are booked under the book's id, so each stream's balance/target progress counts them. `python streams.py` prints all four.
- WHY: the old books refused 257 of 263 signals (3 slots each, cash full). Fast paper takes every routed signal ($5/$4/$2/$4 stakes, 60 open max) so the evidence is complete. Live is separate and wallet-sized.
- NEW SIGNAL famous_name: coin name/symbol matches celebrity / sports / influencer / politician / big-brand list (EARLY_FAMOUS_NAMES, `off` to disable). 2-day data: 9.2% reached 10x vs 2.6% for others (607 vs 20,262 coins; out-of-sample list). Counts as ONE confirmation, never enters alone.
- LOGGING: every young coin writes a free snapshot (buys/sells/volume/mcap) to early_log (t=snap) so the entry filter can be learned from ~300 real winners within ~24 h.
- Replay (2 days, 147 free-signal entries): new exits +~2 points vs old; unfiltered buying all coins = ~-7% after dead coins; free-signal filter ~+8-10% but ~break-even without the 2 best coins. Not proof of profit.
- Paper -> live rule: a stream goes live only after 30+ closed paper trades, positive after costs, and still positive without the best coin.
- Honest: $16,000 needs ~640x on a ~$25 wallet; no stream shows that edge yet.

## 1B. 14 OCT (daytime) - WHAT BROKE OVERNIGHT AND WHAT WAS FIXED
- Live buy "worked" only in the ledger: executor counted HTTP 200 with an error body as filled. Wallet showed no buy. FIXED: `executor/pumpportal_exec.py` now needs a tx `signature`, logs every reply to `logs/live_exec.log`, pool = "auto" (was "pump", fails on graduated coins). Backup: `.SAFE_BACKUP_20261014`.
- MANUAL TEST PASSED (09:13 PKT, `live_test.py`): buy 0.00438 SOL of RUSO then sell all, both Finalized/Success on Solscan, cost ~$0.27 total. Order path proven; automatic buy/stop-loss sell NOT yet proven live.
- Stake bug: paper stake rounded to $0 so live buy never fired -> patched in `worker_early_watch.py` (kept).
- PumpPortal trade feed dropped with HTTP 502 at ~03:15 PKT; restart reconnects.
- XTO bogus price ($36M at 1.4 s old, dev_list had no mcap cap) -> NEW price sanity check in `layers/layer17_early_entry.py` (EARLY_SANITY_MAX_MCAP_SOL=300, EARLY_SANITY_MAX_JUMP=8). dev_list stays paper-only (restore_safe.bat set live to sprint only).
- WHY SPRINT/CORE NEVER FIRED: the paid trade feed only watches dev-list/repeat-grad coins, so every other coin had no buy/sell data and could never reach 2 confirmations. NEW: free DexScreener 5-min buy/sell + volume confirmations (`pressure_free`, `velocity_free`) via `worker_early_watch.py` free_confirmer (env EARLY_FREE_CONFIRM = paper (default) / live / off). Entries that use them are PAPER ONLY until proven. Tests added in `tests/test_early_entry.py`.
- TO DO: restart, then watch `free_polls`/`free_entries`/`insane` in the early-watch heartbeat; after 30+ free-confirmed entries compare outcomes vs the no-confirmation baseline (early_log) before setting EARLY_FREE_CONFIRM=live.
- Tests (14 Oct 15:20 PKT): all 87 test files pass (~942 tests). Three tests used to fail only because Ali's real .env (API key, LIVE_MAX_STAKE_USD=8, EXECUTION_ENABLED=true) leaks into pytest; each now pins its own env with monkeypatch.

## 1A. LIVE NIGHT TEST (13-14 Oct) - REVIEW THIS FIRST IN THE MORNING
- LIVE MODE IS ON in a tiny capped form: PumpPortal wallet ~$21 SOL. EXECUTION_ENABLED=true, EARLY_LIVE_LANES=sprint,dev_list, LIVE_MAX_STAKE_USD=5, LIVE_MAX_OPEN=3, LIVE_DAILY_LOSS_USD=20. EXECUTION_SOLANA_PRIVATE_KEY stays commented out.
- TEMP NIGHT TUNE in .env: EARLY_MIN_BUYERS=6, EARLY_MIN_BUYER_RATE=0.04, EARLY_MIN_BUYS=8, EARLY_MIN_BUY_SELL=2.0. Also EARLY_MAX_ENTRIES_PER_DAY=300, PUMPPORTAL_MAX_WATCHED=40, PUMPPORTAL_MAX_MSG_PER_DAY=20000.
- EXIT CHANGE (executor/paper_ledger.py, EARLY_RULES): sprint + dev_list now sell 25% at 2x, 20% at 4x, 15% at 10x and leave a 40% RUNNER on the trailing stop. Needs a restart to take effect (restart done 00:57 PKT, ladder change NOT yet restarted as of 01:15).
- UNDO EVERYTHING: double-click restore_safe.bat (restores .env.SAFE_BACKUP_20261014 and executor/paper_ledger.py.SAFE_BACKUP_20261014), then restart. Safe = sprint only, $8 stake, open 2, old ladder.
- MORNING TEST (A+C): (1) did any live buy/sell happen, errors? (2) how many dev_list/sprint decisions overnight and how did they end? (3) wallet balance left of the $21; (4) did the 40% runner logic behave (rungs fired, trail)? (5) decide: keep tune, or restore_safe.bat. DO NOT leave the tune on without reviewing.
- HONEST ODDS: $20 -> $150k needs ~30,000x on one coin (about 75,000x on the 40% runner). Chance per entry is ~1 in 100,000 or worse. Do not plan bills on it.
- First 35 min after the tune (00:18-00:50 PKT): 744 new coins, only 5 decisions, all dev_list (1 confirmation); no sprint/core1/core2. Sprint needs >=2 confirmations, which need the paid trade feed, which watches only dev-list/repeat-grad coins.

## 2. TARGETS AND BOOKS (in the app: sprint = "Core 1 Sprint", core1 = "Core 2", core2 = "Core 3")
- Core 1 Sprint: $100 -> $10,000 in 96h, cash out above target, reseed ~$200. Core 2: $125,000 in ~30 days. Core 3: $1M-$1.5M in ~180 days. Also Compound (daily goal) and copy books (Fomo Followers, Fomo New Wallets, Leaderboard, Dev Launch).
- Stakes = % of the book's balance (compounding), pace-adjusted (x1.25 behind path, x0.8 ahead), profit vault banks part of profit above 3x seed, busted books (<$10, nothing open) reseed at $100.

## 3. WHAT IS BUILT AND RUNNING (all paper)
- [x] Pump.fun early sniper (PumpPortal launch stream -> EarlyTracker -> lanes). Per-coin trade feed is OFF until the PumpPortal key exists, so only launch-level signals work.
- [x] Multi-chain sniper (GeckoTerminal new pools: Solana non-pump, BSC, Base, Robinhood + StonkFun) with GoPlus gate on EVM, 45 s poll.
- [x] EXITS (all sniper lanes): hard stop 0.35x, trail 0.40x of peak after 2x, take-profit 20% at 2x / 15% at 4x / 10% at 10x, dead-coin kill after 90 min, 24h max hold, 10% round-trip paper cost.
- [x] CONVICTION TIERS: probe 15% / standard 40% / conviction 60% of balance x book scale (Sprint 1.0, Core 2 0.8, Core 3 0.6), max 80%, stake <= 2% of the coin's market cap, loss-streak brake (one tier down per 3 straight losses). Conviction needs 6 confirmations incl. buyers + pressure (needs PumpPortal/Helius keys). Pump.fun worker: dev_list/repeat_grad alone = probe.
- [x] ENTRY LANES: base cap $60k; Solana momentum up to $250k (>=4 conf); Solana "biglaunch" $250k-$1M (>=5 conf, probe only); Robinhood up to $250k (>=5 conf, probe, stake x0.6); BSC/Robinhood legacy EVM stake mult 0.3/0.6.
- [x] SLOT RULES: a strong signal (standard/conviction, >=5 conf) may use 2 extra slots beyond the normal limit (3 below $300, 4 below $3k, 5 below $30k); if full, it swaps out the stalest FLAT position (age >= 45 min, never ran, still >= 0.75x, no take-profit fired); max 1 swap per book per 5 min. Env: BOOK_STRONG_EXTRA_SLOTS, SWAP_MIN_AGE_MIN, SWAP_MIN_MULT.
- [x] Serial launchers (> 20 launches) are not tracked (0.3x graduation lift). dev_list lane is probe-size (0 of 69 graduated).
- [x] WINNERS TOOLS: replay_coin.py + discover_winners.py (replay known winners on minute candles, picks the original from copycats automatically, Solana/BSC/Base/Robinhood); winners_tracker.py (logs/winners_book.json: every coin the scanner sees, peaks re-checked at +3h/+24h/+72h, winners >= $1M, whether the sniper entered them). Run: python winners_tracker.py
- [x] Evidence tools: analyze_early.py, backtest_sniper.py, api_budget.py (per-API call ledger + shared rate gate), fomo_me.py, kill_switch.py, run_checks.bat.
- [x] Fomo: credits exhausted (0 of 250,000, daily limit 10,000, no published reset date). System now checks the free GET /v2/me every 30 min and resumes by itself when credits return; the moment is saved as credits_back_ts in .gem_alert_state.json (fomo_credit_state). Rotating the key / new account does not help and is NOT to be done.
- PUMPPORTAL METERED FEED GUARD (built 13 Oct 18:15, untested live): PumpPortal bills the keyed per-coin trade feed 0.01 SOL per 10,000 messages from the key's linked wallet, and the wallet must keep >= 0.02 SOL. So: the free launch/graduation socket stays KEYLESS; the key goes only on a separate trade socket; PUMPPORTAL_MAX_MSG_PER_DAY (default 10,000 = ~0.01 SOL/day) pauses the feed until 00:00 UTC; PUMPPORTAL_MAX_WATCHED (default 20) limits coins watched; only repeat-graduate and dev-list launches are watched. Dashboard/log show trade_msgs and est_spend. Fund the linked wallet ~0.04 SOL (0.02 reserve + ~2 days of feed).
- Infra: local mode, DNS-over-HTTPS fallback, dashboard is the only alert place (Telegram off), legacy stage/scalper paper traders retired.

## 4. KEY FINDINGS (honest)
- First 3 h of paper (Oct 13 morning): 0 winners / 42 entries; entries above $50k lost; BSC 0/9, Robinhood 0/5, Solana 0/26 - old rules had no edge.
- Winners replay (known winners only, so best case): 8 of 17 replayable coins would have fired (AUTON x92, JEANPHIL x64, VEIL x47, swordcat x36, CLAUDIA x31, PONS-Robinhood x462; duds CYBERLEEK, AMC). Not caught: LOAD, Orca CLAUDIA, STONK (opened > $250k), MOW/VRAX/CLAUDIA-Robinhood (opened > $60k), FEELSGOOD.
- Old exits wasted winners ($100 -> $124 on the replay). New exits: ~$17.9k at 30% stakes, up to ~$30k at 50/70% stakes (2% of mcap cap). This is a winners-only upper bound, NOT a forecast: remove one big runner and it falls 60-85%; three duds in a row at 50% stakes halves the book.
- Most Solana winners were already $30k-$80k in their first indexed minute; pump.fun curve-phase (before ~$69k) is not visible in GeckoTerminal. True $10k-$20k entries need the PumpPortal trade feed (buyers, wallets, creator dump, bundling, sell pressure).
- early_log study (6,295 launches, day 1): "dev bought 3+ SOL" lift was an ARTIFACT (97% of those coins are the dev holding ~79% of supply, self-graduating; our 25% dev cap blocks them). Real signals: creator graduated before = 4.1x lift (5.0% vs 1.2% base); serial launcher = 0.3x. Only ~2% of launches reach 2x in an hour. One day is not proof: needs day 2.
- DATA QUALITY: early_log outcome prices contain impossible values (e.g. $4k launch -> $300M). Pump.fun runner list and >=10x columns are NOT trusted until verified (clipped at $20M).

## 5. NEXT ACTIONS - IN ORDER
ALI:
1. [x] (A) DONE 16:48: pushed (344b944). Everything on GitHub.
2. [ ] (A) Keep PC awake (sleep = Never), ALL windows open incl. the e2e loop window, dashboard open. Optional: a FREE CoinGecko Demo key in .env as COINGECKO_API_KEY (do NOT pay for any plan; the pricing page shows only paid plans, free signup may be elsewhere on coingecko.com/en/api). Restart the loop after adding it.
3. [ ] (A) Helius: DONE (key in .env). PumpPortal key (METERED, see section 3): open pumpportal.fun/trading-api/setup, it creates a wallet + API key; save any private key privately (never in chat/.env); fund ONLY that linked wallet ~0.04 SOL from Phantom (0.02 must stay as reserve; the rest is the spending cap); then add PUMPPORTAL_API_KEY=... to .env, keep EXECUTION_ENABLED=false (key + EXECUTION_ENABLED=true = live trading). Tell Claude "keys are in" -> one Admin-CMD restart, then restart `python e2e_test.py --loop`.
4. [ ] (A) ~01:31 PKT Oct 14: run python analyze_early.py 1, python winners_tracker.py, python api_budget.py and send all three outputs.
4b. [ ] (A) ~22:00 PKT and again in the morning: paste whatever the e2e loop window shows (full verdict once >= 30 entries).
CLAUDE (after those outputs):
4c. [ ] (C) Read the e2e verdict. GO (edge survives removing the best coin, >= 30 entries) -> set up ONE-lane tiny live test ($2-5 stakes) with PumpPortal key. NO-GO -> rework/cut lanes and rerun. Never treat GO as a forecast.
5. [ ] (C) Verify early_log outcome pricing against GeckoTerminal on sample coins.
6. [ ] (C) Review 24h paper under the new exits/tiers/lanes/slot swaps (stop-outs, tier mix, slot_swap P&L, biglaunch/Robinhood entries). Only then consider higher stakes.
7. [ ] (C) Day-2 check of early_log features; read credits_back_ts for the Fomo reset time; API budget audit close-out (every free API must last the month).
8. [ ] (C) DexScreener/Birdeye as a second pool-discovery source (GeckoTerminal gives only ~6 calls/min from Ali's IP).
9. [ ] (C) Per-lane go/no-go (keep / cut / rework): needs >= 50 entries per lane and positive paper P&L after costs.
LATER (live path, one step at a time, only after proven paper edge):
10. [ ] Pump.fun live: PumpPortal key + >= 0.02 SOL, EXECUTION_ENABLED=true, EARLY_LIVE_LANES = ONE lane, tiny LIVE_MAX_STAKE_USD / LIVE_DAILY_LOSS_USD, watch the first trades, kill switch ready, scale only after 30+ real closed trades.
11. [ ] Other chains in this order: Four.meme/BSC -> StonkFun -> Base -> Robinhood. Each needs a burner wallet (key only in .env), an adapter built by Claude (paper first), and a $2-5 live test.
12. [ ] Run to targets: Sprint cash-out at $10k (withdraw by hand, reseed $200); weekly review of vault/stakes/pace.

## 5A. FAST VERDICT TEST (added 13 Oct 14:40) - replaces waiting for weeks of paper
- e2e_test.py = end-to-end replay of the real entry gate + tiers + exits on an UNBIASED sample of new pools (winners AND duds), 4 chains. Prints per-book result with confidence range, result without the best coin, GO / NO-GO.
- Ali runs (CMD, own window, leave open): `python e2e_test.py --loop` - lists new pools every cycle, replays each coin once it is 4 h old (full path known), prints the verdict automatically once >= 30 entries. Ctrl+C prints the current verdict. `python e2e_test.py` prints the verdict from cache with no API calls. FIRST BATCH (14:31) WAS USELESS: coins were fetched minutes after creation (median 2 candles); fixed.
- Pace: without a key GeckoTerminal allows ~75 coins/h from Ali's IP (6-10 h for 30 entries). With a FREE CoinGecko Demo API key (own quota, ~26 calls/min, 10,000/month) in .env as COINGECKO_API_KEY the test runs ~5x faster; it uses it automatically and stops at 4,000 calls (E2E_MAX_CALLS). DexScreener has no price history so it cannot feed this test.
- GO means: a tiny live test ($2-5 stakes, ONE lane) is justified. Not a forecast.
## 6. KNOWN ISSUES
- Entry rules are still unproven; the new exits/tiers have had no live-paper day yet.
- Pump.fun per-coin trade feed OFF (needs key). Solana public RPC gives HTTP 429 (needs Helius). Fomo credits 0 (reset date unknown). GeckoTerminal ~6 calls/min. Only coins with a DEX pool are visible on EVM chains.
- Paper fills are optimistic vs real; Robinhood swap support unverified; StonkFun API returns only 3 tokens.
- Old admin-level python processes survive normal restarts: always restart from an ADMIN CMD (taskkill /F /IM python.exe, then start_local_mode.bat).

## 7. STANDING RULES (Ali)
- One source of truth only (this file = Notion master page). Dashboard is the only alert place. Free APIs only, paced to last a month. 24h plans. No padded checklists. Ali pays for nothing. No investor outreach. Claude/scripts find winner coins' addresses and tell originals from copycats themselves - never ask Ali for addresses or screenshots coin by coin.
- Security: keys and wallet secrets only in .env, never in chat; the Phantom seed phrase/private key is never shared or entered anywhere; the PumpPortal linked wallet is funded only ~0.04 SOL, never more; live money only after proven edge and only tiny stakes.
- Honest risk: the targets are the growth REQUIRED, not shown performance. Sprint is lottery-like. Live stakes tiny, one lane first.

## 8. CHANGE LOG (newest first)
- 25 Oct 02:5x PKT: Layer20 time-budget fix (95a8173) -- reorder regression (layer20 hanging the whole poll-radar cycle) found and fixed same night; restart still required to pick up all three of tonight's fixes.
- 25 Oct 02:13 PKT: Reordered poll-radar to run layer20/21 before layer18/19 (1bbc8ac) -- they had never once executed since being added.
- 24-25 Oct: e2e_test.py --loop fixed (42f10ca) after being silently dead 3+ days on a GeckoTerminal 5xx crash.
- 25 Oct: PumpPortal wallet-funding gap diagnosed -- key is fine, linked wallet needs >= 0.02 SOL (~$2 current balance is short).
- 21 Oct: Layer 20 (notable-wallet impersonation watch) + Layer 21 (celeb/Telegram signal watch) built, committed, pushed (08faba0).
- 14 Oct 01:15 Live night test: dev_list lane live, $5 stake, tune, 40% runner ladder (paper_ledger.py), restore_safe.bat. Section 1A.
- 13 Oct 23:30 Live mode enabled (sprint, $8 cap) after $21 funding; entry cap raised to 300; PumpPortal metered feed guarded.
- 13 Oct 16:50 e2e_test.py: unbiased end-to-end replay (real gate, tiers, exits) with --loop, 4h-old coins only, optional free CoinGecko key; commits facdfac, 344b944.
- 13 Oct 14:20 Single source of truth rewrite; history archived to docs/archive/.
- 13 Oct 14:10 Fomo: free /v2/me balance check + auto-resume; fomo_me.py.
- 13 Oct 14:00 Slot rules (extra slots + stale-flat swap).
- 13 Oct 13:40 Winners tracker, early_log outlier clip.
- 13 Oct 13:20 Early worker tiers, serial-launcher block, dev_list probe.
- 13 Oct 13:00 Conviction tiers 15/40/60, biglaunch + Robinhood probe lanes.
- 13 Oct 12:30 New exits (stop 0.35, trail 0.40, ladder 20/15/10, dead-coin 90 min) from winners replay.
- 13 Oct 05:00-12:00 Winners replay + discovery, copycat resolution, Base added; entry logging; API budget ledger + gate; fake +$505 bug fix; bust reseed; legacy traders retired; momentum bucket; Core 1/2/3 naming.

## 1E. Live-money bug found and fixed (14 Oct, 15:35 PKT)
- What happened: 6 real buys by 15:30. 3 stopped out (about -$2.7 to -$2.9 each). 2 coins (iCoin, dotfomo) were bought for real but their ledger entry was overwritten by the position manager (it wrote the whole open book back after pricing), so nothing was watching them. Ali sold both by hand with `python sell_orphans.py`; wallet is now about $17.5.
- Fix: (1) manager write now merges positions opened mid-cycle, under a lock; (2) every live buy is remembered and `live_guard` restores any lost live position within 8 s; (3) if a live buy has no position, it is sold straight back. 13 tests in test_fast_lane.py, 945 total pass.
- KILL_SWITCH file in the project folder blocks new live buys. Delete it only after restart with this fix.
- Restart: Admin CMD `taskkill /F /IM python.exe`, then `start_local_mode.bat`, then normal CMD `python e2e_test.py --loop`.
- Real fills are in logs/live_exec.log (they have a tx signature). The dashboard line "Paper: fast_sprint, compound" is the paper record only and does not say if the live buy happened.

## 1F. Live OFF, data collection, score gate (14 Oct, 17:00 PKT)
- Live is OFF: the file `KILL_SWITCH` is in the project folder, and `start_local_mode.bat` no longer deletes it. Live turns on only when Ali and Claude decide after the report. To turn on: delete KILL_SWITCH (and restart so the settings below are read).
- Why: the 4 real sprint live trades on the 2 cheap free signals all stopped out (wallet $25 -> $17.5). Ghost "live" row from the fake-fill bug was also counted in the loss cap; fixed with LIVE_SINCE_TS in .env; sprint-only live loss limit is now LIVE_DAILY_LOSS_USD=17.
- New: entry SCORE (layers/fast_lane.py score()), live needs FAST_LIVE_MIN_SCORE (default 5). On 86 logged signals it looked good (score>=5: 75% reach 1.4x) but that was in-sample, 12 coins.
- Collective report: `python collect_report.py` (writes COLLECT_REPORT.txt). First run on 971 coins: score does NOT separate winners (score>=5: 3% reach 2x, mean x0.89 after costs; all coins mean x0.79). No proven edge yet. Re-run after 24 h (several thousand coins) before any live decision.
- Paper streams keep running and collecting.

## 1G. Free trend feeds (14 Oct, 17:50 PKT)
- New `trend_feeds.py`: every 2 min pulls Google Trends (16 countries), Reddit r/all + r/popular names, Wikipedia most-viewed into `radar_hot_terms` (the key the early watcher already reads). A coin whose name/ticker matches a trending term gets `narrative`; the FIRST coin ever to match a trending phrase also gets `narrative_first` (score +2). Terms are forgotten after 48 h.
- Needs one restart of the worker to load. KILL_SWITCH stays (live OFF). Watch `logs/local_early-watch.log` for `[trends] N terms`.
- Measured, not trusted: confirmations are logged in early_log, so the 24 h report shows if `narrative` / `narrative_first` coins really run more often.

- 1G update (18:15 PKT): sources now also Bluesky + Mastodon trending, X (Twitter) trends via trends24 mirror, TikTok Creative Center hashtags (best effort). Instagram / Facebook have no public trend feed: their trends show up through Google Trends / X / TikTok. Trend terms live in state key `trend_terms_v1` (separate from the old Radar's `radar_hot_terms`; the worker reads both). Started by `start_local_mode.bat` through local_runner -> worker_early_watch.py; `e2e_test.py` is unrelated. Check log lines `[trends] N terms; issues: ...` to see which feeds refuse.

## 1H. Data is split by version (14 Oct, 18:45 PKT)
- Every worker start now writes a `{"t":"run","code":hash,"env":hash,"live":bool}` row to early_log, and `collect_report.py` also finds every past restart in logs/local_early-watch.log (25 so far) and prints a BY VERSION table so mixed-version data is never read as one blob.
- Fixed a report flaw: coins younger than 65 min (outcome checks not in yet) are no longer counted as dead.
- First clean read (1,230 coins, 4 versions): about 3% reach 2x in every version; new-ladder mean about x0.74-0.82 in each; score>=5 (36 coins) mean x0.88. Same story across versions = the result is stable, not a blend artefact. Still no edge.

## 1I. 5-hour check (14 Oct, 22:25 PKT)
- 3,676 coins with outcomes (27 version epochs). Pooled: 3% reach 2x, mean x0.78 on the new sprint ladder. Score>=5 (104 coins): mean x0.82, ex-best x0.79 -> score has no proven edge.
- Trend-matched coins (narrative/narrative_first, 77 coins): 8 reach 2x (10%), ladder mean x0.95, without best 2 x0.86, win 42%. Best cohort so far but still below 1.0 and small. Keep collecting; need 300+ such coins with mean >1.0 ex-best before live.
- Paper streams (fast P&L since start): Sprint -$273.85 (283 closed, 55 wins), Core 2 -$308.60 (238 closed, 36 wins), Compound -$590.07 (455 closed, 78 wins), Core 3 2 open / 0 closed.
- Live: no live buys since 15:33 PKT. KILL_SWITCH still in place. Wallet last known $17.5.
- Fixed: run-row timestamp bug in early_recorder.on_run (needs a restart to take effect); report drops >500x price glitches (A7bdiYdS "Zexy Cat" 3853x).

## 1J. 15 Oct — dev scoreboard, whale buy, wallet roster (live still OFF)
- Screenshot dev list tested on our logs: 512 launches, 3.9% won vs 5.9% average -> weight 0 (dev_list).
- NEW dev_rank.py: good_dev = creator with >=3 judged launches and >=30% wins in OUR logs (56 devs now, refreshed every 20 min). Time-split test: ~30% win on later coins (tiny sample 24-48: watch it).
- NEW whale_buy (one wallet >=3 SOL in a coin <90 s old) and smart wallets built from early buyers of winners. Both need the per-coin trade feed, so tracking now also covers famous/narrative/good_dev coins; buyers logged in `final` rows (early_buyers, max_buy). Cold start: roster fills only as data arrives.
- Moonshot path (fast_moon) now also accepts good_dev / whale_buy, not only famous names.
- Honeypots: pump.fun bonding-curve coins are always sellable by the program; real risk is dev dump (already blocked at >30%).
- Tests: 22 in test_fast_lane, rest of suite passes. Famous-name creation entry: x0.81 at 25% cost -> no edge yet.

### Overnight note 15 Oct 04:12 PKT
- Worker alive since 01:38 PKT, log and early_log growing, KILL_SWITCH present, dev_rank: 58 good devs.
- Found: widening trade-feed tracking to every `narrative` coin burned the 20,000 trade-msg/day budget in ~2.5 h (feed paused until 00:00 UTC = 05:00 PKT). Budget cap is a real-money guard (~0.02 SOL/day), NOT raised. Fix in code: only famous_name / narrative_first / good_dev coins get the trade feed. Takes effect at the next restart.

### Overnight note 15 Oct 07:12 PKT (check 2)
- Worker alive, KILL_SWITCH present. Fresh version (restart 01:38): 2,456 coins, mean x0.82; score>=5 (98 coins) x0.83; trend-matched (170 coins pooled) x0.88, ex-best-2 x0.85. Nothing at break-even. good devs 62, smart wallets 1.

## 1K. 15 Oct — copy-trading bridge, moonshot without virality (live still OFF)
- NEW copy_signals.py: KOL-wallet buys (MadeOnSol feed, roster tiers) + Fomo followed buys + Fomo new-wallet buys -> state `copy_buys_v1` -> EarlyTracker confirmations `kol_buy`, `kol_converge` (2+ traders), `fomo_new_wallet`. Worker task `copy_refresher` every 45 s, re-evaluates the touched coins.
- A copy confirmation lets a thin coin enter without the paid trade feed (cheap-rug filter still applies: dev dump, bundle, sell pressure, price sanity).
- Score weights: kol_buy 3, kol_converge 3, fomo_new_wallet 3, good_dev 3, whale_buy 2. fast_moon (Core 3) now accepts kol_converge / fomo_new_wallet / good_dev / whale_buy WITHOUT a famous name (moonshot without virality).
- BLOCKER: Fomo API credits are 0 (limit 250,000) -> followed-trader and new-wallet feeds from Fomo are inert until Ali tops up. KOL (MadeOnSol) feed works but has fired 0 buys on young coins so far.
- Not covered yet: copy buys for coins older than the tracker window (>20 min); leaderboard-wallet copy on pump.fun/StonkFun needs Fomo credits or a trade feed; Base/BSC KOL polling; live adapters for BSC/Base/RHC (paper only).
- Tests: test_fast_lane 29 pass; rest of suite 954 pass (test_rhc_sell.py hangs in the Linux VM on a network wait, unrelated, skipped).

## 1L. 15 Oct 12:00 PKT — direct wallet watch, Fomo hourly pacing
- Why KOL feed showed 0: MadeOnSol routine budget ~190 calls/day, feed is skipped most hours ("paced for this hour"), so followed traders were barely polled.
- NEW wallet_watch.py: wallet addresses harvested free from every KOL-feed result (kol_wallets_v1); worker task polls a round-robin slice every 60 s straight from the Solana RPC (getSignaturesForAddress, getTransaction), flags BUYS (token balance up, SOL down), cap WALLET_WATCH_MAX_CALLS_DAY (default 15000). Feeds copy_buys_v1 via copy_signals.refresh.
- Fomo credit governor already existed (daily budget 7500, 402 back-off, free /v2/me check every 30 min); added hourly pacing (budget/24 x1.5) so a day cannot be burned in one hour.
- Gaps: Base/BSC/Robinhood copy trading (no wallet addresses; plan: own GeckoTerminal pool-trades wallet scoreboard); dev-launch ALERT on dashboard.

## 1M. Dev-launch alert + EVM copy trading + wider wallet watch (15 Oct 2026, ~12:00 PKT)
- `dev_alerts.py`: when a creator on the good-dev scoreboard (dev_rank, stats in state `dev_stats_v1`) or the dev list launches, a Telegram alert + dashboard Alerts-tab entry (module DEV LAUNCH). Alert only; the same signal also adds the `good_dev` confirmation. One alert per coin, one per dev per 10 min.
- `wallet_watch.py` now also watches our own pump.fun smart-money roster (layer2b, 13 wallets) and `smart_wallets_v1` on-chain, besides the KOL wallets harvested from the feed. This is the pump.fun / StonkFun "leaderboard" source (self-built; no free public leaderboard API exists).
- `evm_wallets.py` (Base/BSC/Robinhood): learns wallets that were early buyers in >=3 distinct trending (winning) pools, from free GeckoTerminal trades; flags fresh pools a smart wallet is buying -> `smart_wallet` confirmation in multichain_sniper. Paper only (no live adapter on those chains). NOT tested against the live API from the build VM; first real check = log/state after restart. Survivor-biased, needs days of running to fill.
- Tests: tests/test_fast_lane.py 38 pass; multichain + early tests 30 pass.

## 1N. Dev history backfill; screenshot dev list OFF (15 Oct 2026, ~12:00 PKT)
- `dev_history.py` + worker task `dev_history_backfiller` (2 devs / 90 s): lists a dev's pump.fun creates via Helius parsed history (fallback plain RPC), judges each coin on free DexScreener data (win = graduated or mcap >= $60k now), stores `dev_history_v1`; `dev_rank.refresh` merges it into the good-dev list. Queue = devs with >=2 launches in our logs + the screenshot wallets (their real history is now measured, not claimed).
- Screenshot dev list is OFF as an entry/alert source (EARLY_DEV_LIST_ON=1 turns it back on). Reason: 0/69 graduated, 3.9% vs 5.9% base.
- UNVERIFIED live: Helius parsed-history response shape was parsed tolerantly; check `[dev_history]` lines in logs/local_early-watch.log after restart.
- GMGN: key is free but needs Ali to create it (Ed25519 key pair + IP whitelist at gmgn.ai/ai); its public docs do not list deployer history. Not wired.

## 1O. RPC fix (15 Oct 2026, ~11:30 PKT) - commit 0a89aa1
- Found in restart logs: wallet_watch failed on every call ("all pool endpoints failed") because executor/rpc_pool.py's Solana pool had only 3 free public RPCs. Ali's own Helius URL (.env SOLANA_RPC_URL) is now tried FIRST; API keys are masked in logs/results. So wallet watching and dev_history only start working after this fix is pushed + restarted.
- TO VERIFY AFTER RESTART (logs/local_early-watch.log): `[wallet_watch]` lines without "all pool endpoints failed"; `[dev_history] <dev>.. -> {...launches, wins}`; `[dev_rank] good devs N`; DEV LAUNCH alerts on Telegram/dashboard; state evm_wallets_v1 growing for base/bsc/robinhood_chain; KILL_SWITCH still present.
- Commits not yet pushed at time of writing: 9e16aa9..0a89aa1 (1d85043, 9e16aa9, dee78c0, 457f2fb, 0a89aa1 + this docs commit).

## 1P. KOL feed shared cache (15 Oct 2026, 16:00 PKT check-in #1)
- Finding: KOL copy feed stayed at 0 because the slow scheduler and the real-time worker share the ~190 calls/day MadeOnSol budget and the worker was paced out all day (kol_wallets_v1 never harvested, so wallet watch only followed the 13 pump.fun roster wallets).
- Fix: `layers/kol_feed.cache_put/cached_feed` store the scheduler's last good roster trades in state; `copy_signals.refresh` falls back to that cache (<=2 h old) when paced. No extra MadeOnSol calls. Needs push + restart.
- 4h data (restart 11:42 PKT): dev_history 243 devs, 3,617 launches, 23 wins (18 devs with any win); good devs 67; EVM smart wallets: bsc 3, robinhood 11, base 3 (5 pool hits); 18 DEV LAUNCH alerts; wallet_watch 0 copy buys (13 roster wallets, partial DNS failures on the PC: ~1,278 getaddrinfo errors).

## 1Q. hot_meta signal; winner-creator backfill; KOL diagnostics (15 Oct 2026, ~20:00 PKT)
- New confirmation `hot_meta` (+2): the coin's name shares a word with a coin launched 1-6 h earlier that already ran >=5x (dev_rank.hot_words, state hot_words_v1, refreshed every 20 min). Offline test on 36,460 coins: reaches 10x 6.54% vs 1.96% otherwise (>=3x 9.6% vs 4.6%); `meta_test.py` reproduces it. Weight is a first guess; paper evidence over the next 24 h decides.
- Dev history queue now includes creators of ANY winning coin in our logs (2,173 devs), 3 devs / 60 s; so we learn which winners' devs have other winners.
- KOL feed diagnostics (kol_feed_diag_v1: total trades, in-roster trades, top names) to find out why the cached feed holds 0 roster trades.

## 1R. Score breakdown in alerts, dud filter, top-trader leaderboard, trending hot words (15 Oct 2026, ~21:00 PKT)
- Alerts (Telegram + dashboard Alerts tab) now show `Score: N = check +points, ...` and whether live-eligible (fast_lane.score_parts / score_text).
- Dud filter study (`dud_filter_test.py`, 36,481 coins): creators with >=3 launches = 49% of coins, only 12% of winners -> `serial launcher` score -2 (not for good_dev/repeat_grad). Coins with <5 buys at the first snapshot: 15% of coins, 0.5% of winners (not yet used as a veto; candidate once snapshots cover all coins). Caveat: only tracked coins have snapshots.
- `solana_tracker.py`: daily top-trader leaderboard (Solana Tracker PnL v2, consistent traders, excludes arbitrage/one-off winners). NEEDS Ali to create a FREE key at solanatracker.io and add `SOLANATRACKER_API_KEY=...` to .env (free plan 2.5K req/month; we use ~2/day). Wallets are watched on-chain via wallet_watch (label st:xxxx). Free-plan coverage of this endpoint is not confirmed in their docs.
- Hot words also come from GeckoTerminal trending Solana pools that are already up (>=50% 1h or >=200% 24h).

## 1S. Solana Tracker credits: leaderboard fix + first-buyers scoreboard (15 Oct 2026, ~20:00 PKT)
- Leaderboard: API works (100 traders per call) but our parser stored 0 wallets; fixed (no PnL-sign filter) in 036b9aa and the retry timer was reset. VERIFY after restart: log line `[leaderboard] ... N wallets stored` with N > 0.
- NEW `st_enrich.py` + `dev_rank.winner_mints()` (commit 6de0e1a): every 15 min, 3 x GET /first-buyers/{mint} for our newest coins that ran >= 10x (400 in logs). Wallets that were early AND profitable on >= 3 different winners -> state `st_early_wallets_v1` -> watched on-chain by wallet_watch (label se:) and unioned into smart_wallets() (-> smart_wallet confirmation). Caps: 45 calls/day, 1,800/month (free plan 2,500).
- NOT confirmed: endpoint credit cost and free-plan access to /first-buyers (check log `[st_first_buyers]`; diag in state `st_fb_diag_v1`). Early+profitable wallets may be snipers/bots; 24 h data decides if it helps.
- Tests: 48 passing. Live stays OFF (KILL_SWITCH present).
- Where we stand / next: (1) 24 h decision report at ~11:43 PKT 16 Oct (outcome by score and by each confirmation, ex-best-2, 10-25% costs); (2) read kol_feed_diag_v1, copy buys still 0; (3) confirm leaderboard + first-buyers wallets arrive; (4) no rule beats break-even yet (all coins x0.81, score>=5 x0.85) so no live trading until the gate passes.

## 24h decision report (2026-10-16, cleaned data, 2,920 decisions, 15% round-trip cost, return = value at last checkpoint)
- No score level clears x1.0 once the best 2 trades are removed: score>=3 0.86, >=4 0.88, >=5 0.87 (hit2x 16%, hit5x 4%, hit10x 1%). ALL signals 0.80.
- The only "big mean" (score>=5 = 1.35, kol_buy 6.69, hot_meta 2.34) comes from ONE token (SPCXxcqX..., a tokenised-stock-style mint, 0 buyers in our stream, $1.5M liquidity) = wrong token, not a win. Remove it and the edge is gone.
- Confirmations alone (ex-best-2): kol_buy 0.91 (n=44), good_dev 0.51 (hit2x 46% but loses after cost), smart_wallet 0.60, whale_buy 0.45, hot_meta 0.71. None clears 1.0; none has >=30 clean winners.
- Train/test (first vs second half): both below 1.0 ex-best-2 (0.82-0.88).
- Pools: 223 early wallets, 200 leaderboard wallets, first-buyers diag shows pnl 0.0 on sample rows (not yet proving skill).
- Decision: live stays OFF. Gate not passed. TODO: add rule "ignore coins with 0 buyers in our stream" to entry_gate.

## 16 Oct: why "closing records" looked empty + first real pattern study (snap_winners.py)
- 'final' rows show buys=0 for ~93% of coins BY DESIGN: the metered PumpPortal trade feed only watches proven lanes. Trade-level data exists only for those. The free DexScreener minute-by-minute 'snap' rows cover 21k coins and are the right base for pattern study (snap_winners.py).
- 21,037 coins, measured from the ~1-minute snap: 1.1% reach 2x, 0.5% 3x, 0.2% 5x, 0.1% 10x. Minute-1 buys, volume, mcap do NOT separate winners.
- Only separators found: creator with >=1 graduate (3x rate 2.3% vs 0.5% base, n=481); creator 3-15 launches (0.9% vs 0.3% for 1 launch); launches>=3 + buys>=6 + buy/sell>=2 (1.5% 3x, n=893).
- Serial-launcher -2 penalty was WRONG -> switched off (SERIAL_LAUNCHER_PENALTY=0). Even the best filter hits 3x only ~2%: still no positive expected value without fast entry. Live stays OFF.

## 16 Oct lab (lab24.py): 17,020 coins, buy at ~1-min snapshot, TP/SL exits searched, 15% round-trip cost, train/test halves
- Base: 0.6% reach 3x. Best exit (TP5x / SL 0.7x) on ALL coins = 0.84x. Every cut tried stays 0.83-0.92x (creator-with-graduate 0.92/0.89; launches 3-15 + buys>=6: 0.85).
- Only cut above 1.0 = "mcap<1,500 at minute 1" (n=155, EV 1.03 -> 0.99 ex-best2, second half 0.89): not robust, likely thin-price artefact. Not adopted.
- Hour of day, clone names, mint suffix, dev buy size: no usable edge. Early-buyer wallets: 18 wallets seen in >=3 tracked coins, none with a 3x.
- Verdict: no tested filter or exit beats cost. Live stays OFF. Fast-entry (sub-second) is the missing ingredient, not more signals.

## 16 Oct: buy-at-launch and buy-after-graduation tests (speed24.py, grad24.py)
- Buy at launch (creator already graduated a coin, TP5/SL.5): EV 1.01 at 15% cost, 1.13 at 5% cost, n=218 only (thin, assumes instant fill). Everything else at launch: 0.83-0.99.
- Buy after graduation (631 coins, entry ~$50k mcap median): 9% reach 2x, 4% 3x, 1% 5x; TP/SL EV 0.69-0.85 at 8-15% cost. Waiting for +15/30/50% momentum first: still 0.77-0.82 ex-best2. No edge.
- Coins Ali named from FOMO (Pawn, Feels Good, John Phil: 0 launches in our log; Useless 5, Claws 2, Mars 33, Whale 58, Claudia 123 with 14 graduated, best post-grad 2.0x). Several are simply not in our 10-16 Oct launch stream -> either older coins or other launchpads; coverage gap to check next.

## 16 Oct boom_lab findings (free GeckoTerminal + Solana Tracker, last ~6 months)
- Real memecoin monsters on Solana started on pump.fun and graduated to pumpswap: ANSEM ($35k -> ~$444M, 155k holders), CATE ($26k -> ~$122M, 142k holders), CLAUDIA ($48k -> $11.7M in 4 days, 33x from first $50k close). STONK / ANTFUN also 100k+ holders.
- Distribution tell: Claudia had 2,123 bundler wallets holding 96% of initial supply (+3 snipers); baton 1,093 bundlers / 19% ; STREAM 335 bundlers. Winners are often heavily bundled/sybil-seeded at launch.
- 15 coins created 14-15 Oct (ARF, NODF, UDR, GDF, XTO, SARP, SI276, DOPE, ATFS...) show impossible FDV ($100M+ born), ~2,500-3,000 holders each, identical first-buyer profits -> coordinated/wash clusters, NOT organic winners.
- Wallet cluster Fn9n3ft.. (15 'winners'), DKKybx.., FRQD8L.., EvDTDv.. etc: profit ~$0-$1 -> bots. 4 of them were in OUR smart-wallet pool (polluting smart_wallet confirmation). 63 listed in bot_wallets.txt and now excluded (wallet_watch, worker smart_wallets, st_enrich rebuild; st_enrich also needs >$100 profit).
- No wallet found early in several REAL winners with real profit (STONK buyers made $0.4-3M but only on that one coin).
- GeckoTerminal FDV for pump.fun coins is unreliable; 'born' size uses pool creation date, wrong for older tokens. Snipers/insiders fields come back 0 almost always (ST does not fill them for old coins).

## 16 Oct runner_lab + wallet_probe (unbiased test, 300 random coins)
- Runner features (103 runners vs 160 duds, Solana Tracker launch data): only weak/thin separators. Creator launches 3-15 + >=10 buys at 1 min: holdout 2.7% runner rate (base 0.49%). Creator with a graduated coin ~4% (11 runners / 2 duds). Bundles >=5: ~5% but 14 runners / 2 duds, holdout n=1. Snipers, dev buy, holder count: no use (holders is measured today = leakage). Insiders: 0 everywhere (Solana Tracker does not fill it).
- Wallet BwWK17cb.. (first-4 buyer in 80 of 103 runners, 150 buys/coin = volume bot): in the random sample it is early in 23 of 300 coins (7.7%) and those coins hit 3x only 4% -> 0.84x/0.91x after cost. SELECTION ILLUSION, not a signal. sssssDdM.. 19 coins, 0.97x/1.05x (n=19, noise). Other candidates: too few.
- Conclusion: no launch-time feature or wallet found that lifts the runner rate anywhere near the ~30% needed to pay after costs. Live stays OFF. S1c keeps collecting data in paper mode.

## §0C — LOSS-PATTERN ANALYSIS + BOOK RESET (26 Oct 2026, 08:35 UTC / 13:35 PKT)

**Multi-chain coverage confirmed.** `multichain_sniper.py` scans Solana, Base, and BSC
via GeckoTerminal (`GT_SLUGS = {"solana","bsc","base"}`) plus a separate `robinhood_chain`
feed. Live tonight on all three: Solana (majority of volume), Base (REPO/ETH), robinhood_chain
(LAUNCH/WETH, INSE/WETH, WDY/WETH). Detection is NOT Solana-only -- it just hasn't been
the focus of recent bug-fixing, which was all on the Solana `layers/` finder stack.

**Analyzed all 241 closed paper trades across sprint/core1/core2/compound (pre-reset).**
Exit-type breakdown (this is the real pattern, not noise):
- stop_loss:     106 trades (44.4%) -- 100% losses, avg -57.1% -- hit hard stop-loss floor
- time_stop:      60 trades (25.1%) -- 100% losses, avg -30.7%, avg hold 45.1min -- ran out
  of time stalled, never moved, got cut
- rug_liquidity:   5 trades (2.1%)  -- 100% losses -- liquidity vanished in 1-2.3 minutes
  (classic rug pattern, confirmations list looked fine going in: pressure/buyers/velocity/
  volume/fresh -- current confirmations do NOT detect this)
- trailing_stop:  55 trades (23.0%) -- 48/55 WIN, avg +51.9%, avg hold 8.4min -- this is
  where ALL the profit comes from
- fully_sold:     11 trades (4.6%)  -- 100% win -- take-profit hit

**Conclusion: ~70% of all trades (stop_loss + time_stop) are pure losses with no
offsetting signal pattern found (confirmations on winners and losers overlap heavily --
pressure/buyers/velocity/volume/fresh appear on both). The edge lives entirely in what
happens AFTER entry (exit management: trailing_stop/fully_sold), not in which coins get
picked. Entry-mcap and entry-age, re-checked this session, show no separation between
winners and losers either (after removing 2 corrupted $1.9B-mcap outlier rows). This
confirms, a second way, the earlier finding that entry speed is not the bottleneck --
now extended to: entry SELECTION overall (which confirmations fire) isn't clearly
separating winners from losers in this sample. The real lever is tightening stop_loss/
time_stop exit rules and/or improving what happens in the first ~1-2 minutes to avoid
rug_liquidity deaths, not changing what layer17-21 flag as a candidate.**

**Action taken:** archived all 241 closed trades to
`grad_runners/archived_closed_trades_pre_reset_1793003701.json` (full backup also at
`.gem_alert_state.json.bak_pre_reset_26oct`), reset sprint/core1/core2/compound to clean
$100 seed / $0 realized P&L. 20 open positions left untouched. Commit f039e40.
Going forward, these four books measure ONLY post-fix performance (after layer20/21 time
budget fix + worker_early_watch.py smart_wallet wiring), not diluted by 241 pre-fix trades.

**Not yet done:** no code change to exit logic (stop_loss/time_stop thresholds) based on
this finding -- that's the next real lever to pull, pending Ali's steer on priority.

## §0D — LIQUIDITY-DEPTH FIX FOR THE _FREE PATH (26 Oct 2026, 09:05 UTC / 14:05 PKT)

**Call frequency ruled out as the cause of catastrophic losses.** Positions re-price every
6s (`EARLY_MANAGE_SECONDS`). The worst stop_loss trades (-75% to -98%) still got 5-20 checks
before closing -- the pool collapsed between two 6s samples, not because checks were too
slow. No realistic check-frequency change would have caught these.

**Real cause, confirmed from the data:** every one of the 23 catastrophic (<-75%) stop_loss
trades (11 unique coins) was entered via the `_free` confirmation path (pressure_free/
velocity_free -- the DexScreener-based fallback `on_free()` uses when the paid PumpPortal
trade feed is off, which it has been for 36+ hours). ZERO catastrophic losses used the
paid-feed confirmations (pressure/buyers/velocity/volume/fresh). The free path had no
liquidity-depth check at all -- only a volume check (`EARLY_FREE_MIN_VOL_USD`), which a
thin/fake pool can pass with one wash-traded burst.

**Fix implemented and committed (401b0fe):** threaded DexScreener's `liquidity.usd` through
`worker_early_watch.py`'s free-data poll into `layer17_early_entry.EarlyTracker.on_free`
(new `liquidity_usd` param, stored as `Rec.free_liq`). `pressure_free`/`velocity_free` now
require `free_liq >= EARLY_FREE_MIN_LIQ_USD` (default $3000) to fire. Fails open when
liquidity is unreported (None) so a missing field never silently disables the whole free
path -- only a real, reported thin reading blocks it. Verified: targeted test (thin
liquidity blocked, healthy liquidity unaffected, missing data fails open) + full suite
1012/1012 passed.

**Needs a restart to take effect** (same as every code change tonight -- Ali must kill and
restart `start_local_mode.bat`). Until restarted, `worker_early_watch.py` is still running
the old code without this gate.

**Not yet done / open question:** the $3000 floor is a reasonable-sounding default, not
measured against this system's own data (same honesty caveat `liq_mcap_check`'s own
docstring already carries for its 1%/2% ratios). Once a batch of fresh post-restart trades
accumulates, re-check whether $3000 is actually separating the good/bad _free entries or
needs tuning.

## §0E — SECOND ENTRY ENGINE'S LIQUIDITY/STALENESS HOLES FIXED (26 Oct 2026, 12:55 UTC / 17:55 PKT)

**First post-reset batch (163 trades, ~5h after restart) showed performance had NOT
improved -- Sprint and Compound both back at $0.00.** Investigated honestly rather than
declaring the §0D fix a failure: it worked exactly on its target (layer17_early_entry.py's
`_free` path), but 25 of 32 catastrophic losses this batch came from `multichain_sniper.py`
-- a THIRD, independent entry engine (GeckoTerminal-sourced, chains: solana/bsc/base/
robinhood_chain) that was never touched. Correction to an earlier mid-session misstatement:
these were NOT from the PumpPortal paid-feed path as I first said -- verified by `tags.src`
on the actual closed-trade records ("gt", not the paid feed).

**Two distinct, separately-confirmed bugs in multichain_sniper.py, not one:**
1. Its liquidity floor (`MC_MIN_LIQ_USD`) only applied `if src == "gt"` -- stonkfun entries
   carry their own real `liq` field and skipped the check entirely. Also the $2500 floor
   itself was too low -- 14 trades (rug_liquidity/stop_loss) drained through it, several
   within 2-40 seconds of entry (LNCH, Squzepad, minigang). Fixed: raised to $4000, applied
   to every source with a reported liq (not just gt); liq=None still fails open.
2. 11 catastrophic losses were `written_off` (pool stopped reporting data, NOT a price
   crash) -- all 11 sat unpriced for exactly the full 30-minute `UNPRICEABLE_MINUTES_
   BEFORE_WRITE_OFF` window before being booked as a loss. That logic was working as
   designed (an honest, deliberate rule: unpriceable = full loss, never silently dropped)
   but 30 minutes needlessly tied up open-position slots on something dead by cycle 3-4.
   Cut to 8 minutes (now env-tunable).

Commit 7c40ac5. Targeted tests + full suite (1012/1012) both pass. **Needs a restart to
take effect**, same as every change tonight.

**Honest framing for Ali:** three independent entry engines exist in this system
(layer17_early_entry.py paid-feed + its _free fallback, multichain_sniper.py). Each had its
own version of the same missing-liquidity-floor problem, found one at a time by running the
system and tracing real losses -- not by inspection in advance. This is the actual process
of hardening against adversarial memecoin markets, not a sign of repeated incompetence --
but it does mean "is this finished" has no honest single answer; it means "here's what's
been found and closed so far, here's what a fresh batch shows next."

## §0F — _FREE LIQUIDITY GATE FLIPPED TO FAIL-CLOSED (26 Oct 2026, 16:15 UTC / 21:15 PKT)

**Third post-restart check (253 trades, ~3h after the §0E restart) was worse, not better** --
Sprint/Core2/Compound all back at $0.00, net P&L more negative than the previous batch.
Investigated honestly rather than declare it a wash.

**Confirmed §0D and §0E WORKED on what they targeted:** multichain catastrophic losses
dropped 25 -> 12 this batch; all 4 written_off trades this batch correctly cut off at ~8.1
min (not 30). Both real, both holding.

**But a new dominant failure appeared: 45 of 57 catastrophic (<-75%) losses this batch were
on the SAME `_free` path fixed in §0D, still firing `pressure_free`/`velocity_free`.** Root
cause, traced carefully (not guessed): the §0D fix failed OPEN when liquidity was unreported
(`free_liq is None`), intended to avoid silently disabling the whole path on missing data.
That was the wrong call for this path specifically -- missing liquidity is disproportionately
common on brand-new coins (this batch's losses were entering ~24s old, before DexScreener
has indexed real pool depth), which is exactly the riskiest window, not a rare case to be
lenient about.

**Fixed (commit 6cc204e): flipped to fail CLOSED** -- `pressure_free`/`velocity_free` now
require liquidity to be BOTH reported AND >= the floor (raised $3000 -> $6000, second
independent tightening). Known trade-off, accepted: fewer `_free`-path entries overall,
including some winners, in exchange for cutting this loss pattern. Existing test updated,
two new tests added locking in the new behavior. Full suite 1014/1014 passed. **Needs
restart.**

**Status for Ali, plainly:** three liquidity-related holes found and closed across three
engines/paths in one night (layer17 `_free` -- twice, multichain_sniper.py once), each
found only after running the system and tracing real losses. This is not evidence the
system is broken beyond repair -- it is evidence the holes ARE findable and ARE getting
closed, one real batch of data at a time. What it does mean honestly: no single "final"
restart tonight should be expected to flip results immediately. The next clean read needs
a longer, quieter observation window once the fail-closed change has had several hours to
run, not another 3-hour spot-check -- otherwise each check just finds the next hole before
the current fix has had room to prove out.

## §0G — THE REAL CAUSE OF BOOKS STUCK AT $0.00 FOUND (27 Oct 2026, 01:32 PKT / 20:32 UTC)

**1:30 AM scheduled check-in found something more fundamental than a trading-performance
problem.** Set out to evaluate whether the fail-closed `_free` liquidity fix (§0F) reduced
catastrophic losses. Instead found: core1, sprint, and compound had each sat at EXACTLY
$0.00 for 3+ HOURS STRAIGHT -- not low, mathematically dead, refusing every single signal
the whole time regardless of quality. Could not evaluate any of tonight's fixes against
these three books because they had not opened a single new trade in hours.

**Root cause, confirmed from live state (not guessed): `check_bust()`'s guard -- "let this
book's one open sniper position resolve before reseeding" -- had no timeout.** Each dead
book had exactly one open "early" position, genuinely still priceable (not stuck/dead data,
real live mcap updates), simply riding for 190+ minutes as a long runner. That's correct
behavior for the runner itself (should not be force-closed), but it meant the ENTIRE BOOK
stayed frozen at $0 the whole time, unable to reseed and act on any other signal -- a
single slow-moving position silently blocking the whole book indefinitely.

**This also explains the whole night's pattern of "fixes don't seem to move the balance
numbers":** three of four books have likely been intermittently stuck this way for much of
the night, so improved entry quality from the liquidity fixes had no books available to
prove itself on.

**Fixed (commit 68d2fef):** `check_bust()` now times the block. Under `BUST_WAIT_MINUTES`
(default 60 min), it still waits as before. Past that, it reseeds the book at its $100 seed
and resumes trading in parallel -- the long-running position is never force-closed, it
keeps riding independently and closes on its own exit rules whenever that happens. New test
confirms both halves: a fresh (5-min) blocker still waits; a stale (90-min) one no longer
blocks reseed. Full suite 1015/1015 passed (run in two halves due to a tool time limit, not
a shortcut -- both halves clean). **Needs a restart to take effect.**

**Honest framing:** this is a different KIND of bug than tonight's earlier three (which
were all about which coins get let IN). This one is about a book getting permanently stuck
regardless of entry quality -- arguably a bigger deal, because it means the system could
look broken from the balance number alone even when the entry-side fixes are working
correctly. Worth watching closely post-restart: does the balance actually start moving
again, not just whether new trades open.

## §0H -- Phantom balance bug: compound book showed $1,309 equity on a bleeding $100 book,
## feeding paper stakes 21x beyond any tier cap (27 Oct 2026)

**What triggered this:** Ali asked for a status check ~5.5 hours after the previous
restart. Live state showed `compound` with `equity=$1,309.74` and `realized_pnl=$1,599.63`
-- looked like a huge win, but it had actually busted (hit the $10 floor) THREE times in
under 3 minutes (20:42:29-20:44:32 UTC) the same night, and one currently-open position
("PJAM / CARDS", Solana, multichain/GeckoTerminal source) had `usd=$1,289.59` -- more than
21x the mathematically-possible maximum stake for a $100-seed book (tier cap: 0.60 x equity,
further capped at 0.80 x equity -> $60 max, right after a reseed).

**Root cause, confirmed by replaying the real closed-trade history, not guessed:**
`realized_pnl(book) = gross_pnl - withdrawn - bust_offset(book)`. `bust_offset()` was
summing EVERY past bust's `realized_at_bust` snapshot together. But each snapshot is
already the full cumulative gross-minus-withdrawn total at that instant, not a delta --
so a book that busts more than once was having the same historical losses double- and
triple-counted. compound's three same-night busts produced snapshots of `0`, `-3413.10`,
`-3645.17`; summed, `bust_offset = -7058.27`. Subtracted (as a negative) from realized_pnl,
that doesn't cancel out old losses -- it ADDS thousands of dollars of phantom balance back
onto the book. Confirmed live: `gross=-3653.86` (book was genuinely down ~$3,654) but
`total_value` computed to `$3,504.41` instead of `$0`.

This phantom equity is exactly what fed the oversized stake: `tier_stake_usd()` sizes off
real `equity()`, so an inflated equity reading produces an inflated (but internally
"correctly capped relative to the wrong number") stake. The cap logic itself was never
broken -- the balance it was capping against was wrong. Traced and ruled out as the cause
first: `ladder_scale` (only scales partial sells of already-open positions, not initial
sizing), the tier/cap formulas themselves (verified mathematically sound), and a
concurrency race in `act()` (plausible contributor to stacking but not the primary cause
once the balance corruption was confirmed directly from real numbers).

**Fixed (this commit):** `bust_offset()` now returns only the MOST RECENT bust's snapshot,
not a sum across all of them -- each new bust already captures all losses since the book
began, superseding every prior one rather than adding to it. Verified immediately:
`compound` now correctly reads `equity=$0.00` (a genuinely busted book), down from the
phantom `$1,309.74`. sprint/core1/core2 numbers are unchanged (each has only busted once
so far, so this bug hadn't bitten them yet -- but it would have, identically, the next time
any of them busts a second time).

**Also corrected:** the one currently-open position sized off the phantom balance
("PJAM/CARDS", `usd=1289.59`) had its `usd` field rescaled down to $40 (tier=standard x the
$100 seed equity it should have been sized against) directly in state, rather than force-
closed -- the position itself is real, legitimately confirmed, and currently up about +22%,
so it's left open and managed by the normal exit rules; only its distorted stake size was
corrected so its eventual P&L is realistic instead of a four-figure distortion.

New regression test (`test_bust_offset_uses_latest_snapshot_not_sum`) replays a book busting
twice in a row and asserts it reseeds to exactly $100 both times, never a stacked phantom
balance. Full suite 1016/1016 passed (1015 + this new test; run in two halves due to a tool
time limit, not a shortcut -- both halves clean).

**Honest framing:** this is a different root cause than every other fix tonight. It wasn't
about which coins get let in or how long a book waits to reseed -- it was simple accounting
math being wrong once a book busted more than once in the same run. compound is the only
book that happened to bust 3x in 3 minutes tonight, which is what exposed it; the other
three books are carrying the identical latent bug and will hit it the first time any of
them busts twice. **Needs a restart to take effect for new trades; the state correction to
the one mis-sized position is already applied directly and doesn't need a restart.**

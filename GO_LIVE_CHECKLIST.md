# S1c MASTER CHECKLIST - THE ONLY SOURCE OF TRUTH
Same text lives in: this file (git) = Notion "S1c MASTER CHECKLIST" page. Nothing else is a checklist.
Old versions are archived in docs/archive/ (history only - never read them for status).
UPDATE RULE (Claude, after every change): rewrite THIS file, copy it to the Notion master page, then commit. Ali pushes.
Legend: [x] done & running | [~] built, under test | [ ] to do | (A)=Ali | (C)=Claude

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

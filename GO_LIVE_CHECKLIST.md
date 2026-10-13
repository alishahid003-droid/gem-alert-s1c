# S1c MASTER CHECKLIST - THE ONLY SOURCE OF TRUTH
Same text lives in: this file (git) = Notion "S1c MASTER CHECKLIST" page. Nothing else is a checklist.
Old versions are archived in docs/archive/ (history only - never read them for status).
UPDATE RULE (Claude, after every change): rewrite THIS file, copy it to the Notion master page, then commit. Ali pushes.
Legend: [x] done & running | [~] built, under test | [ ] to do | (A)=Ali | (C)=Claude

## 1. STATUS - 13 Oct 2026, 16:55 PKT
- Everything is PAPER (EXECUTION_ENABLED=false). No live trades anywhere. 923 tests pass.
- Running since the 14:12 PKT restart: all items in section 3. Dashboard: http://localhost:8787
- Last commit 344b944 (e2e replay test + loop). Git, folder and Notion are in sync as of 16:55 PKT.
- RUNNING NOW in its own CMD window: `python e2e_test.py --loop` (end-to-end replay test, no CoinGecko key yet = slow pace). First coins become replay-ready ~20:00 PKT; verdict (needs >= 30 entries) expected tonight/overnight. Its first batch (14:31) was useless (coins fetched minutes after launch) and was fixed.
- e2e_test.py has no unit tests yet (checked by hand with a mocked HTTP call).

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
- [x] Infra: local mode, DNS-over-HTTPS fallback, dashboard is the only alert place (Telegram off), legacy stage/scalper paper traders retired.

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
3. [ ] (A) PumpPortal key (~0.02 SOL; send ONLY the exact fee to the address its site shows; Phantom has $1.53 ETH + $0.57 SOL) and a free Helius key -> .env as PUMPPORTAL_API_KEY and SOLANA_RPC_URL. Never paste keys or the seed phrase in chat. Then say "keys are in" and Claude guides the Admin-CMD restart. This enables buyers/pressure, the Conviction tier, curve-phase 10-25k entries with rug filters (creator dump, bundled launch, one-wallet dominance, sell pressure).
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
- Security: keys and wallet secrets only in .env, never in chat; the Phantom seed phrase/private key is never shared or entered anywhere; PumpPortal gets only the exact fee amount to the address its site shows; live money only after proven edge and only tiny stakes.
- Honest risk: the targets are the growth REQUIRED, not shown performance. Sprint is lottery-like. Live stakes tiny, one lane first.

## 8. CHANGE LOG (newest first)
- 13 Oct 16:50 e2e_test.py: unbiased end-to-end replay (real gate, tiers, exits) with --loop, 4h-old coins only, optional free CoinGecko key; commits facdfac, 344b944.
- 13 Oct 14:20 Single source of truth rewrite; history archived to docs/archive/.
- 13 Oct 14:10 Fomo: free /v2/me balance check + auto-resume; fomo_me.py.
- 13 Oct 14:00 Slot rules (extra slots + stale-flat swap).
- 13 Oct 13:40 Winners tracker, early_log outlier clip.
- 13 Oct 13:20 Early worker tiers, serial-launcher block, dev_list probe.
- 13 Oct 13:00 Conviction tiers 15/40/60, biglaunch + Robinhood probe lanes.
- 13 Oct 12:30 New exits (stop 0.35, trail 0.40, ladder 20/15/10, dead-coin 90 min) from winners replay.
- 13 Oct 05:00-12:00 Winners replay + discovery, copycat resolution, Base added; entry logging; API budget ledger + gate; fake +$505 bug fix; bust reseed; legacy traders retired; momentum bucket; Core 1/2/3 naming.

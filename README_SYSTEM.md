# S1c — How the system works (read this first)  ·  15 Oct 2026

**Live trading is OFF (KILL_SWITCH file). Nothing beats break-even yet (best fresh-data result x0.83 after costs). Everything below runs on PAPER except the single live path described in section 4.**

## 1. The flow (one coin, start to finish)
1. **Launch feed** (PumpPortal) announces a new pump.fun coin. We record creator, name, time.
2. **Tracker** watches the coin: free DexScreener data every few seconds, plus the metered trade feed only for coins with a proven signal (20,000 msgs/day cap).
3. **Filters** (block_reason) can veto it: blacklisted creator, dev dumping >30%, one wallet >40% of buys, bundled launch, heavy selling, absurd price jump, serial spammer (>20 launches), thin coin (<4 independent buyers, unless a copy signal exists).
4. **Confirmations** = evidence checks the coin has passed (section 2). They are added up into a **SCORE** (section 3).
5. **Router** picks exactly ONE stream by the coin's age and market cap (section 4).
6. **Paper ledger** records every entry and exits by the stream's rules; `early_log/*.jsonl` records the launch, price at 5/15/60 min, score and decision — that is the evidence base for every test.
7. **Live** only for stream "sprint", only when the score is high enough, and only after Ali removes KILL_SWITCH (section 5).

## 2. What each module contributes (confirmation → points)
| Module | Confirmation | Points | Source / status |
|---|---|---|---|
| Virality | famous_name | +3 | name matches a famous/trend term. Feeds: Google, Wikipedia, Bluesky, Mastodon work; X/TikTok/Reddit-popular empty |
| Virality / meta | hot_meta | +2 | name shares a word with a coin launched 1-6 h earlier that already ran >=5x (test: 10x rate 6.5% vs 2.0%, 36,460 coins) |
| Virality | narrative / narrative_first | +2 / +2 | coin name matches a live trend phrase / first coin with that phrase |
| Dev launches | good_dev | +3 | dev on our scoreboard (>=3 launches, >=30% wins; log history + Helius backfill) |
| Dev launches | repeat_grad | +2 | dev already graduated a coin before |
| Dev launches | dev_list (screenshot list) | 0 | OFF — 0/69 graduated |
| Copy trading | kol_buy / kol_converge | +3 / +3 | KOL feed + direct on-chain wallet watch (rare so far) |
| Copy trading | smart_wallet | +3 | our own pump.fun smart-money roster / wallets that won early in our logs |
| Copy trading | whale_buy | +2 | single buy >= 3 SOL within 90 s |
| Fomo | fomo_new_wallet | +3 | new high-balance wallet buying (Fomo credits are 0 → idle) |
| Dud filter | serial launcher (>=3 prior launches, not a proven dev) | -2 | dropping them removes 49% of coins but only 12% of winners (36,481 coins) |
| Copy trading | Solana Tracker first-buyers of our 10x winners (early + profitable on >=3 winners, `st_enrich.py`, ~45 calls/day) | via wallet watch + smart_wallet | same free key; budget-capped |
| Copy trading | Solana Tracker top-trader leaderboard | via wallet watch | free key needed (SOLANATRACKER_API_KEY in .env); ~2 calls/day |
| Market behaviour | pressure_free | +1 | buying pressure on free data |
| Market behaviour | velocity_free | +1 | price/volume velocity on free data |
| Market behaviour | sell/buy ratio | +2 (<0.30), +1 (<0.45), −2 (>0.60) | sells vs buys so far |
| Market behaviour | buys count | +1 (40–80 buys), −1 (>80) | too many buys = already late |
| Market behaviour | market cap | +1 (30–45 SOL), −2 (>=45 SOL) | chasing penalty |
Not scored but always on: rug/dump filters (veto), mint/freeze (pump.fun coins are always sellable; GoPlus honeypot/tax gate on Base/BSC only), catalyst radar (older slow system, feeds paper books), Base/BSC/Robinhood sniper + wallet scoreboard (paper only, own `smart_wallet` confirmation).

## 3. Reading a score (what "2", "5" mean)
Score = sum of the points above. Examples:
- **Dev only:** good_dev = **3**. Nothing else passed.
- **Dev + market confirms:** good_dev 3 + pressure_free 1 + velocity_free 1 = **5**.
- **Famous name + proven dev:** 3 + 3 = **6**.
- **Famous name only, but heavy selling:** 3 − 2 = **1**.
- **Two weak signals:** narrative 2 + pressure_free 1 = **3**.
- **Copy buy + dev + whale:** 3 + 3 + 2 = **8** (rare).
So **5 = roughly two independent kinds of evidence agreeing** (a signal AND the market behaving), never one check alone. The weights are a first guess and will be re-fitted on logged data.

**Alerts and the dashboard Alerts tab now show the breakdown**, e.g. `Score: 5 = good_dev +3, pressure_free +1, velocity_free +1 -> paper only (needs 5 for live)`.

## 4. What the score does (routing and execution)
Router (age since launch, market cap in SOL):
| Condition | Stream | Book | Stake (paper) | Can go live? |
|---|---|---|---|---|
| age <= 60 s, mcap <= 35 SOL, (famous / good_dev / whale / kol_converge / fomo_new_wallet) and >= 3 confirmations | fast_moon | Core 2 (lottery) | $2 | NO — paper only |
| else age <= 120 s, mcap <= 55 SOL | fast_sprint | Sprint | $5 | YES, only if score >= 5 |
| else age <= 20 min, mcap <= 130 SOL | fast_core | Core 1 | $4 | NO |
| plus any of the above with >= 2 confirmations, mcap <= 130 SOL | compound | Compound | $4 | NO (in/out daily stream) |
- **Score alone does not trade.** A coin must first survive the filters and fit a window. The score decides only whether the sprint entry may use real money (>= `FAST_LIVE_MIN_SCORE`, default 5).
- **Everything else is paper** and is how we test whether any score level has an edge.
- **A dev-launch-only coin (score 3)** is entered on PAPER in the stream its age/mcap select; it cannot go live. It reaches 5 only if the market also confirms (pressure + velocity). Same for every single signal — by design, because one check did not beat random in our data.
- Note: the strongest early coins (<=60 s, >=3 confirmations) go to fast_moon, which is paper-only; the live path takes the coins the router sends to fast_sprint.

## 5. Going live (the gate)
Live requires all of: KILL_SWITCH file removed by Ali; EXECUTION_ENABLED=true; EARLY_LIVE_LANES includes sprint; EARLY_FREE_CONFIRM=live; stream "sprint" passed the evidence gate (>= 30 fresh trades, mean > x1.0 after dropping the best 2); stake <= 5% of wallet (LIVE_MAX_STAKE_USD=5, LIVE_MAX_OPEN=3, LIVE_DAILY_LOSS_USD=17). Scale up only after 30 live trades > x1.0.

## 6. Where the data and docs live
- `early_log/*.jsonl` — every coin: create, checkpoints, score, decision, outcome.
- `collect_report.py`, `dev_analysis.py`, `score_backtest.py`, `runner_test.py` — analysis scripts (read-only).
- `GO_LIVE_CHECKLIST.md` (sections 1J–1O: what was built, how to verify), `SYSTEM_BLUEPRINT.md` (modules, chains, targets), this file.
- Code map: `worker_early_watch.py` (main loop + tasks), `layers/layer17_early_entry.py` (tracker, confirmations, filters), `layers/fast_lane.py` (score + router), `dev_rank.py` / `dev_history.py` / `dev_alerts.py` (devs), `copy_signals.py` / `wallet_watch.py` (copy trading), `evm_wallets.py` + `multichain_sniper.py` (Base/BSC/Robinhood), `layers/layer13_fomo_copytrade.py` (Fomo).

## 7. 16 Oct 2026 research results (all read-only scripts; full numbers in GO_LIVE_CHECKLIST.md "16 Oct" sections)
- `report24.py` / `lab24.py` / `speed24.py` / `grad24.py` / `snap_winners.py`: cleaned-data tests (17k+ coins, 15% cost, train/test). No score, filter, exit, launch-time or post-graduation entry clears x1.0 robustly. Base: 0.5% of coins reach 3x after minute 1.
- `history_study.py` / `boom_lab.py` / `boomers.py`: 6-month winners via GeckoTerminal + Solana Tracker (laptop only; this workspace cannot reach those hosts). Real monsters (ANSEM, CATE, CLAUDIA) began on pump.fun and graduated; heavy bundling common; 15 coins of 14-15 Oct were wash clusters.
- `runner_lab.py` / `wallet_probe.py`: runner vs dud launch features + candidate wallets. Only weak separators; wallet BwWK17.. is a volume bot present in ~8% of coins (not a signal).
- `bot_wallets.txt` + `bot_wallets.py`: 63 bot/sybil wallets excluded from smart-wallet pool; st_enrich now needs >$100 profit per coin.
- Serial-launcher -2 penalty switched off (SERIAL_LAUNCHER_PENALTY=0). entry_gate.py rejects fake DexScreener mcaps.
- STATUS: live OFF. Gate (>=30 fresh trades, mean >x1.0 after dropping best 2) not passed. Solana Tracker credits: ~800 of 2,500 used by 16 Oct (renews 15 Nov) - do not run more credit-heavy studies this month.

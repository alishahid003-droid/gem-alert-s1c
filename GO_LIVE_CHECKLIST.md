> The single up-to-date checklist is the MASTER STATUS block right below (updated Oct 13 2026).

# S1c — Go-Live & 80% Win-Rate Checklist (single source of truth)

# ▶ MASTER STATUS & ORDER OF WORK — updated 13 Oct 2026, 09:50 PKT  (THE single source of truth)

**UPDATE RULE (Claude, every time):** after EVERY build, test, restart or result — update this block, then HANDOFF.md, then the Notion S1c "CURRENT STATE" banner, then commit. Ali pushes with `git push origin main`. Nothing is "done" until its DONE-WHEN is met. Older sections below this block are history; if they disagree with this block, THIS BLOCK wins.
Legend: ✅ done & verified · 🟡 built, running/under test · ⬜ to do · 🔴 broken/blocked · (A)=Ali · (C)=Claude · (B)=both

**One-line state:** everything is PAPER. Sniper + multi-chain sniper have been running since 01:31 PKT Oct 13 (first real evidence run). No live trades anywhere. Live-trading adapter exists only for pump.fun (needs key); other platforms need adapters + wallets. 897 tests pass (mocked data only).

## 1. WHAT HAS BEEN BUILT
| Area | What | Status |
|---|---|---|
| Pump.fun early sniper | PumpPortal launch stream → EarlyTracker (dev_list, repeat_grad, smart_wallet, velocity, pressure, narrative confirmations) → lanes → paper entries | 🟡 running since 01:31 (launch feed works; per-coin trade feed OFF until PumpPortal key) |
| Exit engine (all sniper entries) | partial take-profit 2x/4x/10x, hard stop, trailing stop on moon bag after 2x, dead-coin time stop, 24h max hold, checked every 6 s; 10% round-trip paper cost | ✅ built, tested on mocks and replayed in backtest_sniper.py |
| Books / targets | Core 1 = Sprint 96h ($100→$10K, cash-out above $200 reseed); Core 2 = $125K/30d; Core 3 = $1M–$1.5M/180d; Compound (daily goal); copy books (Fomo Followers, Fomo New Wallets, Leaderboard, Dev Launch) | ✅ built · 🔴 Compound book shows $0.00 (lane can't open) |
| Capital engine | stakes = % of book trading balance (auto-compounding), pace gauge (x1.25 behind / x0.8 ahead), profit vault (banks 20–30% of profit above 3x seed) | ✅ built/tested |
| Multi-chain fast sniper | `multichain_sniper.py`: GeckoTerminal new pools for Solana(non-pump)/BSC/Base/Robinhood + StonkFun newest-tokens API; price-action confirmations; GoPlus honeypot gate on BSC/Base; same lanes/books/exits | 🟡 running; PAPER ONLY; entries seen on Solana, BSC, Base, Robinhood |
| Dashboard | single place for alerts, trade links (GMGN etc.), positions, paper+live P&L, book cards (balance/vault/total/cash-out), Sniper desk | ✅ http://localhost:8787 |
| Alerts | Telegram optional/off (blocked in Pakistan; DoH fallback built); dashboard is primary | ✅ |
| Evidence tooling | recorder (all launches priced +5/+15/+60 min, graduation events), `analyze_early.py` (lift tables, Wilson CIs), creator reputation, `backtest_sniper.py` (exit replay on real graduated coins), `run_checks.bat` (backtests + feed health → logs\check_*.txt) | ✅ built · 🟡 first run partial |
| Live execution (pump.fun) | PumpPortal Lightning adapter, risk caps (LIVE_MAX_STAKE_USD/LIVE_MAX_OPEN/LIVE_DAILY_LOSS_USD), kill switch (`python kill_switch.py on|off`) | ✅ built · ⬜ OFF (needs key + switches) |
| Infra | local mode (no Upstash), DNS-over-HTTPS fallback, cron-job.org GitHub jobs paused (Ali), watchdogs | ✅ |
| Docs sync | GO_LIVE_CHECKLIST (this), HANDOFF.md, MODULES.md, STATUS_2026-10-13_NIGHT.md, Notion banner | ✅ |

## 2. WHAT IS BEING TESTED RIGHT NOW
| Test | What it proves | Started | Status / result so far | Where to read |
|---|---|---|---|---|
| T1 24h evidence run (pump.fun recorder + multichain paper entries) | which launch features lift graduation odds on OUR data; paper P&L per book/lane/chain | 01:31 PKT Oct 13 (clock ends ~01:31 Oct 14) | 🟡 running. Logs 01:33 show early-watch started, multichain started, Robinhood slug = `robinhood`, paper entries on Solana/BSC/Robinhood/Base | logs\local_runner.log, logs\local_early-watch.log, dashboard, then `python analyze_early.py` |
| T2 Exit-engine backtest on real graduated pump.fun coins (`run_checks.bat`) | do the exit rules make money on real post-graduation price paths (NOT an entry-edge test: pre-graduation prices aren't public) | 01:07 PKT | 🔴 run 1 crashed (Windows unicode) + fake 1,000,000x glitch spikes → both fixed (be02027). Partial 92-coin file is rough/unusable. Deliberately STOPPED so it doesn't share GeckoTerminal quota with T1 | logs\check_backtest_top.txt / _new.txt after re-run |
| T3 Feed health (solana/bsc/base/robinhood/StonkFun) | each data feed actually responds on Ali's PC | — | 🟡 partly confirmed in sniper logs (all four chains + StonkFun producing candidates); full dump pending | logs\check_health.txt |
| T4 Live trade test | real buy+sell works | — | ⬜ not started (needs Phase 2) | — |

## 3. PLATFORM / CHAIN COVERAGE (what detects, enters, exits, and what's missing for LIVE)
| Platform / chain | Detection | Paper entries | Exits | Live buy/sell | To make it live |
|---|---|---|---|---|---|
| Pump.fun (Solana) | ✅ fast stream (pre-early) | ✅ | ✅ | 🟡 adapter built (PumpPortal Lightning), OFF | (A) PumpPortal key + ≥0.02 SOL → `PUMPPORTAL_API_KEY`; `EXECUTION_ENABLED=true`; `EARLY_LIVE_LANES`=ONE lane; tiny stakes; watch first trades |
| StonkFun (Solana) | 🟡 newest-tokens API (pre-graduation) + GeckoTerminal | ✅ | ✅ | ⬜ no adapter | (C) Jupiter/Raydium LaunchLab adapter (verify Jupiter routes curve-phase coins); (A) burner Solana wallet key in .env |
| Four.meme (BSC) | 🟡 only after a DEX pool exists (GeckoTerminal); bonding-curve phase NOT seen | ✅ (pool phase) | ✅ | ⬜ no adapter | (C) bonding-curve buy/sell via TokenManager + PancakeSwap after migration, honeypot gate, slippage; (A) burner BNB wallet; $2–5 test |
| Base | 🟡 GeckoTerminal new pools | ✅ | ✅ | ⬜ no adapter | (C) DEX-aggregator swap adapter; (A) burner wallet + 1inch API key |
| Robinhood Chain | 🟡 GeckoTerminal slug `robinhood` (confirmed) | ✅ | ✅ | ⬜ no adapter | (C) 1inch-based adapter (memecoin support unverified); (A) burner wallet, 2nd RPC |
| Fomo | ✅ wallet/roster alerts (credits exhausted, HTTP 402) | ✅ copy books | ✅ | ❌ no public trade API | stays alert + manual execution via dashboard links; (A) top up credits |

## 4. ORDER OF WORK (do top to bottom; what's next is the first ⬜)
### Step 1 — Run the evidence clock (NOW → ~01:31 PKT Oct 14)
- ✅ (A) Restart from an ADMINISTRATOR CMD (old admin python processes silently blocked every normal restart; this is why the sniper never ran before 01:31) 
- 🟡 (A) PC awake, Windows sleep = Never, minimized windows left open; restart ONLY via Admin CMD: `taskkill /F /IM python.exe` then `start_local_mode.bat`
- ✅ (C) Morning: read logs + state (08:00 PKT). RESULT of first 3 h (01:31–04:47 PKT, 42 paper entries): **every target book blown out** — Core 1 Sprint $100→$8, Core 2 →$0, Core 3 →$13, Compound $0. 0 real winners (best coin peaked 2.2x then fell). Solana 0/26 real wins avg -45%; BSC 0/9 avg -81%; Robinhood 0/5 avg -54%; Base 1 trade; entry mcap >$50k: 0/13 avg -82%; <$5k never moved (12 time-stops). This is the honest first reading: the current entry rules have no edge yet.
- ✅ (C) FIXED 08:30: (1) BUG: a drained pool booked a fake +$505 profit on one Sprint coin (sold at a glitched price) which inflated Sprint stakes to $135/$114 and hid real losses → liquidity-collapse exits now book ~total loss (test added). (2) Books that bust (<$10, nothing open) now auto-reseed at $100, count the BUST on the dashboard and keep trading, so the 24h evidence run is not starved (Compound was stuck at $0). (3) multichain log crash on non-ASCII coin names fixed. Needs restart to take effect.
- ✅ (A) 08:49 PKT: pushed 15f3f33; Admin CMD killed python and restarted `start_local_mode.bat` (new fixes live).
- 🟡 (C) API STATUS 08:55 (first 1 h of ledger, `python api_budget.py`): all within limits — MadeOnSol 6 calls today (cap 200/day), GeckoTerminal busiest minute 12/30 (3 HTTP 429), DexScreener 51/300 per min, GoPlus 17/30 per min, StonkFun 12 total, Birdeye 1. Solana public RPC is the weak point: 68 HTTP 429 → free Helius key (A) needed. FOMO: free bucket is 250,000 credits/month and was drained in hours on Sept 30 (before the credit governor existed); `remaining` has been 0 every day Oct 10–13, i.e. it did NOT refill on Oct 1, so the reset date is NOT the calendar month — unknown until the API tells us. Now saving every credit/reset header the API sends (`headers_seen` in fomo_credit_state); the governor probes once per 6 h at ~1 call. Free fixes: wait for the refill, or live without Fomo (all other feeds work). Do NOT pay.
- ✅ (C) 09:20 Runner bucket: `MC_MAX_MCAP_USD`=60k still blocks the moonshot lanes (Sprint/Core 2/Core 3) from chasing, BUT Solana coins up to `MC_MOMENTUM_MAX_MCAP_USD`=250k may now enter ONLY Core 2 ("core1" book) as a "momentum" entry, needing ≥4 confirmations. Own bucket → data will judge it. KEY FINDING: pump.fun per-coin trade feed is OFF (no PumpPortal key) so velocity / pressure / smart-wallet confirmations CANNOT fire on pump.fun coins — only dev_list + narrative can. That is why the pump.fun entries look random. PumpPortal key + 0.02 SOL (A) is the single biggest quality upgrade.
- ✅ (A) 09:03 pushed d159747; 09:04 restarted again (momentum bucket live).
- 🔴→✅ (C) 09:40 found after restart: (1) GeckoTerminal: 222 HTTP 429 in 1 h (shared 30/min across processes) → built a SHARED per-minute gate (`api_budget.gate`, 80% of each API's per-minute limit, uses the shared call log) hooked in utils/http.py. (2) The old fake +$505 row still sat in history (Sprint showed $894) → healed centrally in paper_ledger (a rug exit can never be a profit). (3) Compound's 22 open positions are LEGACY scalper/stage1 paper trades that bypass the books (-$2,075 on 1,032 rows, $25 stakes) — they blocked the bust-reseed; bust now only waits for a book's own sniper positions. TO DECIDE: retire the legacy scalper/stage1 paper trader or move it under book limits (it is why Compound is $0).
- ✅ (C) 09:15 RETIRED (Ali: "retire") the legacy stage1 / scalper / quick paper traders (outside the books, -$2,298 on 1,045 trades, why Compound sat at $0). `LEGACY_PAPER_ENABLED=false` by default; sniper entries unaffected; the 22 already-open legacy positions just run out. Compound now only trades from sniper confluence (≥3 confirmations). 903 tests pass.
- ✅ (A) 09:32 pushed f956065 (GitHub "run failed" emails stopped: smoke-test no longer runs on push, alert-replay no longer scheduled); 09:24 clean restart (retire + gate live).
- 🔴→🟡 (C) 09:40 GeckoTerminal MEASURED: the gate held calls to ≤24/min but 122 of 176 calls still got HTTP 429 → from Ali's IP only ~6 calls/min succeed (published 30/min is not what we get). Gate cap now 8/min (→6 effective), multichain poll 20 s → 45 s. Multichain sees fewer pools per minute until GeckoTerminal is replaced/complemented (candidates: DexScreener new-pairs, StonkFun API, Birdeye) — TO DO after tonight's data. Needs restart.
- 🟡 (C) **API BUDGET AUDIT** — built `api_budget.py` + hook in `utils/http.py`: every call logged per API in `logs\api_calls_<date>.log`; `python api_budget.py` prints calls today / projected day / busiest minute vs limit / month projection / 429 count. MadeOnSol confirmed 200/day (BASIC, 6,000/mo): 95-175/day used Oct 9-12, 27 so far today, existing guard keeps routine ≤190 — OK. GeckoTerminal 30/min shared by all pollers, only 1 HTTP 429 seen. Fomo credits exhausted (402, Ali top-up). STILL TO DO after restart: read a full day of `api_budget.py` output and add hard back-off where any API projects over its allowance.
- ✅ (C) TUNED multichain from the 3 h data: `MC_MAX_MCAP_USD` 1.5M → 60,000 (no chasing; every >$50k entry lost), BSC/Robinhood stakes ×0.3 (`MC_EVM_STAKE_MULT`) until they show wins. pump.fun early thresholds NOT touched yet (3 h is too small a sample; use `analyze_early.py` at 01:31 PKT Oct 14).
- ⬜ (B) Re-run `run_checks.bat` (backtest top/new + feed health) once the sniper has run a few hours; (C) reads logs\check_*.txt
- ⬜ (A) ~01:31 PKT Oct 14: `python analyze_early.py`, send output → (C) tune EARLY_*/MC_*/ROUTER_*, stakes, take-profit ladder, bank_pct
### Step 2 — Decide go/no-go per lane (Claude + Ali, from Step 1 data)
- ⬜ (C) Verdict per lane/chain: keep, cut or rework (no live on hope). Gate: positive paper P&L after the cost model, ≥50 entries in the lane
- ⬜ (C) Verify dev-list wallets (only 6/25 verified); decide use
- ⬜ (C) Book-driven stakes for copy books; realistic fills check
### Step 3 — Live on pump.fun (smallest first)
- ⬜ (A) PumpPortal API key + 0.02 SOL minimum (also enables per-coin trade feed → all lanes' buyer logic)
- ⬜ (A) `.env`: `PUMPPORTAL_API_KEY`, `EXECUTION_ENABLED=true`, `EARLY_LIVE_LANES=<one lane>`, `LIVE_MAX_STAKE_USD` small, `LIVE_DAILY_LOSS_USD` small; restart from Admin CMD
- ⬜ (B) First live buy+sell watched on dashboard; kill switch ready; review every trade for 48h vs paper; scale only after 30+ real closed trades
### Step 4 — Live on other chains (one at a time)
- ⬜ (A) Pick first chain (recommended: Four.meme/BSC → StonkFun → Base → Robinhood)
- ⬜ (A) Separate BURNER wallet per chain, key only in `.env` (never in chat); 1inch API key; free Helius key → `SOLANA_RPC_URL`
- ⬜ (C) Build adapter for that chain (paper until switched on) + tests; (B) $2–5 live test; then lane enable
### Step 5 — Run to targets
- ⬜ Core 1 Sprint → on $10K: dashboard "CASHED OUT" → (A) withdraw by hand, book reseeds $200; Core 2 ($125K/30d) and Core 3 ($1M–1.5M/180d) on their own seeds; weekly review of vault/stakes/pace
### Always-open Ali items
- ⬜ (A) Top up Fomo credits (HTTP 402) — Fomo books dead until then · ⬜ (A) Windows sleep = Never · ⬜ (A) free Helius key · ⬜ (A) send screenshots of 50x+ coins (name+chain) for calibration (12.13) · ⬜ (A) keep `EXECUTION_ENABLED=false` until Step 3

## 5. KNOWN ISSUES
- 🔴 Compound book $0.00 — lane refuses entries (fix in Step 1)
- 🟡 Multichain thresholds loose/untuned; only coins with a DEX pool are visible; ~20 newest pools per call (busy chains miss some)
- 🟡 Per-coin pump.fun trade feed OFF (needs key) → only dev-list entries at creation + launch-level signals until then
- 🟡 Paper fills optimistic vs real; Robinhood memecoin swap support unverified
- 🟡 API budgets not yet audited after adding multichain/StonkFun/non-Solana (see Step 1 API BUDGET AUDIT); 1 GeckoTerminal 429 seen at 01:33
- ✅ Fixed 13 Oct: restart script couldn't kill admin-level python (use Admin CMD); backtest unicode crash + glitch wicks

## 6. TEST / EVENT LOG (newest first)
- 01:55 Added API BUDGET AUDIT item (Ali): all free APIs must be paced to last a month.
- 01:50 Checklist rewritten as single master block (this).
- 01:33 Logs confirm early-watch + multichain running; Robinhood slug `robinhood`; entries on Solana/BSC/Robinhood/Base. 24h clock started 01:31.
- 01:31 Restart from Administrator CMD succeeded (3 old admin python processes killed).
- 01:07–01:27 run_checks.bat run 1/2: crashed/partial (unicode; glitch wicks) → fixed be02027; stopped on purpose.
- 00:46 Pushed multichain sniper (7c5a11f), run_checks.bat (153effd).
- 00:30 Pushed Core 1/2/3 naming + cash-out (e0886ca), backtest_sniper.py (783052d).
- Earlier history: see sections below (Phases 0–12) and HANDOFF.md.

## 7. HONEST RISKS
- Targets ($10K/96h = +216%/day, $125K/30d = +27%/day, $1M/180d = +5.2%/day) are what growth is REQUIRED, not shown performance. No proven edge yet; Sprint is lottery-like. Live stakes tiny, one lane first, only affordable money.
- A 24h run proves plumbing and a rough hit rate, not a durable edge.

---
# (HISTORY — older sections below are kept for reference; this block above overrides them)



_Created Sept 30 2026. Last reviewed Oct 9 2026 (no new work since Oct 6; see HANDOFF.md 0c). Every item has an OWNER and a DONE-WHEN. When an item
is finished it is ticked `[x]` in the SAME commit that finished it, with the
commit id. Nothing counts as done on "the code exists" alone — only on its
DONE-WHEN being met. Older checklists were removed Oct 9 2026 (still in git history); this file supersedes them._

Owners: **ALI** = needs you (money, keys, your PC, a decision) · **CLAUDE** = I build it · **BOTH** = I build, you run/confirm.

How "win rate" is measured here: a trade is a WIN if the position closes with
positive P&L **after fees and slippage**. Win rate = wins ÷ closed trades,
measured first on the paper ledger (Phase 2), then on real trades. The 80%
target is a number we measure against every week — not something assumed.

---

## ▶ OCT 12 2026 — WHAT'S LEFT TO MAKE S1c PROPER AND LIVE (supersedes the older "Score" line below)

**Truth first:** the system alerts, paper-trades and now scores demand + liquidity. It has NOT proven an edge (9.9 backtest = INSUFFICIENT DATA, needs 40+ coins of buyer data). Real money stays OFF until the gates in section C pass. 819 tests pass. GitHub main is up to date (`b2120fc`).

### A. ALI — only you can do these (in order)
- [ ] **A1 Restart local mode** — close the CMD running `start_local_mode.bat`, run it again. Loads the liquidity/mcap scoring + buyer-quality layer. DONE-WHEN: dashboard on http://localhost:8787 loads and System tab shows fresh heartbeats.
- [ ] **A2 Top up Fomo credits** at fomoapi.io (HTTP 402 = credits exhausted). Then `findstr /i "layer13 fomoapi" logs\local_poll-fast.log` shows no 402. Without this Stage 2 (Fomo copy-trade) and the Fomo tab are dead. (11.6/1.7)
- [ ] **A3 Keep the PC awake and the runner open.** Local mode only works while the PC is on and `start_local_mode.bat` is running. Set Windows sleep to "Never" while plugged in. If it sleeps, nothing is scanned.
- [ ] **A4 Let it collect data 3-7 days** with no changes. The buyer archive fills only while it runs. Then tell me "run the backtest" (or run `python backtest_buyer_signals.py`).
- [ ] **A5 (optional, recommended) Free Helius key** (helius.dev, free tier). Put it in `.env` as `SOLANA_RPC_URL=...`. Fixes RPC 403/rate limits for the buyer-funding lookups and wallet checks.
- [ ] **A6 Decide 10.2** — confirm `EXECUTION_ENABLED` is `false` in the PC `.env` (it is OFF now; keep it so).
- [ ] **A7 Create a NEW dedicated Solana wallet** (never your main Phantom) and fund it with a small amount you can afford to lose entirely. (6.1) Only after the section C gates pass.
- [ ] **A8 Put that wallet's key** in the PC `.env` as `EXECUTION_SOLANA_PRIVATE_KEY` (and GitHub Secret if the test-trade workflow is used). Never paste it in chat. (6.2)
- [ ] **A9 Set campaign env vars** (11.2): `SPRINT_MODE`, `MOONSHOT_ENABLED`, `COMPOUND_SEED_USD`, `TOTAL_WALLET_USD` — only when you decide to go live.
- [ ] **A10 Final switch** `EXECUTION_ENABLED=true` — only after C1-C4. (6.5)

### B. CLAUDE — I do these (no action from you unless noted)
- [ ] **B1 Run the 9.9 backtest** when A4 has 40+ coins. Verdict decides whether buyer signals are used for buying or stay information-only.
- [ ] **B2 Make the dev+sniper cap live** — pass `dev_pct` into the entry context (the guard exists but is dormant). Small change + tests.
- [ ] **B3 Decide on the verified dev wallets** — 6 VERIFIED, 17 exist/active, 2 unchecked (RPC errors). Add them to a deployer watch list only if the backtest shows it helps.
- [ ] **B4 Test-trade supervision (6.4)** — walk you through the $2 buy+sell and check the fills on Solscan and the dashboard.
- [ ] **B5 Daily trade review for the first 48 h live (6.6)** — compare every real trade to paper.
- [ ] **B6 (optional) Stricter rug filters** — LP lock threshold 50% -> 95% and Solana RugCheck integration. Only after measuring whether they remove losers on our data.
- [ ] **B7 Keep checklist, HANDOFF, Notion and GitHub in sync** after every change.

### C. GATES before real money (must ALL be true)
- [ ] **C1** Fomo credits topped up and Layer 13 returning data (A2).
- [ ] **C2** 9.9 backtest says SIGNAL SHOWS EDGE on the unseen half, OR we accept running without buyer signals and say so explicitly.
- [ ] **C3** The daily replay win rate (Phase 2 gate, `replay_results/`) meets the gate in Phase 2, not just "looks fine".
- [ ] **C4** $2 test trade passes: real buy AND sell confirmed on Solscan (6.4).
- [ ] **C5** First 48 h live at minimum size, every trade reviewed (6.6). Scale only after 30+ real closed trades (6.7).

### Change log (Oct 12)
- Dev-wallet verification run (25 wallets from the Oct 10 screenshot): 6 VERIFIED, 17 EXISTS_ONLY, 2 ERROR (RPC). PnL claims unverifiable; NOT added to scanner. Verifier fixed in `b2120fc`.
- Liquidity-to-market-cap rug check live in Layer 0 (9.10).

---

## ▶ WHERE WE STAND — read this first (updated Sept 30 2026, 20:15 PKT / 15:15 UTC)

**Code:** everything is merged to `main` (PRs #1–#11). Ali's PC is on `main` (pulled at `bd86c0c`; pull again for `restart_fast_watch.bat`). 736 automated tests pass.
**Real money:** OFF. No trading wallet key yet, and `EXECUTION_ENABLED` isn't set. The system alerts and paper-trades only.
**Score:** 44 items done, 25 open (list below). Open items needing Ali, in order: 6.1 wallet → 6.2 key secret → 6.4 $2 live test → (after tomorrow's replay) 6.5 `EXECUTION_ENABLED=true`.

**What's running live (all verified with heartbeats):**
| Runner | Where | How often | Notes |
|---|---|---|---|
| poll-fast | GitHub, via cron-job.org | 10 min | discovery on all 4 chains, scoring, Stage 1, moonshot screen, stall alerts, daily Telegram summary |
| poll-slow | GitHub | ~20 min | deep scoring, wallet layers |
| poll-madeonsol | Ali's PC (Task Scheduler) | 15 min | MadeOnSol layers, paced within 190 calls/day |
| fast-watch | Ali's PC (Startup folder) | 20 s with real money in a coin, otherwise 60 s | positions, scalper, paper ledger, revival watch. Restart with `restart_fast_watch.bat` after every `git pull` |
| alert-replay | GitHub | daily 06:07 UTC | win rate of the system's own alerts (7-day archive, alerts ≥ 4 h old) |

**Trading modes (what exists and whether it's on):**
| Mode | Status | Real money |
|---|---|---|
| Stage 1: band A/B buy + exit rules + 25% free runner | built, tested, paper-trading | when `EXECUTION_ENABLED=true`. Band B floor; band C never |
| Stage 2: Fomo roster convergence copy-trade + copy-exit | built, tested | same switch. Needs Fomo credits (monthly reset) |
| Compound scalper (aggressor) | built, tested, paper-trading | separate switch `COMPOUND_SCALPER_ENABLED` + manual pool start (7.5) |
| Moonshot (Layer 15): escape-velocity screen, half rides as runner | built, tested, alerts + paper | separate switch `MOONSHOT_ENABLED` (3.0d), $5/shot |
| Revival watch (Layer 14) | built, tested, live | feeds the scalper / Stage 1 |
| Chains | Solana, BSC, Robinhood: buy paths exist (only Solana has a wallet planned). Base: alerts + paper only, no buy path (7.3) | |

### Tests & evidence log — already done, DO NOT re-run to "check" again
| # | Test | When | Result | Where |
|---|---|---|---|---|
| T1 | Unit/integration suite | every commit | 736 pass | `tests/` |
| T2 | Live diagnostic (real Secrets, read-only) | Sept 30 12:24 & later | all runners fresh, orphans repaired, keys present | `diag-live.yml` |
| T3 | Test trade **dry-run** (no key, nothing sent) | Sept 30 13:50 UTC | passed: Jupiter quote + swap build OK | `test-trade.yml` |
| T4 | Labeled-coin trade backtest (Birdeye candles, 24 coins) | Sept 30 | stop −55% / lock 1.5x / trail 50% → 75% wins (79% with entry filter); became the default exits | `backtest_trades.py` |
| T5 | New loops make **zero** MadeOnSol calls | Sept 30 | verified at runtime (only DexScreener + GoPlus) | — |
| T6 | Upstash usage measured | Sept 30 | 26 cmds/min → cut to ~9/min idle (5.4) | heartbeat notes |
| T7 | Alert replay run 1 (`36725405507`) | 14:03 UTC | INVALID: GeckoTerminal 429 read as "no pool" → fixed | alert-replay |
| T8 | Alert replay run 2 (`36726979727`) | 14:32 UTC | **band B 4/5 wins (+58..+149%, all Base); band C 1/13** (−60..−66% rugs) → band C blocked from real money (incl. momentum) | alert-replay |
| T9 | Alert replay run 4 (`36730347993`) | 14:50 UTC | not conclusive: the feed held only ~2 h, so 71/82 trades were still open → 7-day archive + ≥4 h filter + daily schedule added | alert-replay |
| T11 | Live diagnostic (`36736431827`) after the PC restart | 15:24 UTC | all 4 runners fresh (fast-watch 2 min, new code); MadeOnSol 191/190 (resets 05:00 PKT); Fomo 0 credits. **Found 3 bugs, fixed:** (1) management tried to sell coins never bought (buy failed/execution off); (2) a 3,173x fake "gain" from an entry mcap in a different unit → entry sanity re-base (real + paper); (3) moonshot positions had no entry baseline, so their exits would never fire | diag-live |
| T12 | Ali's dashboard review (Sept 30 21:22 PKT) | — | **Paper scoreboard 8 closed / 0% / −49.5% was corrupted data:** entry prices came from GeckoTerminal (different mcap unit), and coin 0xcae117 showed −93% while its real candles were −5%. Pre-fix paper trades (opened before 15:40 UTC Sept 30) are now excluded from all win rates, kept for history. **9 "open positions" were never bought** (execution off) and blocked re-buys ("stage1 already fired") → auto-cleared after 1 h, no longer block. **Same coins re-alerted every 10 min** → repeat suppression (same band, score ±10, 60 min). Low-data band B now tagged "low data NN%". | dashboard |
| T13 | Paper scoreboard + diag, Oct 1 10:22 PKT | — | 189 closed paper trades: **scalper +$9,643 / trail-stop "+2,355%" wins were DATA GLITCHES** (DexScreener switching pair or marketCap↔FDV → fake 20x jumps) → `executor/price_sanity.py` ignores >4x jumps between reads (real + paper), stats restart from 05:45 UTC Oct 1. Real signal: **stage1 band B 7% wins (time stops: coins don't move in 90 min), momentum lane 16%, Robinhood 8%** → paper-record gate already blocks band B from real money; momentum lane now also blocked by its paper record. Fomo credits reset (241k) but a post-reset burst spent 8,625 (> 7,500/day) → candidate lookups cut 10→3/run. MadeOnSol 46/190, well paced. | dashboard + diag |
| T10 | Moonshot detector + runner exits | Sept 30 | 17 unit tests (gates, exit profile, handler, screen/alert/cooldown) | `tests/test_layer15_moonshot.py` |

**Pending evidence (automatic, don't trigger by hand):**
- Oct 1 06:07 UTC (11:07 PKT): daily alert replay → decides 2.5/2.8 and the go-live band.
- Paper ledger → dashboard Positions tab (by signal / chain / band / source incl. `moonshot`, `runner`).
- First Telegram daily summary → confirms 5.6.

**Superseded files removed Oct 9 2026** (`FINAL_CHECKLIST_2026-09-27.md`, `TASKS_LEFT_*.md`, `NEXT_STEPS.md`); recover from git history only if needed.

---

## Phase 0 — Already fixed (Sept 30, branch `claude/kind-mayer-1ydst8`)

- [x] Stage 2 copy-trade buy sat in the wrong loop and could never fire; large untracked buys crashed the cycle — `345d246`
- [x] Base alerts created phantom positions (no Base buy path) — now alert-only — `345d246`
- [x] Max-3-concurrent-positions rule was defined but never enforced — `345d246`
- [x] Band A/B score made mostly of "unknown" defaults could trigger a buy — now needs ≥50% real data — `345d246`
- [x] Two orphan Sept 24 BSC records blocked the whole $30 Stage 1 budget (every alert since Sept 24 refused) — self-repairing now — `e2341d9`
- [x] fomoapi.io credits drained (0/250,000) — credit governor, caching, 402 back-off — `e2341d9`
- [x] Fomo roster name matching, dedupe, paging, runs on GitHub too, feeds Stage 2 — `345d246`, `e2341d9`
- [x] New-trader insider check (free RPC) + auto-promotion rule — `e2341d9`
- [x] Base/BSC scoring: holder growth, activity-collapse, capped Birdeye crash check — `e2341d9`
- [x] Live diagnostic workflow using real Secrets (key presence only) — `504144e`
- [x] Runner heartbeats + dashboard Health panel + Auto-buy verdict column — `2ea4382`
- [x] Windows Task Scheduler installer for the PC job — `2ea4382`

---

## Phase 1 — Make today's fixes live

- [x] **1.1 ALI** — Approve merging `claude/kind-mayer-1ydst8` into `main` (say "open the PR"). DONE-WHEN: merged; cron-job.org runs include the fixes. — approved Sept 30
- [x] **1.2 CLAUDE** — Open the PR, keep CI green, merge on your OK. DONE-WHEN: PR merged. — PR #1 merged as `9053bd2`
- [x] **1.3 ALI** — On the PC: `git pull`, then double-click **`setup_pc.bat`** (does 1.3 + 1.4 safely: backs up .env, removes test lines, asks your wallet size, disables any duplicate old MadeOnSol task, registers the 15-min job). DONE-WHEN: System tab shows `poll-madeonsol … (local-pc)` under 30 min old. — done Sept 30: diagnostic 12:37 UTC shows `poll-madeonsol last cycle 2m ago (local-pc) madeonsol_calls=7`.
- [x] **1.4 ALI** — (done automatically by `setup_pc.bat`) Delete the `STAGE1_POSITION_USD=3`, `STAGE2_POSITION_USD=3`, `TOTAL_WALLET_USD=7` test lines from the PC `.env`. DONE-WHEN: lines gone. — done Sept 30 17:28 PKT via setup_pc.bat (backup `.env.backup-20260930-172801`, 3 test lines removed, TOTAL_WALLET_USD=100)
- [x] **1.5 ALI** — Add GitHub Secret `TOTAL_WALLET_USD` = real trading-wallet size in USD. DONE-WHEN: diagnostic shows it SET. NOTE: poll-fast/poll-slow did not pass this secret through until `TOTAL_WALLET_USD` was added to both workflows (Sept 30) — set it to 100 to match the PC. — done Sept 30: diagnostic 12:35 UTC shows stage1 $30 / budget $60 (= $100 wallet) on GitHub runs.
- [x] **1.6 BOTH** — Run "Live diagnostic (read-only)" after 1 hour. DONE-WHEN: all 3 runners fresh, orphan records repaired, band A/B alerts show an Auto-buy verdict. — done Sept 30 13:33 UTC: fast-watch, poll-fast, poll-slow, poll-madeonsol all fresh; orphans repaired; Auto-buy verdicts recorded on Solana/Robinhood/BSC/Base alerts.
- [ ] **1.7 ALI** — fomoapi.io: wait for the monthly reset or top up. DONE-WHEN: System tab shows credits > 0.

## Phase 2 — Measure before money (the win-rate engine)

The single biggest lever for 80%: stop guessing, measure every would-buy.

- [ ] **2.1 CLAUDE** — **Paper-trading ledger**: every WOULD-BUY (Stage 1, Stage 2, scalper) opens a paper position at the real quoted price incl. estimated slippage/fees, and runs the SAME exit logic as real money, with live prices. Separate from real positions, never blocks real budget. DONE-WHEN: paper positions open/close automatically and show on the dashboard. — BUILT `dd71d46`; tick once live on main and the first paper trades close
- [ ] **2.2 CLAUDE** — **Win-rate scoreboard** on the dashboard: win rate, average win, average loss, expectancy, max drawdown — broken down per signal (score band, deployer, convergence, Fomo roster, scalper), per chain, per trader. DONE-WHEN: visible and updating daily. — BUILT `dd71d46` (Positions tab); tick once it shows live data
- [x] **2.3 CLAUDE** — **Auto-disable losers**: any signal/trader/chain whose paper win rate is below target over ≥20 closed trades stops triggering real buys (still paper-traded so it can earn its way back). DONE-WHEN: rule live + unit-tested. — `dd71d46`: >=20 paper trades and win rate < MIN_SIGNAL_WIN_RATE (default 50%) or negative expectancy -> real money blocked, paper continues. Raise the bar toward 80% as data grows (2.5).
- [ ] **2.4 CLAUDE** — **Per-trader track record** for the 38 Fomo traders + promoted ones: copy only traders whose own copied-trade win rate is proven; demote the rest to alert-only. DONE-WHEN: ranking visible, gate enforced.
- [ ] **2.5 BOTH** — **Go/no-go gate**: real money only on signal types with paper win rate ≥ 80% (or the best achieved, with your explicit OK) over ≥ 30 closed paper trades. DONE-WHEN: gate numbers reviewed with you.
- [ ] **2.6 CLAUDE** — Grow the labeled backtest set from ~17 to 100+ real rugs / pump-dumps / moonshots and re-run weekly (current accuracy 54.5% on 11 tokens is too small to trust). DONE-WHEN: ≥100 labeled, result logged.

- [x] **2.7 CLAUDE** — **Fast trade backtest** on the labeled coins (Ali: no time to wait for paper results): real Birdeye candles, entry at +15/30/60 min, stage + scalper exits, costs, pessimistic candle order; point-in-time entry filter and exit-parameter grid. `backtest_trades.py`, workflow "Trade backtest" — `a4aeb14`, `2bb20fc`. First result (no entry filter, 24 coins): 33–42% win rate.
- [ ] **2.8 CLAUDE** — Backtest grid result: stop -55% / lock 1.5x / trail 50% = 75% wins (79% with entry filter, 15/19) vs 42% for the first settings. APPLIED as defaults (execution is off; paper ledger now runs them live). DONE-WHEN: 30+ closed paper trades confirm >= the backtest win rate; if not, re-tune. Scalper: stop 55% / TP 1.5x / 3 h / trail 35% = 71% wins over 72 simulated trades (was 32%) — applied `e653cd2`.

## Phase 3 — Entry quality (fewer, better buys)

- [x] **3.0 CLAUDE** — **Solana + Robinhood Chain discovery** (Ali: "why are only BSC/Base coins on the Alerts tab?"): free GeckoTerminal trending + new pools (>=10 min, >=$5k liquidity), GoPlus Solana security, free-RPC holder concentration, pump.fun pre-graduation bands, Robinhood via GeckoTerminal id `robinhood` — zero MadeOnSol calls — `78d6c42`, `e33cd92`. Live once PR #2 merges.
- [x] **3.0b CLAUDE** — **Layer 14 revival watch + momentum scalper** (Ali: "a coin can look like a rug at launch, then liquidity/traction comes -- track it and ride it like a scalper/sniper"): band C/D coins on every chain are re-checked every minute (fast watcher) or 10 min (GitHub) with one free DexScreener batch call per chain; liquidity added or momentum (1h +30%, buyers > sellers, volume) triggers a free re-score, alert, Stage 1 check and a compound-scalper MOMENTUM entry (band C allowed, hard red flags never) — `e2291f3`. Scalper retuned from the backtest: 71% wins vs 32% — `e653cd2`.
- [x] **3.1 CLAUDE** — **Confluence rule**: a real buy needs ≥2 independent layers agreeing (e.g. band A/B + tracked trader, or trusted deployer + momentum), not one signal alone. Tunable per signal from Phase 2 data. DONE-WHEN: enforced + tested. — `fbb7f0b`: executor/entry_guards.py — `REQUIRE_CONFLUENCE=true` makes every real buy need ≥2 independent signals (score + trusted deployer / wallet convergence). Built and tested; OFF by default until paper data says it raises the win rate (the scoreboard's `guard` column shows it).
- [x] **3.0c CLAUDE** — **Moonshot module (Layer 15)** (Ali: "coins do 1000x every week — how do WE catch one?"): screens every trending/new pool on all 4 chains for escape velocity (mcap $150k–$5M, 1–72 h old, +25% 1h AND +80% 6h, buyers ≥1.3× sellers, ≥150 trades/h, deep liquidity, not a vertical candle). Every qualifier → 🌙 Telegram alert + paper trade. Own exit profile: stake back at 2x, then HALF rides as a runner (only a −75% trail after 5x, rug, or the 10x/50x ladder sells it). Also fixed: the normal 50% trailing stop used to sell the moonbag too — now 25% of every winning position rides free. 16 tests.
- [ ] **3.0d ALI decide** — Turn moonshot real buys on: GitHub Secret `MOONSHOT_ENABLED=true`. Stake ladder (Ali, Oct 1: "$5 is useless, eaten by fees"): **$30 from the start, $40 once the A/B account is $300+, $60 at $700+ (~7x), then 8% of the account (max $2,000)**, never more than 35% of the account; one moonshot with stake at risk at a time under $300, two above (a free runner doesn't count). Override with `MOONSHOT_POSITION_USD`.
- [x] **3.0e CLAUDE** — **Robinhood Chain scoring** (Ali: "people are moving to this chain — find other factors"): RHC coins all scored ~50 with 15% real data (GoPlus doesn't cover RHC), so none could ever be bought. New market-structure score from real, free data: unique buyers in 24h, unique buyers vs sellers, 1h buy pressure, pool depth vs FDV, 1h/6h/24h trend, website + socials. RHC is scored on real data only; the missing security checks are replaced at buy time by an **on-chain sell-leg quote** (refuses unsellable coins and >25% round-trip loss). Also: Solana priority fee now capped at 0.5% of the trade (a $5 trade could otherwise pay ~8% in fees). 7 tests.
- [x] **3.0f CLAUDE** — **5-day sprint mode** (`SPRINT_MODE=true`, executor/sprint.py): the compound scalper with $100 seed, 90% of the pool per trade, the backtested exits unchanged (stop −55% / 60% sold at 1.5x / 35% trail / 3 h), and ONLY the highest-evidence entries: band B+ with ≥60% real data AND live momentum (5-min ≥ −5%, 1-h > 0), or a Layer 15 moonshot qualifier. Starts itself; 3 losses in a row → 90-min pause then auto-resume (stops for good below $25 or at −75%); 120 h / 400 trades. Status line in the daily Telegram summary. Also fixed: aggressor/moonshot/sprint switches were never passed to the GitHub runners. 6 tests.
- [x] **3.0h CLAUDE** — **Sprint momentum lane (Layer 16)** (Ali: "band A/B may not come in a day — go in a running coin, out in 15–20 min, multiple trades"): trending/new pools on all 4 chains re-priced every fast-watch tick (one DexScreener batch call per chain). Entry on a live buying burst: liquidity ≥ $30k and ≥ 4% of mcap, mcap $50k–$50M, ≥ 30 min old, 5-min +2..+25%, 1-h ≥ +8%, buys ≥ 1.3× sells, ≥ 100 trades/h, 1-h vol ≥ $20k. Exit "quick": 70% at +12%, 6% trail on the rest, −8% stop, 20-min limit. **Paper-trades every burst from tonight (dashboard source `momentum_lane`), real buys only in sprint mode**; pauses itself if its first 6 real exits win < 50%. Untested on history — the paper record is the test. 14 tests.
- [x] **3.0i CLAUDE** — **Sprint milestones (profit lock)** — Ali's plan: at $3,500 bank everything above $500; at $20,000 bank everything above $1,500; at $75,000 bank everything and stop (`SPRINT_MILESTONES`, default `3500:500,20000:1500,75000:0`). Banked money stays in the wallet but is never traded again (withdraw it to be fully safe); Telegram message at each milestone. **Dashboard: new "Modules" panel** on the Overview tab — Stage 1, Stage 2, Sprint/aggressor pool, Momentum lane, Moonshot — each with real money on/off, its own paper win rate and P&L, and status; scoreboard gains "By module". 5 tests.
- [ ] **3.0g ALI decide** — Start the sprint: GitHub Secrets `SPRINT_MODE=true` (+ `EXECUTION_ENABLED=true` after the $2 test) AND the same lines in the PC `.env`, then `restart_fast_watch.bat`.
- [x] **3.2 CLAUDE** — **Sell-ability check before every buy**: quote the SELL leg too (Jupiter/router) and refuse if it can't route, tax > 10%, or honeypot/freeze/Token-2022 transfer-hook risk (GoPlus + on-chain). DONE-WHEN: buys refuse unsellable tokens in tests. — `b88ca55`: executor/sellability.py runs before every real Solana/BSC buy (Jupiter sell-leg quote, round-trip loss cap 25%, GoPlus Token-2022 flags; GoPlus honeypot/cannot-sell-all/tax >10% on BSC). 13 tests.
- [x] **3.3 CLAUDE** — **Liquidity & slippage floor**: refuse if our size is > 2% of pool liquidity or quoted price impact > 3%. DONE-WHEN: enforced + tested. — `fbb7f0b` + `f9b197d`: pool ≥ $10k and our buy ≤ 2% of the pool (all chains), Jupiter quoted price impact ≤ 3% (Solana), BSC/RHC slippage cut to 2%/3%.
- [x] **3.4 CLAUDE** — **Late-entry guard**: refuse if price already up > X% in the last 5–15 min (don't buy the top); X tuned from paper data. DONE-WHEN: enforced + tested. — `fbb7f0b`: refuses after +50% in 5 min, or +300% in 1 h for non-momentum entries (env-tunable GUARD_MAX_M5_PCT / GUARD_MAX_H1_PCT).
- [x] **3.5 CLAUDE** — **Sniper/bundle & dev-sell guard**: refuse if top snipers/bundlers hold > 25% or the developer has started selling. DONE-WHEN: enforced + tested. — `fbb7f0b`: refuses when snipers/bundlers hold ≥ 25% (when known). Dev-selling is covered by Layer 6's defensive sell on held positions.
- [x] **3.6 CLAUDE** — **Promote stricter A-band for real money**: until Phase 2 proves otherwise, only band A (or B + confluence) buys real; B alone paper-trades. DONE-WHEN: config + tests. — `fbb7f0b`: real money needs band B or better on a score-only buy (`REAL_MONEY_MIN_BAND`, momentum scalper C); trusted-deployer / convergence fires keep their own rules.

## Phase 4 — Exit discipline (this is where win rate is made)

- [x] **4.1 CLAUDE** — **Hard stop-loss** for Stage 1/2 positions (e.g. −25% from entry, tunable). Today only the scalper has one — a slow bleed with no rug signal is held forever. DONE-WHEN: enforced + tested. — `c243c97`: -25% stop (STOP_LOSS_PCT), tests in tests/test_exit_rules.py
- [x] **4.2 CLAUDE** — **Breakeven lock**: at +40–60% sell the slice that recovers the full stake + fees, then the rest rides risk-free (turns most green trades into locked wins). DONE-WHEN: enforced + tested. — `c243c97`: at 1.5x sells just enough to recover stake + costs; moonbag rungs rescale to what rides
- [x] **4.3 CLAUDE** — **Trailing stop** on the remainder (e.g. give back max 30–40% from peak), keeping the existing 3x/10x/50x moonbag ladder and dollar targets. DONE-WHEN: enforced + tested. — `c243c97`: arms at 2x, exits 35% off peak (TRAIL_ARM_MULT / TRAIL_GIVEBACK_PCT)
- [x] **4.4 CLAUDE** — **Time-stop**: exit a position that hasn't moved +X% within N minutes (dead coins tie up budget). DONE-WHEN: enforced + tested. — `c243c97`: 90 min without reaching 1.2x (TIME_STOP_MINUTES / TIME_STOP_MIN_MULT)
- [x] **4.5 CLAUDE** — **Tracked-trader exit mirror**: when the Fomo trader(s) we copied sell, we sell (Layer 9 alerts already detect this — wire it to execution). DONE-WHEN: enforced + tested. — `01ecf8c`: executor/copy_exit.py — a roster trader's SELL alert on a coin they bought and we hold sells our remaining tokens. 5 tests.

### Bugs found & fixed while building Phases 2/4 (Sept 30)
- [x] close_position P&L ignored earlier partial-sell proceeds -> winning trades recorded as losses — `c243c97`
- [x] defensive_sell (rug exit) tried to sell 100% of original tokens after trims -> would fail on-chain mid-rug — `c243c97`
- [x] MadeOnSol wallet-feed calls (Layers 2+9) were never counted or gated -> real usage above the tracked number — `ae43b4a`

## Phase 5 — Speed & reliability (don't lose trades to plumbing)

- [x] **5.1 CLAUDE** — **Fast position watcher on the PC**: checks open positions every 5–10 s for stop/target/rug exits (today: every 10 min on GitHub — far too slow for memecoins). DONE-WHEN: runs under Task Scheduler, heartbeat on dashboard. — BUILT `f3b36f9` (worker_fast_watch.py, 20 s; registered by setup_pc.bat). Tick when the dashboard shows `fast-watch … local-pc`. — live Sept 30: `fast-watch last cycle 1m ago (local-pc)`. Auto-start at login via Startup folder (PR #4, no admin).
- [x] **5.2 CLAUDE** — **Solana priority fee** (and Jito tip option) on buys/sells so they land during congestion — currently none is set. DONE-WHEN: in swap path + tested. — `b88ca55`: capped priority fee (SOLANA_PRIORITY_MAX_LAMPORTS, default 0.001 SOL, level veryHigh) + dynamic compute units on every Jupiter buy and sell.
- [x] **5.3 CLAUDE** — **Buy/sell retry with fresh quote** (1 retry, re-checked slippage) on transient failures. DONE-WHEN: tested. — `60b4ea7`: executor/trade_ops.py — one retry with a fresh quote on pre-send failures only (quote/build/price/RPC busy); never after a tx was sent, never on safety refusals. Tested.
- [x] **5.4 CLAUDE** — **Upstash usage check**: measure commands/day vs free-tier limit; batch/cache further if near it. DONE-WHEN: number logged, headroom ≥ 2x. — `54a2ead`: measured 26 commands/min from the fast watcher (~1.1M/month, over the free tier). Now 60 s ticks when nothing real is open (20 s only with real money in a coin), dashboard refresh 30 s; every runner's heartbeat reports its command count. ~9/min idle ≈ 390k/month.
- [x] **5.5 CLAUDE** — **Telegram alert on every real trade + every failed trade + stalled runner** (dashboard already shows it; phone alert so nothing is missed). DONE-WHEN: tested message received. — `60b4ea7`: every real buy/sell (ok or failed) → Telegram with tx; a runner silent too long → one alert per 2 h. Uses the existing TELEGRAM_BOT_TOKEN/CHAT_ID secrets.
- [x] **5.6 CLAUDE** — **Daily summary**: trades, win rate, P&L, API credits left — to Telegram once a day. DONE-WHEN: first one received. — `60b4ea7`: once per UTC day from poll-fast: paper win rate, real trades in/out, MadeOnSol usage, top signals. First one arrives on the first poll-fast run after merge.
- [ ] **5.7 CLAUDE** — **MadeOnSol budget pacing**: at 12:37 UTC 160 of 190 daily calls were already used (one PC cycle ≈ 7 calls; every 15 min ≈ 670/day, so the budget runs out by mid-day and those layers go blind until 5 AM PKT). Spread the 190 calls across 24 h (per-hour allowance, priority to real candidates over routine scans) and show calls-left on the System tab. DONE-WHEN: budget lasts the full day in the diagnostic. — BUILT `ae43b4a`; tick when a full UTC day passes with budget left in the diagnostic

## Phase 6 — First real money (Solana only)

- [ ] **6.1 ALI** — Create a NEW dedicated Solana wallet (never your main Phantom), fund it with a small amount. DONE-WHEN: funded.
- [ ] **6.2 ALI** — Add GitHub Secret `EXECUTION_SOLANA_PRIVATE_KEY` (that wallet only) + same line in the PC `.env`. DONE-WHEN: diagnostic shows SET.
- [x] **6.3 CLAUDE** — Build a one-click **"Test trade" workflow**: buys ~$2 of a liquid token, sells it back, prints both tx links — works even while `EXECUTION_ENABLED` is off for the scheduler. DONE-WHEN: workflow exists + tested with mocks. — `b88ca55`: test_trade.py + workflow "Test trade (Solana)". Dry run PASSED live on GitHub 13:50 UTC (SOL price, Jupiter buy quote, sell route found, priority fees set).
- [ ] **6.4 BOTH** — Run the test trade. DONE-WHEN: real buy AND sell confirmed on Solscan, fills recorded correctly on the dashboard.
- [ ] **6.5 ALI** — Set Secret `EXECUTION_ENABLED=true` (only after 6.4 and the Phase 2 gate). DONE-WHEN: set.
- [ ] **6.6 BOTH** — First 48 h live at minimum size, daily review of every trade vs paper. DONE-WHEN: reviewed, no unexplained trade.
- [ ] **6.7 BOTH** — Scale position size only after 30+ real closed trades hit the win-rate gate. DONE-WHEN: decision logged.

## Phase 7 — Other chains & modules

- [ ] **7.1 ALI** — BSC: fund a new BNB wallet, add `EXECUTION_BSC_PRIVATE_KEY`; then **BOTH** run the test trade on BSC.
- [ ] **7.2 CLAUDE** — BSC: add PancakeSwap V3 routing + four.meme bonding-curve buys (today only V2-listed coins can be bought).
- [ ] **7.3 ALI decide** — Base: build a buy path (Uniswap/Aerodrome) or keep alert-only. Base is the chain with the most alerts today.
- [ ] **7.4 ALI** — Robinhood Chain: create/fund wallet, add `EXECUTION_RHC_PRIVATE_KEY`; **BOTH** run test trade. **CLAUDE**: find a 2nd RPC endpoint (only one, rate-limited, today).
- [ ] **7.5 ALI decide** — Compound scalper on/off (`COMPOUND_SCALPER_ENABLED=true` + manual pool start) — only after its paper win rate is proven in Phase 2.
- [ ] **7.6 ALI decide** — StonkFun snipe worker: run it on the PC (fast 8-second loop) once Solana is live.
- [ ] **7.7 ALI** — Layer 12 Telegram caller channels: add `TELEGRAM_CALLER_BOT_TOKEN` + `TELEGRAM_CALLER_CHANNEL_IDS` if you want it.

## Phase 8 — Data sources (only if Phase 2 shows they'd pay for themselves)

- [ ] **8.1 ALI decide** — fomoapi.io paid plan vs staying on the free budget (free = alerts every ~30 min).
- [ ] **8.2 ALI decide** — MadeOnSol PRO (~$45/mo): more calls + holder/bundle data (only MadeOnSol-only feeds: developer alerts, tracked-wallet feed).
- [ ] **8.3 ALI decide** — Helius or similar paid Solana RPC (~$50–100/mo): faster, reliable fills and a real-time wallet feed.

---

### Change log
- Sept 30 2026 13:33 UTC — PR #4 merged (fast watcher auto-start without admin). Solana coverage fix: top-10 holders from GoPlus (RPC fails on GitHub).
- Sept 30 2026 — PR #2 merged (`62f3447`): win-rate engine, exit rules, MadeOnSol pacing, Solana/Robinhood discovery, trade backtest. PR #3 merged (`811fdfd`): Layer 14 revival + momentum scalper, 20 s fast watcher, backtested scalper settings, batched paper pricing. New loops verified to make zero MadeOnSol calls.
- Sept 30 2026 — checklist created; Phase 0 filled from today's commits.
- Sept 30 2026 — 1.1/1.2 done: PR #1 merged into main (`9053bd2`).
- Sept 30 2026 12:24 UTC — live diagnostic on main: poll-fast + poll-slow running the new code (heartbeats 3-4 min old); the 2 Sept-24 orphan records auto-repaired (budget freed); first Auto-buy verdict recorded. NOT yet: no `poll-madeonsol` heartbeat (PC task is the old Sept 28 one, running pre-merge code -> 1.3/1.4 still open); `TOTAL_WALLET_USD` secret missing (1.5); fomoapi.io still 0 credits, governor backing off correctly (1.7).
- Sept 30 2026 17:28 PKT — PC: stash of old local layer13 edits (`pc-local-edits-before-sept30`), pull to 4fe41d0, setup_pc.bat OK, task re-registered; 1.4 done; 1.3 waiting on first `poll-madeonsol` heartbeat.
- Sept 30 2026 12:35 UTC — 1.5 done. No PC heartbeat yet 7 min after the 17:28 PKT run; PC log was buffered (only header visible) -> run_poll_madeonsol.bat now runs `python -u` and writes a `finished ... exit code` line.
- Sept 30 2026 12:37 UTC — 1.3 done (first PC heartbeat). Found: MadeOnSol 160/190 used by mid-day -> added 5.7.
- Sept 30 2026 14:05–15:15 UTC — PR #7 (entry guards 3.1/3.3–3.6, retry 5.3, Telegram 5.5/5.6, copy-exit 4.5, Upstash 5.4), PR #8 (band B floor for momentum, from replay T8), PR #9 (moonshot Layer 15 + free runner; fixed: the 50% trail used to sell the moonbag), PR #10 (7-day replay archive + daily replay), PR #11 (`restart_fast_watch.bat`: a second copy had locked the log). All merged.
- Sept 30 2026 20:15 PKT — PC pulled `bd86c0c`. Next: `git pull` + `restart_fast_watch.bat`; then 6.1 wallet, 6.2 key, 6.4 $2 test.
- Sept 30 2026 20:30 PKT — PC pulled `882dd10`, `restart_fast_watch.bat` OK (one watcher). T11 diagnostic → 3 pre-live bugs fixed (see T11).
- Oct 1 2026 — PR #14: dashboard-review fixes (T12). Paper win rate restarts clean from 15:40 UTC Sept 30.
- Oct 1 2026 — PR #15: Robinhood Chain market-structure scoring + on-chain sell-leg check, size-scaled Solana priority fee (3.0e).
- Oct 1 2026 — PR #16: moonshot stake ladder $30 → $40 → $60 → 8% of account (was a flat $5).
- Oct 1 2026 — PR #17: 5-day sprint mode (3.0f); workflow env now carries SPRINT_MODE / COMPOUND_* / MOONSHOT_* / REQUIRE_CONFLUENCE.
- Oct 1 2026 — PR #19: sprint momentum lane (3.0h); paper evidence starts now.
- Oct 1 2026 — PR #20: sprint milestones + dashboard Modules panel (3.0i).
- Oct 1 2026 — PR #23: price-glitch filter, paper stats reset, lane gated by paper record, Fomo candidate lookups 10→3 (T13).

---

## Phase 9 — Finding a REAL edge (Oct 1 2026)

Honest basis: the rule search (T13+) showed all 560 entry/exit rules LOSE on
the coins the system alerts on. The problem is WHICH coins get picked, not
the exits. The system filters for SAFETY (won't rug) when it should filter
for DEMAND (who is buying). The only early signal that separates the ~1
pump.fun survivor from the ~99 that die is "a proven smart wallet is buying
it" — the same mechanism Fomo copy-traders use, applied at launch. This
phase is the plan to get that signal. NONE of it is a path to a 24-hour
miracle; it is the honest path to a system with a real chance over time.

### 9A — Signals that actually predict a pump (ranked)
- [ ] **9.1 CLAUDE** — **Smart-wallet-buying signal (THE signal).** Build/curate a
      list of wallets that repeatedly bought early into coins that graduated,
      and alert/buy when ≥2 of them hit a fresh coin. Per-platform (Fomo list
      ≠ pump.fun list ≠ StonkFun list). DONE-WHEN: list exists, back-tested, wired.
      **STATUS (Oct 9): CODE BUILT + TESTED, NOT PROVEN. layers/layer15_buyer_quality.smart_wallet_hits (>=2 roster wallets). Roster = 13 pump.fun wallets today. Not back-tested (see 9.9), so stays open. Per-platform lists for Fomo/StonkFun still to do.**
- [ ] **9.2 CLAUDE** — **Distinct-buyer velocity.** Count UNIQUE buyer wallets in
      the first 2–5 min and whether the rate is accelerating — real demand, not
      total volume. DONE-WHEN: computed live + in the score.
      **STATUS (Oct 9): CODE BUILT + TESTED. buyer_velocity (unique buyers, 1st vs 2nd half of 5 min) feeds the demand score; computed live from the buyer archive. Not proven to predict profit.**
- [ ] **9.3 CLAUDE** — **Anti-bot / fake-traction filter (Ali's point).** A dev can
      run many wallets to fake "many buyers". Overcome it by FUNDING-SOURCE
      clustering: trace each buyer's funding wallet (Layer 10 already does
      first-funder). Many independent funders (CEX withdrawals, varied
      sources) = organic; all tracing to 1–2 funders, brand-new wallets,
      identical buy sizes/timing = one bot farm → REJECT. DONE-WHEN: distinct-
      funder count + new-wallet ratio in the score, tested on known bot rugs.
      **STATUS (Oct 9): CODE BUILT + TESTED on synthetic bot-farm / organic cases. Uses Layer 10 first-funder (cached, 8 fresh RPC lookups/call). NOT yet tested on real known bot rugs; thresholds are first-pass guesses. A bot_farm verdict BLOCKS the buy in executor/entry_guards.**
- [ ] **9.4 CLAUDE** — **Dev/sniper concentration cap.** Reject when dev + first
      bundles hold > ~20–30% (built to dump). DONE-WHEN: enforced + tested.
      **STATUS (Oct 9): CODE BUILT + TESTED. entry_guards blocks when dev% + sniper% >= GUARD_MAX_DEV_SNIPER_PCT (0.30). Only active when both values are in the entry ctx; the scheduler does not pass dev% into ctx yet, so today only the older sniper-only guard (3.5) is live.**
- [ ] **9.5 CLAUDE** — **Deployer track record** (have it, Layer 1) — weight it more.
      **STATUS (Oct 9): CODE BUILT + TESTED. deployer_weight adds/subtracts demand-score points from state.get_deployer_reputation.**
- [ ] **9.6 ALI decide** — **Narrative / attention (X, call channels).** Memecoins
      run on attention; we have almost none. Needs a social data source. Big
      lift, deferred.

### 9B — Data tier (the real question: can we even SEE 9.1–9.3 in time?)
- [ ] **9.7 ALI decide** — **Real-time data: Helius vs MadeOnSol PRO.**
      - **Helius (~$50/mo, has a FREE tier to start):** real-time Solana
        transaction STREAM (sub-second). This is the unlock for 9.1–9.3 —
        see a launch and which wallets buy it the moment it happens, not 10
        min later. Solana ONLY (covers pump.fun; NOT StonkFun/Robinhood).
      - **MadeOnSol PRO (~$45/mo):** more holder/bundle data but POLLING, not
        streaming — helps 9.3/9.4, does NOT give real-time 9.1. Solana only.
      - **Verdict: neither ALONE is enough.** Helius gives the DATA; you still
        need the curated wallet list (9.1) to act on it. Recommended order:
        start on Helius FREE tier + build the wallet list from the Fomo
        copy-trade archive; pay the ~$50 only once the list proves out.
      - **Least capital:** $0 to start (Helius free tier + our existing
        MadeOnSol 190/day), ~$50/mo only after 9.1 shows a real edge.
- [ ] **9.8 ALI decide** — **StonkFun / Robinhood Chain:** Helius & MadeOnSol are
      SOLANA-only, so they do NOT cover StonkFun. StonkFun needs its own
      Robinhood-chain RPC/websocket or indexer for the same 9.1–9.3 signals.
      Separate build; defer until the Solana path is proven.

### 9D — Rug-check filters from the Oct 12 review (Google AI rug-pull checklist)
- [x] **9.10 CLAUDE** — Mapped each check to what S1c does. LP burned/locked: already scored (layer0, LP lock = >=50% of LP locked/burned; the guide's stricter 95% is NOT enforced). Honeypot/mint/freeze: already scored via GoPlus/MadeOnSol (Solana RugCheck not integrated). Dev-funded wash trading / one master funder: Layer 15 (9.3), pump.fun archive only. **Liquidity-to-market-cap ratio: was MISSING, added Oct 12** (`layer0_scoring.liq_mcap_check`): liquidity < 1% of mcap (mcap >= $250k) forces band D; < 2% (mcap >= $1M) demotes band A to B; skipped when data is unknown and on pump.fun curve coins. Thresholds are a rule of thumb, NOT measured on our data; env: LIQ_MCAP_MIN_MCAP / LIQ_MCAP_KILL_RATIO / LIQ_MCAP_CAP_RATIO. 4 new tests, 819 pass.

### 9C — Prove it before a cent
- [ ] **9.9 CLAUDE** — Back-test 9.1–9.4 on the launch archive (`--source
      launches`) and the Fomo archive (`--source fomo`) before any real money.
      DONE-WHEN: a signal set shows positive expectancy on the UNSEEN half.

Reality check (not negotiable): even fully built, this competes with bots on
faster machines; it improves odds, it does not guarantee wins or 80%, and it
is a build of days–weeks, not a 24-hour fix.

---

## Phase 10 — Oct 5 2026 incident: Upstash free tier exhausted (open items)

Cause: the free DB hit 500K commands/month (908K reads); Upstash then rejects every call (HTTP 400), reads look empty, dashboard blank, nothing recorded. Code fix shipped in `f741699` (read cache, quota guard, buy refusal while blocked, throttled cycles, lighter dashboard). These remain:

      **STATUS (Oct 9): TOOL BUILT (backtest_buyer_signals.py), RESULT: INSUFFICIENT DATA. The old launch archive (202 rows) has no buyers/outcomes, so nothing can be tested yet. The pump.fun poller now archives buyers (state key buyer_archive); rerun after a few days. Needs 40+ coins; judges only the unseen half.**
- [ ] **10.1 ALI** — Restore the data store: create a NEW free Upstash DB (or upgrade); put its REST URL + token in the PC `.env` (`UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`) AND the two GitHub Secrets of the same names; run `update.bat`. DONE-WHEN: dashboard System tab shows all 4 runners fresh and no "DATA STORE BLOCKED" banner. If Upstash refuses a 2nd free DB -> **CLAUDE** builds the local-file mode (discovery on the PC, no Upstash).
- [ ] **10.2 ALI** — Real-money switch: PC `.env` showed `EXECUTION_ENABLED=true` + `EXECUTION_SOLANA_PRIVATE_KEY` set, contradicting "real money OFF". Confirm it was deliberate and tell Claude the wallet balance, or set `EXECUTION_ENABLED=false` and run `restart_fast_watch.bat`. DONE-WHEN: decision logged here.
- [ ] **10.3 BOTH** — Run the two never-run replays: GitHub Actions -> "Alert replay" -> source `fomo`, then source `launches` (daily scheduled replay already runs; Oct 1–4 succeeded but Claude cannot read the log). DONE-WHEN: results pasted/saved and Phase 9 direction chosen from them.
- [x] **10.4 CLAUDE** — Make the replay workflow commit its results to the repo (e.g. `replay_results/latest.md`) so any session can read them without log access. DONE-WHEN: file updates after a run.
- [ ] **10.5 CLAUDE** — After 10.1: read each runner's REAL commands/min from the heartbeat notes, find the per-coin consumers (mc/holder history writes, pump.fun roster get/set, Fomo + launch archives), and tune cadences/batching to stay under ~16K commands/day. DONE-WHEN: a full day shows < 16K in the Upstash Usage tab.
  - Oct 5 measured (offline, counting fake store): empty poll-fast cycle = 14 commands, poll-slow = 9, poll-madeonsol = 10 -- fixed overhead is NOT the problem. Per-coin cost (mc_history/holder_history get+set, pump.fun roster loop, archives) needs real traffic: after 10.1, read the new 'Database commands today' line (dashboard System tab / Telegram summary) for 1-2 days, then trim the top consumer. Not done blind.
- [x] **10.6 CLAUDE** — Add a daily command-usage line to the Telegram summary + dashboard System tab (commands today vs 16K/day budget) so the cap is never a surprise again. DONE-WHEN: visible and tested.
- [ ] **10.7 ALI (optional)** — Check the Upstash Usage tab for the monthly reset date. If the free DB is still blocked, old data returns on reset.

### Change log (Oct 5)
- Oct 5 2026 — `f741699`: Upstash quota fix (790 tests). Cause + measurements in HANDOFF.md §0b.

## Phase 11 -- Two-target setup (Oct 6 2026)
- [x] **11.1 CLAUDE** — Sprint retargeted to $90k in 168 h (finishes when banked + pool >= `SPRINT_TARGET_USD`; locks 3500:1500, 20000:5000). Marathon tracker for $1M over `MARATHON_DAYS` (default 150) with path/pace in the daily summary + dashboard. `SPRINT_ONLY=false` in runner workflows so both run. See TARGETS.md. 795 tests.
- [ ] **11.2 ALI** — Put `SPRINT_MODE=true`, `MOONSHOT_ENABLED=true`, `SPRINT_ONLY=false`, `COMPOUND_SEED_USD=100`, `TOTAL_WALLET_USD=100` in PC `.env` and the GitHub Secrets (only after 10.1/10.2; real money stays off until you decide).
- [x] **11.3 CLAUDE** — **Local mode (no Upstash)**: state.py local backend made multi-process safe (file lock, atomic writes, change-aware read cache, private copies, real cross-process locks); `local_runner.py` + `start_local_mode.bat` run fast watcher + poll-fast/slow/madeonsol + dashboard on the PC; GitHub runners skip when no Upstash state. 802 tests incl. a 4-process write test. See LOCAL_MODE.md.
- [x] **11.4 ALI** — Local mode switched on (Oct 6, ~9:20 AM PKT): Upstash lines blanked in `.env`, old Task Scheduler jobs disabled, `start_local_mode.bat` run from a fresh clone/pull of the repo. Dashboard confirmed on the local file: fast-watch heartbeat live (`local-pc`), DB commands 0/16,000, Marathon tracking $100 day 0/150. Waiting on first poll-fast / poll-slow / poll-madeonsol heartbeats (~10-15 min after start) -- not yet confirmed.
- [ ] **11.5 ALI+CLAUDE** — Confirm the three poll runners show recent times on the dashboard System tab (if still "never" after ~20 min: `type logs\local_poll-fast.log`). Then decide 10.2 (`EXECUTION_ENABLED`) and 11.2 (campaign env vars: `SPRINT_MODE`, `MOONSHOT_ENABLED`, `COMPOUND_SEED_USD=100`, `TOTAL_WALLET_USD=100`).
- [ ] **11.6 ALI** — Fomo tab empty (Layer 13). DNS PART DONE Oct 9: Wi-Fi DNS set to 1.1.1.1/8.8.8.8 (`netsh`; the PowerShell in the first fix_dns.bat was broken and is corrected), log shows no more NameResolutionError. REMAINING: Fomo API returns `HTTP 402` (credits exhausted) on /v2/alerts and /v2/leaderboard/7d. Top up fomoapi.io credits, restart `start_local_mode.bat`, re-check with `findstr /i "layer13 fomoapi" logs\local_poll-fast.log`. Not a code bug.

## Phase 12 -- Catalyst Radar: finding the moonshot BEFORE the crowd (Oct 12 2026)
Basis: MOONSHOT_RESEARCH.md (PONS, STONK, AMC, MARS: each had a public, dated catalyst; hypotheses, untested).
- [ ] **12.1 CLAUDE** -- Listing-ladder radar: watch Binance Alpha/futures/spot announcements, alert + paper-trade the next step. DONE-WHEN: tested with mocks, runs in local mode.
- [ ] **12.2 CLAUDE** -- News-jack radar: viral brand/person news -> coins launched in the next hours with matching names -> scored with Layer 15. DONE-WHEN: tested + paper-running.
- [ ] **12.3 CLAUDE** -- Launchpad/chain radar: new launchpads/chains, fee growth, buyback ratio vs mcap. DONE-WHEN: tested + paper-running.
- [ ] **12.4 CLAUDE** -- Narrative-leader map (rank first/biggest token of a hot sector, ignore clones).
- [ ] **12.5 ALI+CLAUDE** -- Forensics of the 4 winners (first-24h buyers) to check what Layer 15 would have flagged. NEEDS a free Helius key from Ali (A5).
- [ ] **12.6 CLAUDE** -- Point-in-time backtest of every radar incl. the catalysts that did NOT run. No radar goes live without positive results on unseen data.
- [ ] **12.8 CLAUDE** -- Regime gauge: daily market-temperature from our own archive (launch count, graduation rate, profit share); size up only in hot regimes, paper/tiny in cold ones.
- [ ] **12.9 CLAUDE** -- Buyback-set radar (StonkFun-style top-15 buyback inclusion; verify the claim first) and reward-token watch.
- [ ] **12.10 CLAUDE** -- Conviction-money test: concentrated LARGE buys by tracked wallets in sub-$5M coins vs many small buys (uses Fomo roster + buyer archive).
- [ ] **12.11 CLAUDE** -- Cross-chain narrative-lag watch (story starts on one chain, copies appear on another within hours).
- [ ] **12.12 BOTH** -- TEST the conflict: does the Layer 15 bot-farm block remove winners? (82.8% of >100% coins reportedly show artificial growth vs MemeTrans loss reduction.) Decide on the 9.9 backtest.
- [ ] **12.13 ALI** -- Send screenshots of the DexScreener/Fomo lists of coins that ran 50x+ in the last 30-60 days (name + chain) so the forensic script has a real winners list. My shell cannot reach those APIs.
- [ ] **12.7 ALI** -- Core 1 ($100 -> ~$1.5M) and Core 2 ($100 -> $125k in 1-2 months) stay on paper until 12.6 passes. Maths: 1,250x and 15,000x; see MOONSHOT_RESEARCH.md.

## OCT 12 2026 (late) -- LAYER 16 CATALYST RADAR BUILT + WIRED (Claude side)
- DONE: 12.1 listing ladder, 12.2 news-jack, 12.3 launchpad surges, 12.4 narrative leader/clone, 12.8 regime gauge (off by default: REGIME_GATE_ENABLED), 12.9 buyback proximity (optional buyback_sets.json), 12.10 conviction wallets, 12.11 cross-chain lag. All alert + PAPER only, never real orders.
- WIRED: scheduler.py --poll-radar, local_runner radar thread every 3 min (LOCAL_RUNNER_RADAR=false to disable), dashboard System tab "poll-radar", daily alert cap 40.
- B2 FIXED: entry guards now receive real liquidity, sniper % and dev % on the MadeOnSol Solana path.
- Tests: 846 passed. Restart start_local_mode.bat to activate.
- STILL OPEN: 12.5 forensics (needs Helius key), 12.6 backtest (needs 3-7 days data), 12.12, 12.13 (Ali screenshots), 12.7 Cores stay paper.

## ACTIONABLE ITEMS LEFT (as of Oct 12 2026 evening; radar pushed at 83dfe07)
ALI:
- [ ] A1 Restart start_local_mode.bat; confirm "poll-radar" fresh on dashboard System tab (~5 min)
- [ ] A2 Top up Fomo credits at fomoapi.io (HTTP 402) -- Layer 13 empty until then
- [ ] A3 Windows sleep = Never
- [ ] A5 Free Helius key -> SOLANA_RPC_URL in .env (unlocks 12.5 forensics)
- [ ] A6 Keep EXECUTION_ENABLED=false
- [ ] A4 After 3-7 days of data: python backtest_buyer_signals.py, send the output (9.9 / 12.6)
- [ ] 12.13 Screenshots of 50x+ coins (name + chain) from DexScreener/Fomo
- [ ] A7-A10 wallet / keys / final live switch -- only after a backtested pattern exists
CLAUDE:
- [ ] 12.5 on-chain forensics of winners (after Helius key + screenshots)
- [ ] 12.6 point-in-time backtest incl. radar signals (after data)
- [ ] 12.12 bot-farm block vs engineered-winners conflict test
- [ ] Review first live radar alerts, tune thresholds; create buyback_sets.json
- [ ] Notion banner updated Oct 12 (done)

## OCT 12 2026 NIGHT -- MODULE SYSTEM (Claude side, DONE)
- DONE: 10 modules (Moonshot Core 1, Compound Core 2, Radar, Leaderboard copy, Fomo followers, Fomo new wallets, Dev launch, Dev intel, Own analysis, Context). Telegram label on every alert, module on every paper trade, dashboard Modules tab, Module column on Alerts tab. See MODULES.md.
- OPEN (Claude): Fomo new-wallet module lists wallets, not yet the coin each one traded (needs Fomo credits + profile call); dev-intel for coins we BUY (not only alert on) once positions exist; Core 2 target ($125K/1-2 months) vs code default ($90K/7 days) needs Ali's call on SPRINT_TARGET_USD.
- ALI: restart start_local_mode.bat, open Modules tab, tell me which module is wrong/missing.

## OCT 12 2026 LATE NIGHT -- BOOKS + ROUTER (Claude side, DONE)
- DONE: books (Core 1 $100->$125K/60d, Core 2 $100->$1M/150d, Compound daily goal, 4 copy books) with own balance, pace and pace-aware stake; router sends each coin to exactly one book (alert labelled by destination); radar + Layer 15 + band A/B coins wired to it; Fomo New Wallets pipeline built (inert until Fomo credits, then automatic, zero extra credits); daily summary shows every book; dashboard cards show balance/target/pace. 863 tests green.
- OPEN (Claude): copy books (leaderboard/fomo/dev) still use the old fixed stake on entry (book balance counts, stake not yet book-driven); Layer 15 pre-screen starts at $100K mcap so Core 2 early plays <$100K are not screened yet (needs a screen-threshold decision); tune router thresholds after first live data.
- ALI: push, restart start_local_mode.bat, check Modules tab, confirm Core targets ($125K Core 1, $1M-$1.5M Core 2).

## OCT 12 2026 NIGHT 2 -- PRE-EARLY SNIPER ENGINE (Claude side DONE; paper only)
Built: free PumpPortal stream -> per-coin tracker -> entries in the first seconds/minutes (Core 2 <=4 min, Core 1 <=20 min, Compound on >=3 confirmations, dev_list, leaderboard fallback). Router is age-first. See MODULES.md.

### ALI's actions
- [ ] `git push origin main` (if rejected: `git pull --rebase --autostash origin main`, then push)
- [ ] Restart `start_local_mode.bat` (now also starts early-watch)
- [ ] Run `python diag_early_stream.py` (30 s) and send me the output -- verifies the live PumpPortal field names
- [ ] Dashboard: Modules tab shows "Launch sniper LIVE"; System tab shows early-watch green
- [ ] Top up Fomo credits (HTTP 402) -- Fomo New Wallets starts by itself
- [ ] Windows sleep = Never; keep EXECUTION_ENABLED=false; free Helius key -> SOLANA_RPC_URL
- [ ] Let it collect 3-7 days, then send me the Modules tab numbers so thresholds can be tuned

### Claude's actions (open)
- [ ] Tune EARLY_*/ROUTER_* from real paper results
- [ ] Faster paper exits for minute-scale pumps (fast-watch manages ~1/min)
- [ ] Book-driven stakes for copy books

## STATUS as of Oct 12 2026, 10:15 PM PKT (pushed: 5783305)
DONE
- [x] Modules/books/router (Core 1 $125K/60d, Core 2 $1M/150d, Compound daily, copy books, Fomo New Wallets wired)
- [x] Pre-early sniper engine built; pushed; running via start_local_mode.bat
- [x] diag_early_stream.py run on your PC: launch feed WORKS (~25 launches/30 s), field names match
- [x] FOUND: PumpPortal per-coin trade feed needs an API key funded >= 0.02 SOL (~$3). Without it only dev-list launches can enter (at creation). Code auto-detects and shows "TRADE FEED OFF" on the Modules tab.
- [x] 876 tests green; code supports PUMPPORTAL_API_KEY in .env

OPEN -- ALI
- [ ] DECISION: (a) PumpPortal API key + 0.02 SOL (~$3) -> add `PUMPPORTAL_API_KEY=...` to .env, restart (fastest, enables all lanes); or (b) free Helius key -> `SOLANA_RPC_URL`, Claude builds a free trade feed from Solana logs (slower, unverified)
- [ ] Top up Fomo credits (HTTP 402) -> Fomo New Wallets + followers resume
- [ ] Windows sleep = Never; keep EXECUTION_ENABLED=false
- [ ] After 3-7 days: send Modules tab numbers; run `python backtest_buyer_signals.py`
OPEN -- CLAUDE
- [ ] Build free trade feed (Helius) if chosen; tune EARLY_*/ROUTER_* from data; faster paper exits; book-driven copy stakes

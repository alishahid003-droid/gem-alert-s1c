# S1c — Go-Live & 80% Win-Rate Checklist (single source of truth)

_Created Sept 30 2026. Every item has an OWNER and a DONE-WHEN. When an item
is finished it is ticked `[x]` in the SAME commit that finished it, with the
commit id. Nothing counts as done on "the code exists" alone — only on its
DONE-WHEN being met. Older checklists (FINAL_CHECKLIST_2026-09-27.md,
TASKS_LEFT_*.md, NEXT_STEPS.md) are history; this file supersedes them._

Owners: **ALI** = needs you (money, keys, your PC, a decision) · **CLAUDE** = I build it · **BOTH** = I build, you run/confirm.

How "win rate" is measured here: a trade is a WIN if the position closes with
positive P&L **after fees and slippage**. Win rate = wins ÷ closed trades,
measured first on the paper ledger (Phase 2), then on real trades. The 80%
target is a number we measure against every week — not something assumed.

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
- [ ] **1.3 ALI** — On the PC: `git pull`, then double-click **`setup_pc.bat`** (does 1.3 + 1.4 safely: backs up .env, removes test lines, asks your wallet size, disables any duplicate old MadeOnSol task, registers the 15-min job). DONE-WHEN: System tab shows `poll-madeonsol … (local-pc)` under 30 min old.
- [x] **1.4 ALI** — (done automatically by `setup_pc.bat`) Delete the `STAGE1_POSITION_USD=3`, `STAGE2_POSITION_USD=3`, `TOTAL_WALLET_USD=7` test lines from the PC `.env`. DONE-WHEN: lines gone. — done Sept 30 17:28 PKT via setup_pc.bat (backup `.env.backup-20260930-172801`, 3 test lines removed, TOTAL_WALLET_USD=100)
- [ ] **1.5 ALI** — Add GitHub Secret `TOTAL_WALLET_USD` = real trading-wallet size in USD. DONE-WHEN: diagnostic shows it SET. NOTE: poll-fast/poll-slow did not pass this secret through until `TOTAL_WALLET_USD` was added to both workflows (Sept 30) — set it to 100 to match the PC.
- [ ] **1.6 BOTH** — Run "Live diagnostic (read-only)" after 1 hour. DONE-WHEN: all 3 runners fresh, orphan records repaired, band A/B alerts show an Auto-buy verdict.
- [ ] **1.7 ALI** — fomoapi.io: wait for the monthly reset or top up. DONE-WHEN: System tab shows credits > 0.

## Phase 2 — Measure before money (the win-rate engine)

The single biggest lever for 80%: stop guessing, measure every would-buy.

- [ ] **2.1 CLAUDE** — **Paper-trading ledger**: every WOULD-BUY (Stage 1, Stage 2, scalper) opens a paper position at the real quoted price incl. estimated slippage/fees, and runs the SAME exit logic as real money, with live prices. Separate from real positions, never blocks real budget. DONE-WHEN: paper positions open/close automatically and show on the dashboard.
- [ ] **2.2 CLAUDE** — **Win-rate scoreboard** on the dashboard: win rate, average win, average loss, expectancy, max drawdown — broken down per signal (score band, deployer, convergence, Fomo roster, scalper), per chain, per trader. DONE-WHEN: visible and updating daily.
- [ ] **2.3 CLAUDE** — **Auto-disable losers**: any signal/trader/chain whose paper win rate is below target over ≥20 closed trades stops triggering real buys (still paper-traded so it can earn its way back). DONE-WHEN: rule live + unit-tested.
- [ ] **2.4 CLAUDE** — **Per-trader track record** for the 38 Fomo traders + promoted ones: copy only traders whose own copied-trade win rate is proven; demote the rest to alert-only. DONE-WHEN: ranking visible, gate enforced.
- [ ] **2.5 BOTH** — **Go/no-go gate**: real money only on signal types with paper win rate ≥ 80% (or the best achieved, with your explicit OK) over ≥ 30 closed paper trades. DONE-WHEN: gate numbers reviewed with you.
- [ ] **2.6 CLAUDE** — Grow the labeled backtest set from ~17 to 100+ real rugs / pump-dumps / moonshots and re-run weekly (current accuracy 54.5% on 11 tokens is too small to trust). DONE-WHEN: ≥100 labeled, result logged.

## Phase 3 — Entry quality (fewer, better buys)

- [ ] **3.1 CLAUDE** — **Confluence rule**: a real buy needs ≥2 independent layers agreeing (e.g. band A/B + tracked trader, or trusted deployer + momentum), not one signal alone. Tunable per signal from Phase 2 data. DONE-WHEN: enforced + tested.
- [ ] **3.2 CLAUDE** — **Sell-ability check before every buy**: quote the SELL leg too (Jupiter/router) and refuse if it can't route, tax > 10%, or honeypot/freeze/Token-2022 transfer-hook risk (GoPlus + on-chain). DONE-WHEN: buys refuse unsellable tokens in tests.
- [ ] **3.3 CLAUDE** — **Liquidity & slippage floor**: refuse if our size is > 2% of pool liquidity or quoted price impact > 3%. DONE-WHEN: enforced + tested.
- [ ] **3.4 CLAUDE** — **Late-entry guard**: refuse if price already up > X% in the last 5–15 min (don't buy the top); X tuned from paper data. DONE-WHEN: enforced + tested.
- [ ] **3.5 CLAUDE** — **Sniper/bundle & dev-sell guard**: refuse if top snipers/bundlers hold > 25% or the developer has started selling. DONE-WHEN: enforced + tested.
- [ ] **3.6 CLAUDE** — **Promote stricter A-band for real money**: until Phase 2 proves otherwise, only band A (or B + confluence) buys real; B alone paper-trades. DONE-WHEN: config + tests.

## Phase 4 — Exit discipline (this is where win rate is made)

- [ ] **4.1 CLAUDE** — **Hard stop-loss** for Stage 1/2 positions (e.g. −25% from entry, tunable). Today only the scalper has one — a slow bleed with no rug signal is held forever. DONE-WHEN: enforced + tested.
- [ ] **4.2 CLAUDE** — **Breakeven lock**: at +40–60% sell the slice that recovers the full stake + fees, then the rest rides risk-free (turns most green trades into locked wins). DONE-WHEN: enforced + tested.
- [ ] **4.3 CLAUDE** — **Trailing stop** on the remainder (e.g. give back max 30–40% from peak), keeping the existing 3x/10x/50x moonbag ladder and dollar targets. DONE-WHEN: enforced + tested.
- [ ] **4.4 CLAUDE** — **Time-stop**: exit a position that hasn't moved +X% within N minutes (dead coins tie up budget). DONE-WHEN: enforced + tested.
- [ ] **4.5 CLAUDE** — **Tracked-trader exit mirror**: when the Fomo trader(s) we copied sell, we sell (Layer 9 alerts already detect this — wire it to execution). DONE-WHEN: enforced + tested.

## Phase 5 — Speed & reliability (don't lose trades to plumbing)

- [ ] **5.1 CLAUDE** — **Fast position watcher on the PC**: checks open positions every 5–10 s for stop/target/rug exits (today: every 10 min on GitHub — far too slow for memecoins). DONE-WHEN: runs under Task Scheduler, heartbeat on dashboard.
- [ ] **5.2 CLAUDE** — **Solana priority fee** (and Jito tip option) on buys/sells so they land during congestion — currently none is set. DONE-WHEN: in swap path + tested.
- [ ] **5.3 CLAUDE** — **Buy/sell retry with fresh quote** (1 retry, re-checked slippage) on transient failures. DONE-WHEN: tested.
- [ ] **5.4 CLAUDE** — **Upstash usage check**: measure commands/day vs free-tier limit; batch/cache further if near it. DONE-WHEN: number logged, headroom ≥ 2x.
- [ ] **5.5 CLAUDE** — **Telegram alert on every real trade + every failed trade + stalled runner** (dashboard already shows it; phone alert so nothing is missed). DONE-WHEN: tested message received.
- [ ] **5.6 CLAUDE** — **Daily summary**: trades, win rate, P&L, API credits left — to Telegram once a day. DONE-WHEN: first one received.

## Phase 6 — First real money (Solana only)

- [ ] **6.1 ALI** — Create a NEW dedicated Solana wallet (never your main Phantom), fund it with a small amount. DONE-WHEN: funded.
- [ ] **6.2 ALI** — Add GitHub Secret `EXECUTION_SOLANA_PRIVATE_KEY` (that wallet only) + same line in the PC `.env`. DONE-WHEN: diagnostic shows SET.
- [ ] **6.3 CLAUDE** — Build a one-click **"Test trade" workflow**: buys ~$2 of a liquid token, sells it back, prints both tx links — works even while `EXECUTION_ENABLED` is off for the scheduler. DONE-WHEN: workflow exists + tested with mocks.
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
- Sept 30 2026 — checklist created; Phase 0 filled from today's commits.
- Sept 30 2026 — 1.1/1.2 done: PR #1 merged into main (`9053bd2`).
- Sept 30 2026 12:24 UTC — live diagnostic on main: poll-fast + poll-slow running the new code (heartbeats 3-4 min old); the 2 Sept-24 orphan records auto-repaired (budget freed); first Auto-buy verdict recorded. NOT yet: no `poll-madeonsol` heartbeat (PC task is the old Sept 28 one, running pre-merge code -> 1.3/1.4 still open); `TOTAL_WALLET_USD` secret missing (1.5); fomoapi.io still 0 credits, governor backing off correctly (1.7).
- Sept 30 2026 17:28 PKT — PC: stash of old local layer13 edits (`pc-local-edits-before-sept30`), pull to 4fe41d0, setup_pc.bat OK, task re-registered; 1.4 done; 1.3 waiting on first `poll-madeonsol` heartbeat.

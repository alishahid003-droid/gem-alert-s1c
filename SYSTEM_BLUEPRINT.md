# S1c SYSTEM BLUEPRINT — 15 Oct 2026 (written 10:40 PKT)
Live is OFF (KILL_SWITCH). Everything below is backed by our own logs; "paper" = simulated.

## 1. Where we really are (data)
Paper results, all streams, all history (1,459 closed trades):
| Stream | Trades | Staked | P&L | Return |
|---|---|---|---|---|
| Compound (solana) | 636 | $2,544 | -$497 | -19.5% |
| Sprint (solana) | 474 | $2,370 | -$605 | -25.5% |
| Core 2 / "core1" book (solana) | 337 | $1,348 | -$457 | -33.9% |
| Core 3 / moon (solana, multichain) | 10 | $76 | -$41 | tiny sample |
| Robinhood chain (multichain) | 1 | $14 | +$0.7 | tiny sample |
Fresh code (restart 01:38): 3,605 coins, mean x0.82/trade; trend-matched 193 coins x0.86 (ex-best-2 x0.83).
Tested and failed on honest data: buy at creation (x0.81 @25% cost), buy on early buys/volume (x0.57 ex-glitch), runner exits (same), score>=5 (x0.83), exit grids (best x0.85 out-of-sample).
Why: at launch all coins look identical; winners (30x+) only separate AFTER the first 1-2 min, when price has already run 2.5-15x.
Only real lifts found: early volume/buys (3x lift to reach 2x, but drift kills it), good devs (~20% vs 6% win; tiny sample), trend names (35% win, x0.86).

## 2. The five modules (status as of 15 Oct 2026, ~11:30 PKT)
| # | Module | Status today | Gap / next build |
|---|---|---|---|
| 1 | Copy trading | KOL feed (MadeOnSol, ~190 calls/day, paced) + direct on-chain wallet watch (wallet_watch.py: harvested KOL wallets + our pump.fun smart-money roster + smart_wallets_v1) -> kol_buy / kol_converge confirmations. Fomo followed traders: code ready, credits 0. Base/BSC/RHC: evm_wallets.py learns early buyers of winning pools (GeckoTerminal, live-verified: 16 BSC wallets learned) -> smart_wallet confirmation, paper only | wallet_watch was failing on public RPCs until the Helius fix (0a89aa1); verify after restart. EVM list needs ~a day to fill |
| 2 | New high-balance wallets | Fomo layer13 + fomo_new_wallet confirmation wired; hourly credit pacing added | Fomo credits are 0 (reset date unknown) - nothing flows until they return |
| 3 | Dev launches | dev_rank scoreboard (65 good devs, 20-min refresh) + dev_history.py backfill (Helius/RPC + DexScreener) + dev_alerts.py Telegram/dashboard alert (DEV LAUNCH). Screenshot list OFF (0/69 graduated) | dev_history untested live (check [dev_history] log lines); MadeOnSol Deployer Hunter starved by its call budget; GMGN key optional, no deployer history in public docs |
| 4 | Virality | trend_feeds: Google, Wikipedia, Bluesky, Mastodon work; X/TikTok/Reddit-popular return nothing | fix feeds, tighten to names/phrases |
| 5 | Own filters / score | score() combines signals incl. new copy/dev/whale confirmations; rug/dump filters; GoPlus honeypot gate on EVM only (pump.fun curve always sellable) | re-fit weights weekly on logged data |

**Honest state:** no entry rule beats break-even yet (best fresh-data result x0.83 after costs). Live stays OFF (KILL_SWITCH) until a stream passes the gate: >=30 fresh trades, mean >x1.0 after dropping best 2, stake <=5% of wallet.

## 3. Chains
- Solana pump.fun: the real entry engine (early-watch). Only place live execution exists (PumpPortal).
- BSC, Base, Robinhood, Solana non-pump launchpads, StonkFun: multichain_sniper runs, PAPER ONLY. No live trade adapter exists for BSC/Base/Robinhood. Data so far: 11 closed trades -> nothing to judge.
- KOL feed currently polls solana and robinhood only (not Base/BSC).
- four.meme: not covered (GeckoTerminal lists a coin only after it has a DEX pool).

## 4. Stream plan vs your four targets (stated honestly)
Targets: $15,000 by Monday (~96 h) | ~$20,000 in ~20 days | $125-150k in November | $1M+ final.
From ~$17.5 these are ~860x, ~1,140x, ~7,000-8,500x. No strategy reaches them by edge at this size; they are lottery outcomes. The system is shaped to maximise the number of well-chosen lottery tickets without bleeding the wallet:
- Sprint (bills): live only when module 1 or 3 fires; $1-2 ticket; sell ~half at 2x, runner for the rest.
- Compound (daily in/out): same module-1 signals, quick 2x-3x exits, fresh trades all day.
- Core 2 / Core 3: $0.50-1 tickets on rare signals only (good dev, first-on-trend, KOL convergence); hold runners.
- Never idle: all streams run on paper 24/7; only the best-evidence stream gets live money.
- Promotion gate to live per stream: >=30 trades on fresh data with mean >x1.0 after dropping the best 2; stake <=5% of wallet; scale only after 30 live trades >x1.0.
- Kill switch stays until a gate passes; only Ali removes it.

## 5. Build order
1. Restart once (applies trade-feed narrowing, protects 20k/day message budget).
2. Wire KOL/roster buys into early-watch as confirmations (module 1).
3. Fomo new-wallet signal (module 2) once credits confirmed.
4. Add Base/BSC to KOL polling if MadeOnSol supports them; keep multichain paper.
5. Re-run per-signal analysis every few hours; promote a stream only on the gate above.

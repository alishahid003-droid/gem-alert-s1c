# S1c MASTER CHECKLIST -- updated Oct 13 2026, 01:45 PKT
(Single source of truth. Supersedes GO_LIVE_CHECKLIST.md, FINAL_CHECKLIST_2026-10-12/13.md.)
Legend: [x] done  [~] running / in progress  [ ] to do   (A) = Ali does   (C) = Claude does

## PHASE 0 -- Build (DONE, all pushed to GitHub, 897 tests)
- [x] Pump.fun early sniper: stream, confirmations, lanes, exit engine (2x/4x/10x take-profits, hard stop, trailing moon bag, dead-coin stop)
- [x] Books: Core 1 = Sprint 96h ($100->$10K, cash-out + $200 reseed), Core 2 = $125K/30d, Core 3 = $1M-1.5M/180d, Compound, copy books; profit vault; compounding stakes; pace gauge
- [x] Dashboard = single place for alerts, links, P&L (Telegram optional/off)
- [x] Multi-chain fast sniper (BSC, Base, Robinhood, Solana non-pump + StonkFun) -- PAPER only
- [x] Recorder + analyze_early.py + creator reputation; backtest_sniper.py; run_checks.bat
- [x] Git / Notion / folder in sync (STATUS_2026-10-13_NIGHT.md has the night narrative)

## PHASE 1 -- Evidence run (NOW, the 24h clock started 01:31 PKT Oct 13)
- [~] (A) PC on + awake (Windows sleep = Never), the minimized windows left open. Restart ONLY from an ADMINISTRATOR cmd (lesson learned: old admin python processes block normal restarts)
- [~] Pump.fun sniper + multichain sniper running (confirmed in logs 01:33; paper entries on Solana/BSC/Robinhood already firing)
- [ ] (C) Morning: read logs\local_runner.log, local_early-watch.log, dashboard; confirm Base + StonkFun entries appear
- [ ] (C) Fix Compound book showing $0.00 (lane never opens)
- [ ] (C) Tune loose multichain entries (3-confirmation coins entering Core 1; 3-open-position cap), MC_* thresholds
- [ ] (A+C) Re-run run_checks.bat (backtest top/new + feed health) -- NOT while sniper quota is tight; Claude reads logs\check_*.txt
- [ ] (A) ~01:31 PKT Oct 14: python analyze_early.py, send output -> (C) tune EARLY_*/MC_*/ROUTER_* stakes, ladder, bank_pct

## PHASE 2 -- Make it LIVE (needs Ali decisions; nothing live until Phase 1 data looks sane)
Pump.fun (Solana) -- adapter already built, paper until enabled:
- [ ] (A) Create PumpPortal API key, fund wallet 0.02 SOL minimum (tiny, affordable amount only)
- [ ] (A) Put PUMPPORTAL_API_KEY in .env (also turns on the per-coin trade feed)
- [ ] (A) Set EXECUTION_ENABLED=true, EARLY_LIVE_LANES=<ONE lane first, e.g. sprint>, LIVE_MAX_STAKE_USD small (default $5), LIVE_MAX_OPEN, LIVE_DAILY_LOSS_USD
- [ ] (A) First test: watch one live buy+sell on the dashboard; kill switch ready (kill_switch.py)
Other platforms -- adapters NOT built yet (Claude builds after Ali picks, paper until switched on):
- [ ] (A) Pick first chain. Recommended order: Four.meme/BSC -> StonkFun (Solana) -> Base -> Robinhood
- [ ] (A) A separate BURNER wallet per chain, funded with only losable money; private key goes in .env ONLY (never in chat)
- [ ] (A) 1inch API key (Base/Robinhood swaps)
- [ ] (C) Build adapters: four.meme bonding-curve buy/sell + PancakeSwap after migration; Jupiter for StonkFun; 1inch for Base/Robinhood; honeypot/tax gate; slippage guard; kill switch
- [ ] (A) First live test $2-5 per chain
- [ ] Fomo: no trade API -- stays alerts + manual execution via dashboard links
- [ ] (A) Fomo credits top-up (HTTP 402)
- [ ] (A) Free Helius key -> SOLANA_RPC_URL (stability)

## PHASE 3 -- Run to targets (after live is verified)
- [ ] Core 1 Sprint: $100-200 -> $10K in 96h. On hit: CASHED OUT banner appears -> (A) withdraw by hand, book reseeds $200
- [ ] Core 2 $125K in 30d, Core 3 $1M-1.5M in 180d on their own seeds
- [ ] Review vault/banking and stakes weekly from real results

## HONEST RISKS (keep in view)
- The targets are what growth is REQUIRED (Sprint ~+216%/day, Core 2 ~+27%/day, Core 3 ~+5.2%/day), not shown performance. No proven edge yet; Sprint is lottery-like. Live stakes tiny, one lane first, only affordable money.
- Non-Solana chains: only coins that already have a DEX pool are visible; four.meme bonding-curve phase is not seen; thresholds untuned.
- All live feeds unverified from Claude's side except what the logs show on Ali's PC.

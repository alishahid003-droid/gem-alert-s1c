# S1c CHECKLIST -- update 13 Oct 2026 (replaces nothing; supplements FINAL_CHECKLIST_2026-10-12.md)
DONE since last list: dashboard Sniper desk (alerts, trade links incl. GMGN verified, positions, paper+live P&L); Telegram off (TELEGRAM_ENABLED=false); DNS-over-HTTPS fallback; cron-job.org GitHub poll jobs paused (3 inactive); launch recorder + outcome sampler; early exit engine; PumpPortal live adapter with risk caps + kill switch; creator-reputation table + graduation recording + `analyze_early.py` lift report.
ALI (open): (1) trade-feed decision: PumpPortal API key + 0.02 SOL -> PUMPPORTAL_API_KEY in .env (also enables live execution), or Helius route; (2) Fomo credits; (3) Windows sleep = Never; (4) restart start_local_mode.bat after each push/.env change; (5) after 24h run `python analyze_early.py` and send me the output.
CLAUDE (open): tune from analyze_early results; Helius trade feed if chosen; dev-list wallet verification; copy-book stakes.
HONEST STATE: the 24h run measures which launch features actually lift graduation odds on YOUR data. It cannot prove a 100x edge. Keep live stakes tiny (LIVE_MAX_STAKE_USD) and only with money you can afford to lose.

## ADDED Oct 13 (capital engine)
- [x] Sprint 96h book ($100 -> $10,000 in 4 days), Core 1 = 30 days, Core 2 = 180 days
- [x] Profit vault (banks part of profit above 3x), compounding stakes, 2x/4x/10x take-profits + moon-bag trailing stop
- [ ] ALI: after go-live, keep EARLY_LIVE_LANES to ONE lane at first (e.g. sprint) with LIVE_MAX_STAKE_USD tiny; never fund live with money you cannot lose
- [ ] CLAUDE: tune BANK_AFTER_MULT / bank_pct, stake %, take-profit ladder from the 24h analyze_early results

## ADDED Oct 13 (multi-chain)
- [x] multichain_sniper.py: BSC / Base / Robinhood / Solana-nonpump + StonkFun on the fast loop with the same lanes and exit engine (paper)
- [ ] ALI: after restart open logs\local_runner.log and check lines "[multichain] sniper loop started" and "Robinhood chain slug"; send me any "not found" line
- [ ] CLAUDE: tune MC_* thresholds from 24h data; add live execution for non-Solana chains only if paper results justify it

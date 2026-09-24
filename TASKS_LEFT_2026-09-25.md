# Tasks Left — Sept 25, 2026

1. [ ] Robinhood Chain — auto-buy (blocked: needs Uniswap v4 pool key data not yet fetched)
2. [ ] Robinhood Chain — auto-sell (same block)
3. [x] Dashboard — trade/fill history table (tx hash, filled amount, timestamp)
4. [x] Dashboard — realized P&L display (fixed the real bug behind it: sells never set filled_usd)
5. [x] Dashboard — closed-positions history table
6. [x] Dashboard — backend trade-log addition feeding items 3–5
7. [ ] Re-run Solana/RHC backtest once MadeOnSol's rate limit clears
8. [x] Fix 和平熊猫 duplicate-contract display-name scoring bug
9. [ ] Speed — switch Layer 0b from REST polling to Mobula's WebSocket stream
10. [ ] Build a real multi-coin backtest set (dozens of gems + rugs)
11. [ ] Decide on MadeOnSol coverage: paid tier vs. second data source (your call)
12. [x] `entrypoint.py` already wired into `scheduler.py`'s live poll loop (confirmed, not new work — was already true, docstring was just stale)
13. [ ] Real test: 1 live Solana buy — needs you, real money
14. [ ] Real test: 1 live Solana sell — needs you, real money
15. [ ] Real test: 1 live BSC buy — needs you, real money
16. [ ] Real test: 1 live BSC sell — needs you, real money
17. [ ] Real test: 1 moonbag trim on a real live position — needs you, real money
18. [ ] Real test: dashboard checked against real trade data (now unblocked — item 6 is done, just needs 13-17 to produce real data to look at)
19. [ ] Keep `EXECUTION_ENABLED=false` until tasks 13-16 all pass
20. [ ] `git push origin main` — 3 commits ready (9f69a9e, 94e18f3, 3bb763d) — needs your GitHub login, my sandbox has no credentials for it

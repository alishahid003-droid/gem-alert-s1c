# Tasks Left — Sept 25, 2026

1. [ ] Robinhood Chain — auto-buy (blocked: needs Uniswap v4 pool key data not yet fetched)
2. [ ] Robinhood Chain — auto-sell (same block)
3. [x] Dashboard — trade/fill history table (tx hash, filled amount, timestamp)
4. [x] Dashboard — realized P&L display (fixed the real bug behind it: sells never set filled_usd)
5. [x] Dashboard — closed-positions history table
6. [x] Dashboard — backend trade-log addition feeding items 3–5
7. [ ] Re-run Solana/RHC backtest once MadeOnSol's rate limit clears
8. [x] Fix 和平熊猫 duplicate-contract display-name scoring bug
9. [x] Speed — Layer 0b WebSocket client + worker built and wired into scheduler._handle_scored (commit 88bfc69, 10 mocked tests passing). NOT YET provably live: Mobula's Pulse Stream V2 is Growth/Enterprise-plan only (your key may be free tier) and BSC support on this endpoint is unconfirmed by Mobula's own docs -- run `python worker_pulse_websocket.py` once pushed to see the real accept/reject and the raw `init` message per chain.
10. [x] Real multi-coin backtest already exists as `backtest.py` (~20 real elite/good-deployer + ~20 real spammer-deployer Solana tokens pulled live from MadeOnSol, scored through the live engine) -- found and fixed a real bug blocking it (missing .env load, commit 35740be). NOT YET RUN: needs real network backtest.py can't get from my sandbox (madeonsol.com is proxy-blocked here) -- trigger it yourself either via `python backtest.py` in your own terminal, or GitHub Actions tab -> "Layer 0/0b rug-scoring backtest" -> Run workflow (already wired, workflow_dispatch, needs MADEONSOL_API_KEY set as a repo secret).
11. [ ] Decide on MadeOnSol coverage: paid tier vs. second data source (your call)
12. [x] `entrypoint.py` already wired into `scheduler.py`'s live poll loop (confirmed, not new work — was already true, docstring was just stale)
13. [ ] Real test: 1 live Solana buy — needs you, real money
14. [ ] Real test: 1 live Solana sell — needs you, real money
15. [ ] Real test: 1 live BSC buy — needs you, real money
16. [ ] Real test: 1 live BSC sell — needs you, real money
17. [ ] Real test: 1 moonbag trim on a real live position — needs you, real money
18. [ ] Real test: dashboard checked against real trade data (now unblocked — item 6 is done, just needs 13-17 to produce real data to look at)
19. [ ] Keep `EXECUTION_ENABLED=false` until tasks 13-16 all pass
20. [ ] `git push origin main` — 3 new commits ready on top of your last push (88bfc69, 2c53284, 35740be: #9 WebSocket work + #10 backtest fix) — needs your GitHub login, my sandbox has no credentials for it

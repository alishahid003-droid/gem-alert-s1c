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
10. [x] Real multi-coin backtest infra fixed (backtest.py). Along the way found and fixed a BIGGER, real production bug: Layer 1's `/deployer-hunter/alerts` calls (the live poll-fast.yml cron job, every 10 min) were sending `tier=elite,good` as one comma-joined param -- MadeOnSol's API rejects that with 400 and wants the tier repeated per-value instead. Confirmed live via diagnostic. This means Layer 1's elite/good deployer alerts had almost certainly NEVER actually come through in production, silently, since it was built (commit a04b144, regression test added, 278 tests passing). Still needs you to run: `python backtest.py` for the actual gems-vs-rugs numbers, once you've pushed.
11. [ ] Decide on MadeOnSol coverage: paid tier vs. second data source (your call)
12. [x] `entrypoint.py` already wired into `scheduler.py`'s live poll loop (confirmed, not new work — was already true, docstring was just stale)
13. [ ] Real test: 1 live Solana buy — needs you, real money
14. [ ] Real test: 1 live Solana sell — needs you, real money
15. [ ] Real test: 1 live BSC buy — needs you, real money
16. [ ] Real test: 1 live BSC sell — needs you, real money
17. [ ] Real test: 1 moonbag trim on a real live position — needs you, real money
18. [ ] Real test: dashboard checked against real trade data (now unblocked — item 6 is done, just needs 13-17 to produce real data to look at)
19. [ ] Keep `EXECUTION_ENABLED=false` until tasks 13-16 all pass
20. [ ] `git push origin main` — 5 new commits ready (88bfc69, 2c53284, 35740be, 109271a, a04b144: #9 WebSocket work + #10 backtest fix + the Layer 1 deployer-alerts production bug fix) — needs your GitHub login, my sandbox has no credentials for it

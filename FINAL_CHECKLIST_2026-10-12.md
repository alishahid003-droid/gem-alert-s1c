# S1c FINAL CHECKLIST -- 12 October 2026 (everything left, one list)
Status: paper trading only. Code pushed through 8c9ec52. 876 tests green. Real money OFF (`EXECUTION_ENABLED=false`).
Legend: [ ] open  [x] done | Owner: ALI / CLAUDE | Blocks = what it stops until done

## DONE (for reference)
- [x] Modules + books: Core 1 ($100 -> $125K / 60d), Core 2 ($100 -> $1M / 150d), Compound (daily), Leaderboard, Fomo Followers, Fomo New Wallets (wired), Dev Launch ($50 each)
- [x] Age-first router (market cap no longer picks Core); Layer 15 post-move screen feeds Compound only
- [x] Pre-early sniper engine (PumpPortal launch stream -> tracker -> entries), supervised by local_runner, pushed and running
- [x] Diagnostic run on Ali's PC: launch feed works (~25 launches / 30 s), field names match
- [x] Found blocker: PumpPortal per-coin trade feed needs an API key funded >= 0.02 SOL (~$3). Code handles it (PUMPPORTAL_API_KEY, "TRADE FEED OFF" flag, dev-list entry at creation)
- [x] Dashboard Modules tab, Telegram module labels, Notion banner, HANDOFF, MODULES.md

## PHASE 1 -- TURN ON THE FULL SNIPER (today / tomorrow)
| # | Item | Owner | Blocks |
|---|---|---|---|
| 1 | DECIDE the trade feed: (a) PumpPortal API key + 0.02 SOL -> `PUMPPORTAL_API_KEY=` in .env, restart; or (b) free Helius route | ALI | Core 1, Core 2, Compound sniper lanes (all buyer-count logic) |
| 2 | Free Helius key -> `SOLANA_RPC_URL=` in .env (needed for wallet/dev lookups either way) | ALI | on-chain safety checks (#9), Helius trade feed (#3) |
| 3 | If (b) chosen: build free trade feed from Solana logs (logsSubscribe on the pump.fun program) + verify on Ali's PC | CLAUDE | only if route (b) |
| 4 | Top up Fomo credits at fomoapi.io (HTTP 402) | ALI | Fomo Followers, Fomo New Wallets, roster/smart-wallet confirmation |
| 5 | Windows sleep = Never, plug-in power; rerun `start_local_mode.bat` after every .env change | ALI | 24/7 collection |
| 6 | Verify live: dashboard System tab shows early-watch green; Modules tab says "Launch sniper LIVE" with no "TRADE FEED OFF" | ALI (screenshot to CLAUDE) | confirms #1 worked |

## PHASE 2 -- BUILD THE EDGE (Claude, starts as soon as #1 is decided; these decide whether the system works)
| # | Item | Owner | Why |
|---|---|---|---|
| 7 | Launch recorder: save every launch + trade + outcome (price path at 1/5/15/60 min, peak, rug yes/no) to disk | CLAUDE | We have NO history; without this no rule can be tested. After 7-14 days it lets us measure which confirmations actually predict winners |
| 8 | Backtest/analysis script on recorder data: hit rate, avg multiple and drawdown per lane and per confirmation; run `python backtest_buyer_signals.py` on existing data too | CLAUDE | Replace guessed thresholds with measured ones |
| 9 | On-chain safety filters beyond the creator: bundled/sniper wallets at launch, top-holder concentration, mint/freeze authority, dev's past-launch rug rate for non-listed devs | CLAUDE | Today only creator hold %, creator dump, whale share and sell pressure are checked; most launches rug |
| 10 | Real-time exit engine for early positions (partial take-profit at 2x/4x/10x, trailing stop, hard stop, rug-exit on dev sell / volume collapse), checked every few seconds not ~1/min | CLAUDE | Minute-scale pumps and rugs are mishandled now; this is where profit is kept or lost |
| 11 | Dev list verification: only 6 of 25 wallets verified (17 exist-only, 2 error); re-verify, and drop unverified ones from dev_list confirmation | CLAUDE | Dev Launch book relies on this |
| 12 | Tune `EARLY_*` and `ROUTER_*` thresholds from #8 results | CLAUDE | Current values are untuned guesses |
| 13 | Book-driven stakes for the copy books (Leaderboard, Fomo, Dev) instead of fixed entry stakes | CLAUDE | Copy books don't yet compound on their own balance like Core 1/2/Compound |
| 14 | Realistic paper fills: add slippage + fee model so paper P&L isn't optimistic | CLAUDE | A paper win that real fills can't reproduce is worthless |
| 15 | 50x+ coin screenshots (name + chain) to calibrate what winners looked like in their first minutes | ALI | Calibration for #8/#12 |

## PHASE 3 -- RELIABILITY (before trusting it unattended)
| # | Item | Owner |
|---|---|---|
| 16 | Telegram alert when early-watch dies, the feed goes quiet > 2 min, an API key/credits run out, or the daily entry cap is hit | CLAUDE |
| 17 | Latency test from Lahore (stream delay, RPC round-trip); decide whether to move the whole system to a small cloud server (EU/US) so it runs 24/7 and closer to Solana | CLAUDE (test), ALI (decision on server cost) |
| 18 | Decide long-run cost: Fomo credits + trade-feed key + any VPS must be covered by paper results | ALI + CLAUDE |

## PHASE 4 -- PAPER PROOF (7-14 days after Phase 1 is on)
| # | Item | Owner |
|---|---|---|
| 19 | Let it run; send Modules tab numbers (per-book balance, win rate, avg multiple) every few days | ALI |
| 20 | Go/no-go review: positive paper P&L per book after slippage model, over >= 7 days and >= 50 entries in a lane. If a lane loses, we cut or rework it -- no going live on hope | CLAUDE |

## PHASE 5 -- REAL MONEY (only after #20 passes, lane by lane, smallest first)
| # | Item | Owner |
|---|---|---|
| 21 | Real fast execution: priority fees / Jito tip, slippage cap, fast-route buy+sell, retry logic; test with $1 | CLAUDE |
| 22 | Risk controls: max stake per trade, daily loss cap, max open positions, global KILL SWITCH (one command / dashboard button) | CLAUDE |
| 23 | Fund a NEW dedicated Solana wallet (small amount, ~$4 to test) and run `python test_trade.py --usd 1` | ALI |
| 24 | Flip `EXECUTION_ENABLED=true` for ONE lane (.env + GitHub Secrets), run `update.bat`, watch the first 10 trades | ALI |

## HONEST CAVEATS (do not skip)
- No proven edge yet. Most launches rug or die (graduation rate ~1%). Pre-early entry is high variance by nature.
- Pace targets (Core 1 ~ +12%/day, Core 2 ~ +6%/day) are what the targets REQUIRE, not what the strategy has shown.
- Thresholds are guesses until #7-#12 are done. Paper fills are optimistic until #14.
- Dev list is a + signal only, never a sole trigger.

## CRITICAL PATH
#1 (decide feed) -> #5/#6 (verify live) -> #7 (recorder) running -> #10 + #9 (exits, safety) -> 7-14 days -> #8/#12 (measure, tune) -> #20 (go/no-go) -> #21-#24.
Fastest ALI actions today: #1, #2, #4, #5.

## UPDATE 10:45 PM -- 24-HOUR PLAN (Ali: no time for 7-14 days)
BUILT TONIGHT (tests green): launch recorder + outcome sampler (#7), analysis script `python analyze_early.py` (#8), bundle/sniper detection (#9 partial), real-time exit engine with take-profits/stop/trailing/time-stop checked every 6 s (#10), slippage/fee model (#14), watchdog Telegram alerts (#16 partial), PumpPortal live-execution adapter with risk caps + `python kill_switch.py on|off` (#21, #22). Live trading is OFF until EXECUTION_ENABLED=true AND PUMPPORTAL_API_KEY AND EARLY_LIVE_LANES set.
24h RUN: recorder samples ALL launches (tens of thousands) at +5/+15/+60 min, which gives far more evidence than the few paper entries. After 24h: `python analyze_early.py` -> go/no-go. 24h proves the plumbing and a rough hit rate, NOT a durable edge. Live stakes stay tiny (LIVE_MAX_STAKE_USD=5, LIVE_DAILY_LOSS_USD=10, LIVE_MAX_OPEN=3) so the live test is the real validator.
BLOCKER FOUND: ISP DNS blocks api.telegram.org -> no Telegram alerts reach Ali. Fix: DNS 1.1.1.1/8.8.8.8 or VPN.
GitHub "run failed" emails: disable the cron-job.org "gem-alert" jobs (local mode owns state).

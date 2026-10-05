# S1c — Handoff & Status (single source of truth)

_Last updated: Oct 1 2026. Point of truth for continuity = this file + GitHub `main`.
Not Notion. A new Claude / Cowork session: read this, then `GO_LIVE_CHECKLIST.md`,
then run the tests (`python -m pytest -q`, 778 pass)._

## 0. The honest bottom line (read first)
- **Real money is OFF.** `EXECUTION_ENABLED` not set; the system only alerts + paper-trades.
- **No strategy has shown a winning edge yet.** The rule search (560 entry/exit
  rule combos) and the alert replay both show ~6–8% win rate on the coins the
  system alerts on — because they come from TRENDING lists AFTER the pump.
  The problem is WHICH coins get picked, not the exits.
- **The system's own paper-record gate is correctly blocking band B from real
  money.** Nothing would have been lost live.
- **Do NOT turn on `SPRINT_MODE` / `EXECUTION_ENABLED` until a signal proves
  positive on data it has never seen (see Phase 9 / §4).**
- This is NOT a path to $90k by any near deadline. Honest, not hopeful.

## 0b. Oct 5 2026 incident -- Upstash free tier exhausted (READ THIS)
- The free Upstash DB (500K commands/MONTH) hit its cap: 908K reads / 86K writes.
  Upstash then answers every call with HTTP 400 "max requests limit exceeded";
  reads looked empty, so the dashboard showed "never"/0 everywhere and nothing
  was recorded. Check: `python -c "..."` printing `/dbsize` body, or the
  console Usage tab. Data is retained; access returns after reset/upgrade.
- Measured (counting fake Upstash): idle fast-watch tick 10 cmds, dashboard
  refresh 25, discovery cycle floor 58. Fix pushed in `f741699`: read cache,
  quota guard (stops calls + refuses real BUYS while blocked), throttled
  cycles (poll-fast ~30 min, poll-slow ~60 min, madeonsol ~30 min via
  `POLL_FAST_/POLL_SLOW_/MADEONSOL_MIN_INTERVAL_SECONDS`), fast-watch idle 180 s,
  dashboard refreshes only while visible (3 min). Heartbeat note now shows the
  REAL commands/min per runner -- watch it; target < 16K/day total.
- Ali's PC `.env` had `EXECUTION_ENABLED=true` + a Solana key (contradicts §0);
  must be confirmed/turned off while real money has no proven edge.
- To restore service: new/upgraded Upstash DB, update `UPSTASH_REDIS_REST_URL/TOKEN`
  in the PC `.env` AND the GitHub Secrets, then `update.bat`.

## 1. Where everything lives
- **Code of record:** GitHub `main` (all PRs #1–#26 merged; working branch has
  nothing unmerged).
- **This file + `GO_LIVE_CHECKLIST.md`** = the running log (Phases 0–9, tests T1–T13).
- **PC:** `C:\Users\razas\Downloads\gem-alert-s1c_6`. Update with `update.bat`
  (pull + restart fast watcher + restart dashboard). Dashboard: http://localhost:8787.

## 2. What runs now (all free, no real money)
| Runner | Where | Cadence |
|---|---|---|
| poll-fast / poll-slow | GitHub Actions (cron-job.org) | 10 / 20 min |
| poll-madeonsol | PC Task Scheduler | 15 min (≤190 MadeOnSol calls/day) |
| fast-watch | PC Startup | 20–60 s |
| alert-replay | GitHub | daily 06:07 UTC |

## 3. What is DONE and TESTED
- Discovery on 4 chains (Solana, BSC, Base alert-only, Robinhood Chain).
- Scoring (safety), entry guards, exit rules, moonshot (L15), momentum lane (L16),
  compound aggressor, 5-day sprint, milestones.
- Rug/honeypot checks, on-chain sell-leg check before every buy.
- Reliability: retry-once, Telegram alerts, daily summary, Upstash budget.
- Price-glitch filter (ignore >4x jumps between reads) for paper + real exits.
- Dashboard: Modules panel, Win-Rate scoreboard (by module/signal/chain/exit).
- Test tools: `rule_search.py`; replay `--source alerts|fomo|launches`.
- $2 test-trade DRY RUN passed. 778 automated tests pass.

## 4. What we are WAITING ON (for tomorrow / next session)
These collect data automatically; run the replay to read them:
1. **Fomo copy-trade test** — needs Fomo credits (reset daily ~05:00 PKT; a
   7-day archive of EVERY trader's buys is being recorded).
   → Run: Actions → "Alert replay" → input **source = fomo**. Ranks which
   traders are actually profitable to copy.
2. **Buy-at-launch test** — the launch archive records every new pool.
   → Run: Actions → "Alert replay" → input **source = launches**. Tests buying
   early (2 min after launch) without the trending-list hindsight.
3. **Daily alerts replay** — runs itself 06:07 UTC; current result ~6–8% (bad).
4. **Momentum-lane paper record** — on the dashboard Modules panel (currently ~16%).

Decision rule: wire a strategy to real money ONLY if it shows **positive
expectancy on the UNSEEN (newer) half** in one of these tests.

## 5. The plan to make it BETTER (Phase 9 in GO_LIVE_CHECKLIST.md)
The edge is NOT "early" — it's "early AND a proven smart wallet is buying".
- 9.1 Smart-wallet-buying signal (per platform; Fomo list ≠ pump.fun ≠ StonkFun).
- 9.2 Distinct-buyer velocity (unique wallets in first 2–5 min, accelerating).
- 9.3 Anti-bot filter: funding-source clustering — many independent funders =
  organic; all from 1–2 funders / brand-new wallets / identical sizes = bot farm → reject.
- 9.4 Dev/sniper concentration cap (> ~20–30% held → reject).
- 9.7 Data tier: **Helius (real-time Solana stream, FREE tier to start, ~$50/mo
  later) + a curated wallet list.** Neither alone is enough. MadeOnSol PRO is
  polling, not real-time. **Least capital: $0 to start; pay only after a signal
  proves out.**
- 9.8 StonkFun/Robinhood: Helius & MadeOnSol are Solana-only → separate data
  source needed; defer until Solana path proven.

## 6. ALI's open steps (unchanged; none urgent while real money is off)
- Fund the NEW Solana wallet + `python test_trade.py --usd 1` (needs ~$4 SOL).
- Only after a test (§4) proves an edge: add `EXECUTION_ENABLED=true` (+ the
  relevant mode switch) as GitHub Secrets AND in the PC `.env`, then `update.bat`.
- Paid data (Helius) — optional, only after §4 shows a real edge.

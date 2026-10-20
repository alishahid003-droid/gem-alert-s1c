# S1c system structure (20 Oct 2026)

One page mapping every module Ali named to what actually exists in the code today, what filters it,
and how a coin moves from "found" to "in a book." Grounded in `layers/modules.py` (the single registry
every part of the system already reads from) and `layers/router.py` (the one place that decides which
book a coin lands in, so nothing gets picked twice).

## The shape: FINDERS feed BOOKS

A **finder** looks for coins and hands qualifying ones to the router. A **book** holds actual paper
(later: real) positions with their own balance, stake size and exit rules. A coin is only ever routed
to ONE book — the router (`layers/router.py`) is the single gate that decides which, based on how early
it is and how strong the evidence is, so the same coin never gets double-counted across modules.

```
FINDERS (find & qualify coins)              BOOKS (hold positions, have exits)
├─ Own Analysis (layer0/0b/0c/8/14/15/16)    ├─ Sprint 96h      ($100->$10K, most aggressive)
├─ Catalyst Radar (listing/news/launchpad)   ├─ Core 1          (10x-50x, first ~20min)
├─ Catalyst Revival / Virality (layer18)     ├─ Core 2          (100x+, first few minutes)
├─ Sniper/Insider Wallet Watch (layer19)     ├─ Compound        (2x-13x swings, daily profit)
├─ Dev Intel (per-coin, not a finder itself) ├─ Leaderboard Copy-Trading
└─ Fast lane (layer17 real-time launch)      ├─ Fomo Followers
                                              ├─ Fomo New Wallets
                                              ├─ Dev Launch Copy-Trading
                                              └─ Fast (sprint/core split by speed)
```

## Your named modules, mapped to what exists

| What you called it | Module id | What it actually is | Status |
|---|---|---|---|
| Our own filters / score | `own` | layer0/0b/0c score a coin on deployer quality, convergence, buyer quality, momentum. Passes get routed straight into Core1/Core2/Compound by `router.py`; only coins that fit nowhere sit on the Own Analysis watch-list. | Live |
| Copy-trading (leaderboard) | `leaderboard` | pump.fun / StonkFun leaderboard wallets + our own tracked list buying. Convergence (multiple tracked wallets in the same coin) escalates it. | Live |
| FOMO copy-trading | `fomo_followers` | Your Fomo follow-list's buys + their written theses. | Live |
| FOMO new-world trading | `fomo_new` | New high-balance, positive-PnL wallets not yet on your list — the coins THEY just bought. Starts once Fomo credits are live. | Live, gated on Fomo credits |
| Moonshot identification | `core1` / `core2` | Early-entry moonshot books. Core2 = first ~15 min, under $200K mcap, 100x+ claim. Core1 = up to 2h, under $1M mcap, 10x-50x claim. Entry comes from Own Analysis, Radar, or Layer 17's real-time launch screen. | Live |
| Compound aggression | `compound` | Daily-profit book: takes 2x-10x quickly, compounds daily. Widest intake — scalper, momentum lane, score bands, radar ladder pops, listing pops. | Live, but currently net-negative on paper (see exit-tuning note below) |
| Catalyst / virality side | `virality` (layer18, new) | Watches news/Trends/Reddit/HN + any influencer or Telegram feeds you add. Matches a trending name to a DORMANT coin (age >= 12h — this is what makes it different from the radar's news-jack, which only looks at brand-new coins). Flags HEADS-UP (named, hasn't moved) vs CONFIRMED (named + waking up). | Shadow-mode: alerts + logs outcomes, doesn't route into a book yet. 1 day of data so far — too early to trust. |
| Sniper / insider wallet detection | `sniper_wallet` (layer19, new) | A wallet with a *proven* track record of buying before a coin runs (built from our own graduation history by `wallet_edge_builder.py`, bots excluded) buys a brand-new coin early. | Shadow-mode. First pass tonight: 41 candidate wallets, but each only seen 5-11 times — needs several more nights of `wallet_edge_builder.py` before any wallet is trustworthy. |
| Dev launch copy-trading | `dev_list` | A developer from your elite/good list just launched. | Live |
| Dev intel | `dev_intel` | Not a finder — a per-coin overlay (dev holding %, wallet age, track record) shown on every alert/buy regardless of which finder found it. | Live |
| Fast lane | `fast` | Real-time launch entries (layer17), split by speed into fast_sprint (<=120s) and fast_core (<=20min), each with its own exit profile. | Live — this is also where most of today's paper-trade volume comes from. |

## How a coin actually moves through the system

1. A finder sees a coin (own-analysis score, radar catalyst, dev-list match, leaderboard wallet buy, etc.)
2. `router.py` looks at ONE thing per finder-type: how early is it, how big is the cap, how strong is the
   evidence (band A/B, moonshot_ok, etc.) — and returns exactly one lane: `core1`, `core2`, `compound`, or
   `watch` (fits nowhere, logged but not traded).
3. The book it lands in opens a paper position at that book's stake size, and manages it with its OWN
   exit rules (`executor/exit_rules.py` for the polling-layer books, `executor/paper_ledger.py`'s
   `EARLY_RULES` table for Layer 17 real-time entries — tightened today, see below).
4. Layer 18 and Layer 19 are deliberately NOT wired into the router yet — they alert and log outcomes
   only, so we can prove or kill them with real numbers before they're allowed to open a position.

## What's proven vs. what's still being tested (honest status, 20 Oct)

- **No edge found yet** in graduation-time features alone (998-coin study): every buy-the-graduation
  strategy tested came back below breakeven after costs.
- **Exit logic was losing money unnecessarily** — found and fixed today: a 90-minute "dead coin" flush
  was cut to 45 minutes (data showed coins were already worse off at 90min than 45min, every time), and
  the trailing-stop was giving back 64% of a peak on average vs. a configured 30-55% — tightened, though
  the root cause (price gaps between polls on thin coins) isn't fully solved.
- **Layer 18 (virality)** and **Layer 19 (sniper wallets)** are the two open, unproven leads. Both are
  running in shadow mode, logging real outcomes, specifically so we get an honest yes/no on each rather
  than a guess.

## What "finishing the structure" means from here

The architecture above is already how the code is organized — this document just makes it visible in one
place instead of spread across 15+ files. The real open work is not more modules; it's finishing the two
unproven ones (virality, sniper wallets) with enough real outcome data to trust or kill them, and getting
Compound/Fast back to a positive paper P&L before any of this touches real money.

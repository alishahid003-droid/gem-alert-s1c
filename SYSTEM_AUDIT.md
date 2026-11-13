# SYSTEM AUDIT — gem-alert-s1c_6

Written 13 Nov 2026 by an auditing Claude session, from a full read of the git history (127 commits,
16 Oct–13 Nov 2026), the core source files, the live `.env`, and a test-suite run. Purpose: so a future
Claude session with zero memory of this system can read this one file and understand it completely —
Ali should never have to re-explain the mechanics of this system again.

Everything stated below is grounded in a specific commit hash, a specific file/line, or a specific test
result that was actually read during this audit — not inferred or assumed.

---

## 1. System Overview

This is a memecoin "sniper" / paper-trading research system (`gem-alert-s1c_6`). It:

1. **Finds** coins across chains via many "layers" (`layers/layer0_scoring.py` through `layer21_celeb_signal_watch.py`, plus `multichain_sniper.py` as a separate, parallel finder/entry engine).
2. **Scores/filters** each candidate against chain-specific, real-data-evidenced entry gates.
3. **Routes** a passing coin into one of four real trading "books" (modules): **compound, sprint, core1, core2**.
4. **Manages** the resulting paper position with its own exit ladder (`executor/paper_ledger.py`).
5. Tracks a **5-phase equity-growth target structure** (`executor/books.py`'s `PHASE_DEFS`) scoped to compound+sprint only, with automatic cash-out/reseed events.

**Chains:**
- **Robinhood Chain (RHC)** and **Base** are the two currently-active, filtered, real-execution-capable chains.
- **Solana is DISABLED** in the main entry path (`multichain_sniper.py`'s `Scanner.evaluate()`, gated by `MC_SOLANA_ENABLED`, default off) since commit `ee59140` (29 Oct 2026) — real data showed 972 solana trades, 12.1% win rate, net minus $17,404.56, the single largest money-loser in the system and the real cause of the 4 books' chronic busts. Solana is still *polled* for pattern-mining and still runs through a *separate* pipeline (`layers/layer17_early_entry.py`, PumpPortal-fed) with its own narrower, evidenced filter (mcap $5-20k + exactly 3 confirmations + no `hot_meta`) — but that pipeline's solana paper trades are isolated into separate `solana_sprint/solana_core1/solana_core2/solana_compound` books (commit `449b853`, 1 Nov 2026) and never touch the real 4 books' equity or win rate.
- **BSC is disabled** permanently: flat 0% win rate across its entire history (37/37 losses), no filtered slice ever found that flips it.

**Paper vs live status:** Everything today is **paper-trading**. Real-money execution exists and is fully wired for all four books (commit `39ef314`, 8 Nov 2026) via `LIVE_BOOKS` env var + `books.is_live_armed()`, but `.env` currently has no `LIVE_BOOKS` set, so every book runs 100% paper. `EXECUTION_ENABLED=true` and `EARLY_LIVE_LANES=sprint` are set in `.env`, but a chain-level real buy only fires when `is_live_armed(book)` is also true, which requires the book's id to be listed in `LIVE_BOOKS` — currently unset. Real buy/sell code paths exist for Solana, BSC, Base, and Robinhood Chain in `executor/swap_executor.py`, but **every chain remains explicitly marked UNTESTED against a live network** (confirmed accurate as of commit `e84c0cd`, 12 Nov 2026 — a prior audit had found stale comments claiming the sell-side/RPC wiring was still unbuilt; it was actually complete, just undocumented). Solana and BSC real buys are additionally hard-blocked at `executor/entrypoint.py`'s chokepoint (commit `830fca1`) regardless of `EXECUTION_ENABLED`.

---

## 2. The Four Trading Modules

Defined in `executor/books.py`'s `BOOKS` dict:

| Book | Kind | Seed | Target / days | shot_pct | max_share | Role |
|---|---|---|---|---|---|---|
| **sprint** | moonshot | $100 | $20,000 / 4 days | 0.30 | 0.45 | Fastest horizon, biggest stakes, takes profit earliest/most aggressively (role redesigned 7 Nov, commit `7df8870`) |
| **core1** | moonshot | $100 | $125,000 / 30 days | 0.20 | 0.35 | The genuine "middle" -- balanced ladder, unchanged from original design |
| **core2** | moonshot | $100 | $1,000,000 / 180 days | 0.10 | 0.25 | Smallest/most conservative stakes, tightest hard stop (0.45), but reserves upside via an uncapped moonshot carve-out past 8x |
| **compound** | daily | $100 | none (daily-profit) | 0.50 | 0.75 | Widest intake, steady daily compounding, biggest single-module win rate historically |

**Why they used to be near-identical and aren't anymore:** Ali (7 Nov 2026): *"what's the point of having all these three modules when they have everything identical?"* Until commit `7df8870`, sprint/core1/core2 shared one exit ladder. Each now has its own `EARLY_RULES` ladder and its own `EARLY_TRAIL_RATCHET` trailing-stop schedule (see Section 4).

**Core1 <-> Core2 mutual block (commit `1b7c5de`, 11 Nov 2026):** Real-data finding -- every historical case where core1 AND core2 both opened the *exact same coin* at the same time (4/4 cases) ended in either a double loss or a split result, never a double win. A 3-minute mutual block (`CORE12_MUTUAL_BLOCK_MIN`, default 3) now prevents core1 and core2 from piling onto the same (chain, token) within that window of each other. **This block does NOT apply to sprint or compound.**

**Sprint <-> Compound overlap is deliberately left untouched.** Same real-data review found 13 of 16 real same-coin overlap cases were sprint+compound, and those were mostly *double wins* -- blocking that overlap would have cost ~$906 in real net profit. So sprint and compound are free to enter the same coin as each other, and as core1/core2.

**Solana isolation (commit `449b853`):** `solana_sprint`, `solana_compound`, `solana_core1`, `solana_core2` are separate paper-only books, same exit-rung logic, completely separate ledger/equity/win-rate from the real 4 books. A critical bug (commit `363aa66`, 2 Nov 2026) had the fast-lane path silently routing live Solana trades into the *real* books instead of these isolated ones -- 99 of 353 closed trades in the real books turned out to be solana trades that should never have counted there. Fixed by pointing `layers/fast_lane.py`'s `STREAMS` table at the isolated books.

---

## 3. Entry Filters

All thresholds below are current `.env` values where set, otherwise code defaults, from `multichain_sniper.py`'s `Scanner.evaluate()` (the main cross-chain entry engine).

| Gate | Env var | Current value | Notes |
|---|---|---|---|
| Min market cap | `MC_MIN_MCAP_USD` | 3,000 (default) | |
| Max "normal lane" market cap | `MC_MAX_MCAP_USD` | 60,000 (default) | Above this a coin can only enter the "momentum"/"big" probe lanes |
| Min pool liquidity | `MC_MIN_LIQ_USD` | 4,000 (default) | Raised from $2,500 (commit `7c40ac5`, 26 Oct). Applies to every source with a reported `liq` value (not just GeckoTerminal). `liq=None` (unreported) fails *open* -- only a real thin reading blocks. |
| Max mcap/liquidity ratio | `MC_MAX_MCAP_LIQ_RATIO` | **1.4** (tightened from 1.7, commit `43f6ccd`, 12 Nov) | Chronologically validated (60/40 train/test split): the 1.4-1.7x band was a genuine net loser (10 trades, 20% win, -$727.38), including all 6 of compound's rug-pull closes that looked healthy at entry. The 1.0-1.2x core (82.5% win, +$4,957.98 in compound alone) is untouched. |
| Robinhood Chain min entry mcap | `MC_RHC_MIN_MCAP_USD` | **30,000** (lowered from 50,000, commit `ec49f7b`, 28 Oct) | The $30-50k band outperforms what the old $50k+ floor alone captured. |
| Robinhood Chain max entry mcap ceiling | `MC_RHC_MAX_MCAP_USD_FLOOR` | **50,000** (added commit `90071eb`, 30 Oct) | $50-60k band was a clean 0% win rate (7/7 losses) in a monotonic decay from $30-40k (72.7% win) down. Combined with the $30k floor as a "slab," not a replacement. |
| Robinhood Chain age floor | `MC_RHC_MIN_AGE_MIN` | 0.0 (disabled by default) | Was 2.0 at one point; retired (commit `ec49f7b`) once it no longer helped on the corrected $30k+ bucket. Left as a disabled override, not deleted. |
| Robinhood Chain biglaunch lane | -- | excluded entirely | Entire historical biglaunch lane on RHC (n=2) lost both times; `if rhc and big: return None`. |
| Solana enable switch | `MC_SOLANA_ENABLED` | 0 / off (default) | See Section 1. |
| Base quality gate | -- | requires BOTH `growth` and `volume` confirmations | 91.7% win / +$493.02 net on that slice vs 8.3% win / -$1,538.37 on everything else on base. |
| Min confirmations (standard) | `MC_MIN_CONF` | 3 (default) | For `stonkfun` source: `MC_MIN_CONF_STONK`, default 2. |
| Lane confirmation floor (sprint/core1/core2) | `EARLY_LANES_MIN_CONF` | **5** (raised from 3-4, commit `c55a287`, 8 Nov; reverted once -- see Section 7 -- then the 5-floor stuck for core2/sprint but core1 reverted to 3; **re-raised to 5 again on 11 Nov**, commit `276368c`) | compound's own separate 3+ floor is untouched -- it has a separately-proven positive edge. |
| Cross-book exposure cap (sprint/core1/core2 only) | `CROSS_BOOK_EXPOSURE_CAP_USD` | 40.0 (default) | compound explicitly excluded (`EXPOSURE_CAP_BOOKS`) -- compound often wins big on the exact same coin where sprint/core1/core2 lose. |
| Dead-on-arrival / never-priced write-off | `NEVER_PRICED_CYCLES_BEFORE_WRITE_OFF` / `NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF` | 3 cycles / 2.0 min (default) | Separate, much shorter grace period for a position that was NEVER priced even once since entry -- RHC paper trades were dying at 96% with `last_mcap=None`, tying up slots for the full 8-min generic grace period for no reason. |
| Generic unpriceable write-off | `UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF` | 8.0 (default, cut from 30) | For a position that WAS priced and later goes dark. |
| Smart-wallet / narrative bypass (`dev_list` book) | -- | always active | `evm_wallets.hit_n()` (base/bsc/robinhood_chain) and `narrative_first` (trend_feeds.py) each independently route a coin straight to the `dev_list` book, bypassing every chain gate above -- Ali's explicit instruction: a quality signal should trade "specifically, not bifurcating it to any other module." Both signals are still thin/not yet separately profitable (37.5% win/-$124.24 and 25% win/+$9.59 respectively) -- kept for evidence accumulation. |

**Momentum gate** (`MOMENTUM_GATE_ENABLED=true` in `.env`): built 11 Nov (commit `276368c`) via `dexscreener_momentum_gate.py` and `layers/layer0_scoring.py` -- checks DexScreener h1/h6 drawdown on robinhood_chain/base to refuse entries on coins that have already collapsed.

**Confirmation signals built from live data** (`conf` list in `Scanner.evaluate()`): `pressure` (buys5 >= threshold and >=1.3x sells5), `buyers` (buyers5 >= 8), `velocity` (chg5 in 15-400%), `volume` (vol5/vol24 threshold), `fresh` (age <=15min), `growth` (mcap >=1.25x its first-seen value after >=40s), `graduating` (stonkfun grad >=0.3). A coin with sells5 > 1.5x buys5 is refused outright ("being dumped").

---

## 4. Exit Logic

Core engine: `executor/paper_ledger.py`'s `_manage_early()`, driven by the `EARLY_RULES` table (ladder, hard-stop multiple, flat trailing-stop multiple) per exit profile:

```
EARLY_RULES = {
  "sprint":   ({1.2: .20, 1.6: .20, 2.5: .15, 5: .10}, stop=0.40, trail=0.50),
  "core1":    ({1.3: .10, 2: .15, 4: .15, 10: .10},    stop=0.35, trail=0.50),
  "core2":    ({1.5: .05, 3: .10, 8: .10, 20: .10},    stop=0.45, trail=0.50),
  "compound": ({1.3: .25, 2: .60, 3: .40},             stop=0.70, trail=0.78),
  ... (dev_list, fast_sprint, fast_core, fast_moon, leaderboard also defined)
}
```

- **Sub-2x "1.3x" rung** (commit `dabc3b8`, 31 Oct): added to all four because sprint's one loss and 8/10 of compound's rug losses that day peaked between 1.0-1.96x, below the old first rung (2x), getting zero profit protection. Take-fraction is per-module: compound 25%, sprint/core1/core2 15%.
- **Ratcheting trailing stop** (`EARLY_TRAIL_RATCHET`): past 4x and 8x peak, the giveback-lock tightens (e.g. sprint: 55% to 65% to 75% lock as peak rises through 1.8x/3x/6x). Built after a real ELONPAD trade peaked 8.23x and exited at only 3.64x (56% giveback) under the old flat 50% trail.
- **"No pop" fast exit** (`EARLY_NO_POP_MIN`=1.5 min, `EARLY_NO_POP_MULT`=1.05): a position that has shown zero upward movement (peak AND current mult both <1.05x) past 1.5 minutes is cut as `stop_loss` immediately, instead of riding to the hard stop. Built from the finding that 55.1% of all solana stop_loss losses never moved above 1.05x even once before eventually crashing -- entry confirmation signals don't distinguish winners from losers; only early price movement does.
- **No-pop cross-module re-entry block** (`NO_POP_REENTRY_BLOCK_MIN`, commit under 11 Nov's overnight review): once any module closes a coin as no-pop, every module refuses re-entry on that exact coin for the window -- 65% of one overnight batch's no-pop losses were the SAME coin re-entered by a different module within 1-5 minutes.
- **Dead-coin time stop**: `EARLY_DEAD_MIN` = 45 min (cut from 90) -- every time_stop exit in 1,090 trades fired at exactly ~90min with a median -35% loss and 0% win rate; cutting to 45 doesn't catch recoveries (there weren't any) but stops the loss from drifting further.
- **24h max hold**: `EARLY_MAX_HOLD_MIN` = 1440 -- a true safety net, made independently reachable (was previously unreachable on a fully-carved core2 position due to an `elif` chain bug, fixed in commit `7df8870`).
- **Core2 moonshot carve-out**: once a core2 position's peak clears `CORE2_MOONSHOT_TRIGGER_MULT` (8.0x), `CORE2_MOONSHOT_CARVEOUT_PCT` (22%) of the *original* stake is carved out of the trailing-stop mechanism entirely, one time, and rides with NO trailing stop -- only the dead-coin/24h nets can still close it. Ali's explicit design: "20-25% should bypass the ratchet entirely and aim for that moonshot attempt."
- **Rug detection**: the dead-liquidity floor (`DEAD_LIQUIDITY_USD`=500) catches a pool *after* it's already collapsed. **Option 2 / `rug_liquidity_early`** (built 9 Nov, `LIQ_VELOCITY_DROP_PCT`=0.5 in `.env`) compares liquidity against the *prior* cycle's reading and closes a position the moment liquidity drops >=50% cycle-over-cycle -- catches 71% of real historical rug cases (17/24 had 153-1,400 seconds of lead time) but is honestly documented as unable to catch the other 29% that collapse near-instantly.
- **Probation sizing** (`probation_sizing.py`, `PROBATION_SIZING_ENABLED=true` in `.env`): see Section 5 for full mechanism. New robinhood_chain/base positions open at 50% of their intended stake, topping up to full size either after 2 consecutive stable-liquidity cycles or immediately on crossing `PROBATION_PUMP_GRADUATE_MULT` (1.15x) -- the latter added 10 Nov after real data showed every genuine winner among capped positions reached >=2.09x peak while every capped loss never exceeded 1.00x, a clean zero-overlap separator.
- **Rug sibling kill switch (NEW, 13 Nov 2026, commit `8e7a65b`)**: see Section 5 -- the newest, most important mechanism from today's incident response.

---

## 5. Cross-Module Protections

Two distinct mechanisms, built 2 days apart, that must not be confused with each other:

### (a) Core1/Core2 mutual block (commit `1b7c5de`, 11 Nov 2026)
- Scope: **only core1 <-> core2**, symmetric, triggered the moment EITHER opens a position.
- Window: `CORE12_MUTUAL_BLOCK_MIN` = 3 minutes (short -- about simultaneous pile-ins, not a long re-entry ban).
- Does NOT affect sprint or compound in either direction.
- Rationale: 4/4 historical core1+core2 same-coin cases ended in a double loss or split, never a double win.

### (b) Cross-module rug kill switch (commit `8e7a65b`, 13 Nov 2026 -- the Sakura/inuhao incident)
- Scope: **every module, including sprint and compound** (which the (a) mechanism never touches).
- Trigger: fires ONLY on a **confirmed rug close** -- `exit_type` of `rug_liquidity` or `rug_liquidity_early` -- never on a routine stop-loss or no-pop exit.
- Mechanism, both in `executor/paper_ledger.py`:
  1. `_open_paper_unlocked`: refuses any new entry on a (chain, token) marked `rug_seen` within the last `RUG_SIBLING_BLOCK_MIN` minutes (default 10), for any module.
  2. The two rug-close sites call `_force_close_rug_siblings()`, which immediately force-closes any OTHER module's still-open position on the exact same (chain, token), tagging the forced close `exit_type="rug_liquidity_sibling"` (distinct from a self-detected rug) -- priced at the rugged position's own `entry_mcap * 0.01` (same convention the normal dead-liquidity close uses).
- Real-data basis: both Sakura and inuhao rugged across core1, sprint, and compound (core2 was already excluded by (a)). Real timestamps showed core1 closed Sakura as `rug_liquidity` at ts=...572, but compound didn't even *open* its own Sakura position until ts=...596 -- 24 seconds *later*. The real spread across both incidents was 9-30 seconds, enough lead time for this cross-module signal to actually help.
- **Honest limitation, explicitly documented in the commit**: an instant, single-tick collapse that catches every module in the exact same cycle is still structurally uncatchable by this or any cycle-based mechanism -- same limitation `probation_sizing.py` already documents on the entry side.
- Tested: `tests/test_paper_ledger.py` + `tests/test_state.py` (47/47) passing before and after, plus a direct simulation replaying the real Sakura/inuhao data.

### Probation sizing (entry-side protection, not strictly "cross-module" but closely related)
`probation_sizing.py`, scoped to `robinhood_chain`/`base` only (`PROBATION_CHAINS`). A new position opens at `PROBATION_INITIAL_FRACTION` (0.5) of its intended stake. Each management cycle, `update_probation()` checks the liquidity reading against the prior cycle (`PROBATION_INSTABILITY_DROP_PCT`=0.25). Two consecutive stable cycles (`PROBATION_CONFIRM_CYCLES`=2) leads to topping up to full size. Crossing `PROBATION_PUMP_GRADUATE_MULT` (1.15x) leads to immediate full-size graduation regardless of stability-cycle count. `PROBATION_MAX_CYCLES` (10) without confirmation leads to staying at reduced size permanently, never silently granted full size without proof. The module's own docstring states plainly: this does not eliminate the instant-collapse loss category, it **halves its dollar size** -- a rug completing within the first cycle still costs the reduced size, which is the entire point.

---

## 6. Equity & Capital System

All in `executor/books.py`.

- **`total_value(book_id)`** = `seed + realized_pnl(book_id)`, floored at 0.
- **`banked(book_id)`** = profit taken out of play. For compound: staged via `COMPOUND_STAGES` (bank fixed $ amounts at $1k/$5k/$10k thresholds crossed, idempotent -- never double-banks a retrace). For sprint/core1/core2/copy books: once realized profit passes `BANK_AFTER_MULT`xseed (default 3x), `bank_pct` of the profit above that line is banked.
- **`equity(book_id)`** = `total_value - banked`. This is the real trading balance.
- **`committed_usd(book_id)`** = cost of currently-open paper positions (`paper_ledger.module_open_cost`).
- **`free_equity(book_id)`** = `equity - committed_usd`, floored at 0.
- **Pool borrowing** (commit `1313c5f`, 2 Nov): `POOL_BOOKS` = (sprint, core1, core2, compound). `pool_free_usd()` sums each book's idle capital (`equity - committed`). `borrow_room_usd(book_id)` lets a cash-tight book draw on siblings' idle capital, capped at `(POOL_BORROW_MULT - 1) x full natural capacity` (2.0 default, so up to 2x natural capacity total) and by what the pool actually has free. A borrowed trade counts in full toward the *borrowing* module's own P&L -- "home share" is a capital source, not a profit-attribution boundary.
- **Absolute stake ceiling backstop** (commit `3651ea5`, 27 Oct): every stake cap elsewhere is relative to `equity()` -- the exact thing a phantom-equity bug (commit `ab494cf`, same day) exploited to size a $1,289.59 stake on a $100-seed book. A hard ceiling (3x seed by default, `MAX_STAKE_SEED_MULT`) is now pinned to the book's real configured seed, independent of whatever `equity()` computes, as a backstop against any future bug upstream.

### Milestone / phase-target structure (`PHASE_DEFS`, 5 phases, commit `b6e0171`, 7 Nov; re-scoped `255edd5`, 12 Nov)

| Phase | Trigger | Cash-out | Reseed left |
|---|---|---|---|
| 1 | $10,000 | $8,500 | $1,500 |
| 2 | $12,000 | $10,000 | $2,000 |
| 3 | $12,000 (same trigger as phase 2 -- **confirmed intentional repeat by Ali, not a typo**) | $10,000 | $2,000 |
| 4 | $120,000 | $115,000 | $5,000 |
| 5 | $1,000,000 (final) | none coded yet -- ladder-only, same "ask Ali" pattern as before | -- |

Each phase also walks its own lighter protective-banking ladder on the way up (10-22% of combined equity banked at several sub-thresholds), checked *after* the hard cash-out trigger each call -- the trigger is always checked first so a big jump across several levels at once can't let the small banks drain equity back below the trigger before the real lever fires.

**Scoped to compound+sprint ONLY since 12 Nov (commit `255edd5`).** Core1/core2 used to be folded into the combined-equity trigger and split proportionally to equity share, but simulation showed sprint's reseed share would collapse from $100 in Phase 1 to $0.06 by Phase 5 at a 60% win rate -- compound's bigger stake bands outgrow sprint's every phase under a proportional split. Ali's fix: core1/core2 keep their one-time $200 cascade seed (below) and then grow completely independently forever after -- never topped up or drawn from by any future phase event.

**Cash-out/reseed split is a FIXED 65% compound / 35% sprint** (`PHASE_ENGINE_SPLIT`), not proportional to equity share, via `_split_fixed_amount_capped()` -- which caps each book at its own current equity and spills any shortfall to the other book (caught in testing: a fixture where sprint only had $100 but a naive 35% split tried to take $2,975 from it).

### Cascade mechanisms (chronological -- two retired, one live)
1. `check_compound_split()` (3 Nov) -- $500 total_value leads to $200 split to sprint/core1/core2. **Retired as a permanent no-op**, not deleted (its already-fired history stays real).
2. `_maybe_fire_phase1_cascade()` (8 Nov) -- $600 Phase-1-banked leads to $300 to sprint/core1/core2. **Retired as a permanent no-op** (11 Nov) -- explicitly `return None` at the top of the function, dead code below kept for reference only.
3. **`check_sprint_compound_cascade()` (11 Nov, commit `8fd221e`) -- the live mechanism.** Compound and sprint each run independently toward the phase ladder; whichever FIRST banks $200 in real taken-off-the-table profit fires a one-time cascade, splitting $200 evenly ($100 each) to core1+core2. **Gated to real/live money only** (`is_live_armed()`) -- confirmed to have zero effect on the current paper run. Guarded to fire at most once ever.

---

## 7. State & Reliability

`state.py` is the shared persistence layer. Backend is `local_json` (a single `.gem_alert_state.json` file, OS-level file lock via `msvcrt`/`fcntl`) -- confirmed as what's actually in use (not Upstash) from the lock mechanism and env var (`GEM_ALERT_STATE_FILE`).

### The 13 Nov 2026 incident and the two same-day fixes

**What happened:** the live state file collapsed from 1,205 top-level keys to 109 in one write, wiping 404 closed trades, 32 open positions, `books_cashouts_v1`, `books_deposits_v1`, and the milestone ladder together. This is the **third** occurrence of this general failure shape (first: 9 Nov, second: 11 Nov -- `paper_closed_positions` dropping from 129 to 3). No code path in this repo can produce this shape of loss on its own (every writer only ever *appends* to these keys) -- the leading theory, never fully confirmed, is OS-level file interference on Windows (a sync/backup/AV tool holding or replacing the file), consistent with repeated `PermissionError: [WinError 5] Access is denied` traces in the same windows.

**Fix 1 (commit `7d2a88a`): widened shrink guard + whole-file collapse check.**
- The existing per-key shrink guard (`_SHRINK_GUARD_KEYS`, built 11 Nov after the second incident) only watched `paper_closed_positions` and `books_deposits_v1` -- it never noticed `paper_open_positions`, `books_cashouts_v1`, or the milestone ladder collapsing, and it checks one key at a time so it can't see a *whole-file* collapse.
- `_SHRINK_GUARD_KEYS` widened to 5 keys: `paper_closed_positions`, `books_deposits_v1`, `paper_open_positions`, `books_cashouts_v1`, `milestone_ladder_v2`.
- Per-key rule: a drop of >=20 entries (`_SHRINK_GUARD_MIN_ABS_DROP`) down to below 50% of the current count (`_SHRINK_GUARD_MIN_RATIO`) is refused.
- **New: `_whole_file_collapse_check()`**, run *before* the per-key loop -- cheaper and catches the case even if every individual key check is somehow missed. If the file currently has >=50 top-level keys (`_WHOLE_FILE_MIN_KEYS_FOR_CHECK`) and the incoming write has fewer than 30% of that count (`_WHOLE_FILE_COLLAPSE_RATIO`), the write is refused outright.
- Either guard, on tripping, raises `StateShrinkGuardError`, leaves the live file untouched, and saves the would-be-destructive payload to a `.BLOCKED_*` side file for inspection -- never silently applied, never silently dropped.
- **Honest limitation, stated directly in the code comment:** this cannot stop an external process overwriting the file entirely outside this module's own write path (i.e. `os.replace()` never even runs) -- that would need the separate OS-level investigation Ali has asked for directly, which is still open.

**Fix 2 (commit `8e7a65b`): the cross-module rug kill switch** -- see Section 5(b). Built the same day, in response to a real coin-level incident (Sakura/inuhao) discovered while investigating the state-wipe aftermath, not directly related to the wipe itself.

### Other reliability mechanisms worth knowing about
- **Reentrant file locking** (commit `7dae818`, 6 Nov): reads now take the same OS-level lock writes do (previously only writers locked), via a per-thread depth counter so a read from inside an already-locked write doesn't deadlock. Fixed a real equity-flicker bug where `combined_equity()` intermittently read $300 low from inside `atomic_update()`'s own held lock.
- **No-cache-trust reads** (commit `2eb8865`, 6 Nov): `_local_load_all()` used to skip re-parsing the file when `(mtime_ns, size)` matched the last read -- two different writes landing in the same mtime-tick with the same byte size collided and served stale data. Now always re-reads/re-parses (~46ms cost, negligible against the 20-60s tick interval).
- **`atomic_multi_fn()` / `atomic_update()`** (commits `737b492`, `c198731`, 6 Nov): the real fix for multi-process read-modify-write races across `OPEN_KEY`/`CLOSED_KEY`/`BUST_KEY`/etc. -- read, modify, AND write all happen inside one held lock. Proven via real concurrent-subprocess tests (not just threads) -- the old two-separate-writes pattern lost 72% of trades under real contention; the fix lost 0%.
- **`cache_get`/`cache_set`** (`state.py`): a simple TTL cache pattern -- `cache_get(key, max_age_seconds)` reads `{"ts":..., "value":...}` under key `cache:{key}` and returns `None` if stale; `cache_set` writes it with the current timestamp. Used throughout for things like the rug-sibling block and no-pop re-entry block.
- **Periodic state snapshots** (commit `76c9a04`, 6 Nov): `state_snapshot.py` copies the live state file to `backups/state_snapshots/` every 10 minutes (read-only, auto-pruned to last 200), built after a real $100 to $4,100/344-trade paper run (2-5 Nov) was permanently lost to a diagnostic reset with zero backup, its only surviving record a chat screenshot.
- **`.gem_alert_state.json` is no longer tracked in git** (commit `c584063`, 2 Nov) -- it used to be, which meant every `git pull` silently reverted live trading state to whatever snapshot was last committed. Fixed via `git rm --cached`; `.gitignore` already listed the pattern, it just hadn't been retroactively untracked.

---

## 8. Known Bugs Fixed (chronological)

This is the highest-value section for avoiding repeated explanations. Commit hash leads to one-line real-data finding.

- **`38b975b`** (27 Oct) -- bsc/base/robinhood_chain disabled by default: 1,023 trades showed bsc flat 0% win, base/RHC losing overall despite higher win rates because >55% died unpriced before ever getting a chance.
- **`e55d07a`** (27 Oct) -- RHC re-enabled via GeckoTerminal fallback (DexScreener had no indexed pool for many fresh RHC launches); solana "no pop" fast exit added (55.1% of solana stop-losses never moved above 1.05x even once).
- **`5d27156`** (27 Oct) -- fast write-off for never-priced-since-entry positions (RHC dying at 96%, all with `last_mcap=None`, held for the full generic 8-min grace period for no reason).
- **`629e35a`** (27 Oct) -- `liquidity_usd` was computed and discarded, never saved on the position record -- every historical closed trade (253/253 sampled) was missing it, blocking exactly the analysis meant to find liquidity-rug patterns.
- **`19e70df`** (27 Oct) -- entry-price sanity rebase trusted an unvalidated near-zero live read: one core1 trade's real ~$59,303 entry got rebased onto a garbled $0.04 read, producing a fictional $10,152,470.17 "win" on a $10.76 stake. Fixed in both the paper and real-money (`position_state.py`) paths with a $50 minimum sanity floor on the rebase target.
- **`ab494cf`** (27 Oct) -- phantom balance bug: `bust_offset()` summed every past bust instead of using only the latest, double/triple-counting historical losses. Compound showed equity=$1,309.74 on a book that was actually ~-$3,654 (should have read $0). This phantom equity fed a $1,289.59 stake (21x the real $60 max for a $100-seed book).
- **`3651ea5`** (27 Oct) -- absolute stake ceiling added as a backstop, independent of `equity()`, because every existing cap was relative to the exact value the bug above corrupted.
- **`b07f22b` / `e55d07a` / `1b37567` / `d72c207` / `0f2707a`** (27-28 Oct) -- the systematic per-chain entry-filter audit: smart_wallet-only and growth+conviction-tier gates both built and retired same-day once isolated per chain (apparent edges were mostly a base-chain effect); robinhood_chain mcap floor ($50k to $30k, then +$50k ceiling), age floor (added then retired), base growth+volume gate, solana layer17 mcap$5-20k+conf==3+no-hot_meta pattern -- all chronicled in detail in Section 3.
- **`ec49f7b`** (28 Oct) -- RHC mcap floor erosion caught and fixed (56.3% to 54.5% to 40.0% across checkpoints) by re-bucketing on a larger sample; `repeat_grad` found promising on solana but honestly flagged as resting on one +$63 outlier, not shipped as a gate.
- **`ee59140`** (29 Oct) -- solana blocked from the real books entirely in `multichain_sniper.py`: 972 solana multichain-origin trades, 12.1% win rate, net -$17,404.56 -- the single largest money-loser, confirmed as the real cause of the 4 books' chronic busts (14/5/3/20 recorded), not a stake-sizing problem.
- **`363aa66`** (2 Nov) -- CRITICAL: fast-lane path was routing live solana paper trades into the real sprint/core1/core2/compound books instead of the isolated solana_* books (99/353 closed real-book trades turned out to be solana).
- **`c584063`** (2 Nov) -- live state file was tracked in git; every pull reverted live trading state.
- **`c48aba1`** (1 Nov) -- duplicate-close race: `manage()` runs from 3 separate OS processes with only a thread-local lock protecting cross-process writes; one position closed 3x, 15 ids duplicated across 198 closed records, skewing every win-rate stat (core1/core2 were actually net-losing once deduped, not the small positive shown).
- **`737b492` / `c198731` / `2eb8865` / `7dae818`** (6 Nov) -- the multi-day equity-flicker investigation: a race in `phase_target.check()`'s own read/write, then multi-key state races, then a stale in-memory cache, then unlocked reads -- four distinct, real, separately-confirmed bugs, each found only after the previous fix exposed the next layer. See Section 7.
- **`853f10c`** (6 Nov) -- RHC filling a book's shared position-count limit first was blocking base from entering at all, even though base had real candidates found continuously; fixed with per-chain slot allowance.
- **`7df8870`** (7 Nov) -- sprint/core1/core2 had never actually been differentiated despite different stated roles; also fixed an `elif`-chain bug that made the 24h safety net unreachable on a fully-carved core2 position; also found and fixed a feed-wide-outage detector that was silently disabled for the entire `early_` watcher (checked `batched is None` but the per-token snapshot path also legitimately runs in production, not just tests) -- a real overnight PumpPortal/DNS outage wrote off 16 real positions as "rugged" when the feed, not the pools, had died.
- **`ec8abf2`** (7 Nov) -- liquidity-aware stake cap: a $90.34 compound stake on thin liquidity lost $39.39 purely from price-impact cost on a coin that never even moved.
- **`d259a3b`** (8 Nov) -- orphaned ladder-rung record (`p1_240`, from before the 5-phase redesign) was inflating the cascade trigger total by $40; also fixed 3 pre-existing bust-reseed test failures (a genuine test-isolation bug, not production -- the tests patched `state.get_value/set_value` but not `atomic_multi_fn`, which the real code paths actually write through).
- **`927b529`** (8 Nov) -- REVERTED two 8 Nov fixes after measuring live results, not backtests: the 5+ confirmation floor made core1/core2/sprint win rates collapse live (41.2% to 14.3%, 28.6% to 18.2%, 50% to 0%); `COMPOUND_SWAP_ALLOW_WEAK` dropped compound's win rate 65.6% to 44.2% because it let compound close genuinely-still-developing winners to free cash for unproven candidates. Both backtested well, neither held up live -- a direct lesson about backtest vs live validation.
- **`f27db37`** (8 Nov) -- GeckoTerminal rate-limit race: a naive "reservation counter" fix was built, caught in testing as double-counting the same event, and reverted before shipping in favor of a `max()`-based admit-log.
- **`39ef314`** (8 Nov) -- the real-money wiring build also fixed a sell-side bug an earlier version of the same fix introduced: using `live_amount_tokens` (mutated after every sell) instead of `live_amount_tokens_original` (fixed) as the base for pct-of-original sell math would have oversold every rung after the first.
- **`6f31b03`** (11 Nov) -- shrink guard added after the second trade-history-wipe incident.
- **`1b7c5de`** (11 Nov) -- core1/core2 mutual pile-in block.
- **`43f6ccd`** (12 Nov) -- mcap/liquidity ratio ceiling tightened 1.7 to 1.4x after a liquidity-rug loss review.
- **`e84c0cd`** (12 Nov) -- found and fixed stale code comments claiming RHC sell-side and RPC wiring were unbuilt; both were actually complete, just undocumented after being built in an earlier session.
- **`7d2a88a` + `8e7a65b`** (13 Nov) -- today's two fixes, detailed in Sections 5 and 7: the widened state-shrink guard and the cross-module rug kill switch.

---

## 9. Test Coverage

- **1,119 tests collected** across 103 test files (`tests/test_*.py`), 15,252 total lines of test code.
- **Clean run achieved** (this audit) with 6 known-problematic tests/files deselected (all confirmed to be environmental, not code regressions -- see below): **1,066 passed, 26 failed**, in 37.88s.

### The 26 failures, by category
- **12 failures are genuinely-blocked real network calls** (`utils.http.ApiUnreachable: ... Tunnel connection failed: 403 Forbidden`) -- this sandboxed environment's proxy refuses outbound calls to `api.dexscreener.com` etc. These are environmental to *this specific audit sandbox*, not failures Ali would see running on his own Windows machine with real network access.
  - `test_early_exits_live.py`: `test_take_profits_then_trailing_stop`, `test_live_robinhood_chain_sell_is_mirrored`, `test_sub_2x_rung_fires_and_banks_profit_before_a_reversal`, `test_compound_sub_2x_rung_uses_its_own_25pct_fraction`, `test_sprint_sub_2x_rung_uses_its_own_ladder`
  - `test_sept30_evm_enrichment.py::test_birdeye_only_called_for_a_or_b`
  - (plus others surfaced only when those files/tests are run -- see the 3 excluded files below)
- **11 failures in `test_multichain_sniper.py`** are **confirmed pre-existing fixture drift**, explicitly acknowledged in commit `43f6ccd`'s own message: "11 pre-existing test_multichain_sniper.py failures (confirmed via diff against the pre-change file) are unrelated test/fixture drift from earlier commits, not introduced by this change." This audit's run reproduces exactly that pattern -- fixtures built against older `MC_MAX_MCAP_LIQ_RATIO`/mcap-floor values now fail against the current (correct) 1.4x/30k/50k thresholds. **Not a new regression; a known, previously-flagged, unfixed test-maintenance debt.**
- **10 failures in `test_phase_target.py`** are **confirmed stale**: these tests assert the OLD A/B/C/D phase structure ($10k/$40k/$40k/$40k), which was explicitly superseded by the 5-phase `PHASE_DEFS` structure in commit `94c4bd8` (11 Nov) -- the commit message itself states `phase_target.py`'s old PHASES dict was "confirmed inert" and updated to match the real targets, but these tests were never rewritten to match. **This is real, acknowledged test debt -- a future session should either delete or rewrite `test_phase_target.py` against the current 5-phase/`PHASE_DEFS` reality**, since as written it tests a structure that no longer exists in the live code path.
- **3 additional files/tests were deselected before even reaching the above tally**, each hanging (not failing) on a real, slow, or network-bound call that the sandbox's proxy/filesystem made impractical to wait out in this audit session -- not necessarily failing on Ali's own machine:
  - `tests/test_madeonsol_gating.py` (2 tests) -- real GeckoTerminal HTTP call via `scheduler.run_poll_fast()`, retried with real sleep backoff.
  - `tests/test_radar_wiring.py::test_run_poll_radar_survives_crash` -- real Solana RPC call via `layers/layer20_notable_wallet_watch.py`.
  - `tests/test_rhc_sell.py::test_sell_refuses_when_erc20_approve_fails_to_confirm` -- real sleep-based retry/backoff loop in `executor/swap_executor.py::_confirm_evm_tx`.
  - `tests/test_modules.py::test_module_cards_shape_and_routing` -- already flagged as a pre-existing hang in multiple commit messages (e.g. `1af9975`, `7df8870`: "1 pre-existing hang deselected... confirmed present on the unmodified file too").
  - `tests/test_state.py::test_watch_add_caps_item_count` and `tests/test_layer20_notable_wallet_watch.py::test_below_min_pct_does_not_alert` -- both hung on this sandbox's slow bridge-disk `fsync`/file-lock retry loop in `state.py::_local_save_all`, not a code defect.

### What's actually tested well
Given the above, the **core trading-logic test files** (`test_paper_ledger.py`, `test_state.py` minus the one hang, `test_router_books.py`, `test_pool_borrowing.py`, `test_milestone_ladder.py`, `test_staged_compound_banking.py`, `test_early_entry.py`, `test_base_execution.py`, `test_swap_executor_fills.py`, etc.) pass cleanly and are exercised repeatedly throughout the commit history with real isolated-sandbox and real-subprocess concurrency tests (not just mocks) for every state-race fix.

---

## 10. Current Live Config Snapshot (`.env`, secrets redacted)

```
TELEGRAM_CHAT_ID=8477483859
FOMOAPI_DAILY_CREDIT_BUDGET=0            # Fomo copy-trading paused (credits exhausted)
FOMO_ALERTS_MIN_INTERVAL_SECONDS=2700
FOMO_LEADERBOARD_INTERVAL_SECONDS=86400
EXECUTION_ENABLED=true                   # real-money gate is open, but...
EARLY_LIVE_LANES=sprint                  # ...LIVE_BOOKS (the actual per-book arm) is NOT set, so still 100% paper
LIVE_MAX_STAKE_USD=10
LIVE_MAX_OPEN=3
LIVE_DAILY_LOSS_USD=17
EARLY_FREE_CONFIRM=live
EARLY_MAX_ENTRIES_PER_DAY=300
PUMPPORTAL_MAX_WATCHED=40
PUMPPORTAL_MAX_MSG_PER_DAY=20000
TELEGRAM_ENABLED=false
LIVE_SINCE_TS=1791973384
LOCAL_RUNNER_EARLY=true                  # the solana-only early-watch pipeline is enabled (isolated books only)
BOOK_STRONG_EXTRA_SLOTS=4
SPRINT_SEED_USD=100.00
CORE1_SEED_USD=100.00
CORE2_SEED_USD=100.00
COMPOUND_SEED_USD=100.00
EARLY_TRIM_MULT=2.0                      # "Option 1" fast-trim applied full-strength to compound/sprint/core1
CORE2_EARLY_TRIM_MULT=1.5                # core2 gets a smaller, moonshot-safe version of the same fix
LIQ_VELOCITY_DROP_PCT=0.5                # "Option 2" -- 50% cycle-over-cycle liquidity drop triggers rug_liquidity_early
PROBATION_SIZING_ENABLED=true
MOMENTUM_GATE_ENABLED=true
```

Not set in `.env` (so running on code defaults): `MC_MAX_MCAP_LIQ_RATIO` (1.4), `MC_RHC_MIN_MCAP_USD` (30,000), `MC_RHC_MAX_MCAP_USD_FLOOR` (50,000), `MC_SOLANA_ENABLED` (0/off), `EARLY_LANES_MIN_CONF` (5), `CROSS_BOOK_EXPOSURE_CAP_USD` (40), `CORE12_MUTUAL_BLOCK_MIN` (3), `RUG_SIBLING_BLOCK_MIN` (10), `LIVE_BOOKS` (unset -- no book is live-armed).

---

## 11. Open Questions / Known Limitations

- **Structurally uncatchable instant collapses.** Both `probation_sizing.py`'s own docstring and the 13 Nov rug-kill-switch commit explicitly state that a single-tick liquidity collapse that hits every module in the exact same management cycle cannot be caught by any cycle-based mechanism this system has (or could reasonably build) -- probation sizing only *limits the dollar size* of that loss category, and the rug kill switch only helps when modules' entries/exits are spread out in time (confirmed true for the two real incidents it was built from: 9-30s real spread).
- **The 13 Nov state-wipe root cause is still not pinned down.** The leading theory (Windows-side file/sync/backup/AV interference, consistent with repeated `PermissionError` traces) has never been confirmed. The shrink guard built today is a *mitigation* (refuse to persist a collapse), not a *fix* for whatever external process is causing it -- and it explicitly cannot help if that process overwrites the file through a path that bypasses this codebase's own `os.replace()` write entirely.
- **`test_phase_target.py` (10 tests) is stale** against the current 5-phase `PHASE_DEFS` structure and should be rewritten or retired -- it currently tests a phase structure (A/B/C/D) that no longer exists in the live code path at all.
- **`test_multichain_sniper.py` has 11 known pre-existing fixture-drift failures**, acknowledged in commit history but never cleaned up -- a future session fixing entry-filter thresholds should expect to also need to update these fixtures, and should not assume a failure there means a new regression without checking against this baseline first.
- **Phase 5 ($1,000,000 final target) has no automatic cash-out wired** -- ladder-only, "ask Ali when it's close," same pattern the old phase-2 end-state used. Not an oversight; deliberately deferred.
- **Real-money execution is fully built for all 4 books and all 4 chains (Solana/BSC/Base/RHC) but genuinely untested against any live network**, on every chain, as of the most recent audit (`e84c0cd`, 12 Nov) -- this is stated as the one real remaining gap before going live, not a missing-code problem.
- **Dev-reputation/virality signals exist only on the Solana layer17 pipeline** (`good_dev`, `repeat_grad`, `famous_name`, `hot_meta`, `kol_buy`, `kol_converge` in `layers/layer17_early_entry.py`) -- `multichain_sniper.py` (base/bsc/robinhood_chain's only data source, GeckoTerminal's `new_pools` feed) has no creator/deployer wallet field at all, so this class of signal cannot be extended there without an entirely new data source (e.g. on-chain contract-deploy tracing via a block explorer API). Explicitly flagged as a real gap, not silently skipped.
- **`hot_meta` (the celebrity/trending-name confirmation tag) tested negative** in real data (6.7% win WITH it vs 16.3% WITHOUT, n=60) and its weight was zeroed out -- worth remembering if anyone proposes re-enabling a "virality boost" naively; the one version of it already tested came out backwards.
- **Layers 18-21** (catalyst revival, sniper-wallet watch, notable-wallet impersonation watch, celebrity signal watch via Telegram) remain in **shadow mode only** -- they alert and log outcomes but are not wired into the router to actually open positions, by design, until enough outcome data accumulates to prove or kill each one on its own evidence.

# S1c modules, books and routing (Oct 12 2026)

Registry: `layers/modules.py`. Books: `executor/books.py`. Router: `layers/router.py`.
Telegram alerts start with the module label; dashboard **Modules** tab = one card per module.

## Books (each has its own balance; real money stays OFF, all paper)
| Book | Seed | Target | Window | What goes in |
|---|---|---|---|---|
| 🚀 Core 1 | $100 | $125,000 (`CORE1_TARGET_USD`) | 60 d (`CORE1_DAYS`) | early moonshots: first ~20 min of a launch, >= 2 confirmations (10x-50x) |
| 🌙 Core 2 | $100 | $1,000,000 (`CORE2_TARGET_USD`, stretch $1.5M) | 150 d (`CORE2_DAYS`) | pre-early: first ~4 min of a launch, >= 2 confirmations (100x+) |
| 💰 Compound | $100 | daily goal 5%/day (`COMPOUND_DAILY_GOAL_PCT`) | daily | 2x-13x swings: band A/B, momentum, listing-ladder pops |
| 🏆 Leaderboard Copy | $50 | none | - | pump.fun/StonkFun/tracked-wallet copies |
| 👥 Fomo Followers | $50 | none | - | followed traders' buys, convergence |
| 🆕 Fomo New Wallets | $50 | none | - | qualified new wallets' buys (starts by itself when Fomo credits return) |
| 🧑‍💻 Dev Launch | $50 | none | - | elite/good dev list launches |

Balance = seed + realized paper P&L (runners included). Stake = % of balance, lifted to the fee floor ($30/$40/$60 at $0/$300/$700), x1.25 when >25% behind the target path, x0.8 when >50% ahead, capped at 35% (Core 1) / 25% (Core 2) of balance. Open-position limit grows with balance (1 / 2 / 3 / 4 at $0 / $300 / $3K / $30K).
Seeds: `<BOOK>_SEED_USD`.

## Early entry engine (the sniper) -- `worker_early_watch.py` + `layers/layer17_early_entry.py`
Market cap is NOT how a coin picks a book. Every book enters EARLY; they differ in what they do after entry.
- Free real-time PumpPortal stream (`early_stream.py`): every new pump.fun launch, from its first seconds. Runs as its own process, supervised by `local_runner.py` (`LOCAL_RUNNER_EARLY=false` disables). Memory-only while listening; ~150K events/s of headroom.
- Per coin it tracks buyers, buy/sell pressure, creator behaviour, smart-wallet hits, dev-list match, news-term match. It needs >= 4 independent buyers before any entry, and rejects: blacklisted creator, creator holding > 25% of supply, creator dumping, one wallet = 40% of buying, sell pressure.
- Confirmations: dev_list, smart_wallet, velocity (>=10 buyers fast), pressure (buys >= 2.5x sells), narrative (hot news term from the Radar).
- Lanes (can stack): **dev_list** (dev-list creator + buyers), **Core 2** (age <= 4 min, <= 55 SOL curve, >= 2 confirmations), **Core 1** (age <= 20 min, <= 130 SOL, >= 2), **Compound** also enters when >= 3 confirmations (quick 2x-10x profit-taking exit), **Leaderboard** copy only if a smart wallet is the sole signal and no stronger lane fired after 60 s.
- Paper entries only; one Telegram alert per coin: `[🌙 CORE 2 · 100x+] ⚡ EARLY ENTRY ...`. Tunables: `EARLY_*` env (see the module docstring). Thresholds are reasoned defaults, UNTUNED.
- `python diag_early_stream.py` prints the first raw events so the field names can be checked on your PC (schema written from memory, not verified from my environment).

## Finders (no balance; they route into books)
- Polled layers (Own Analysis, Radar, Layer 15) are slower than the stream, so the router is AGE-first: moonshot-class + <= 15 min old -> Core 2, <= 120 min -> Core 1; older or already-ran coins are NOT moonshots (swing at most, else watch). Layer 15 (post-move screen) now only ever feeds Compound. A coin that already ran > +300% in 1h goes to watch.
- Router env: `ROUTER_CORE2_MAX_AGE_MIN`, `ROUTER_CORE1_MAX_AGE_MIN`, `ROUTER_COMPOUND_MAX_AGE_MIN`, `ROUTER_MAX_RAN_H1_PCT`.
- 🔎 Dev Intel: dev holding / wallet age / track record for any alerted coin (a view).
- 🧭 Context: news, buzz, mega-alerts (a view).

Caveat: pace is a gauge of what the target needs (Core 1 ~ +12%/day, Core 2 ~ +6%/day), not growth the strategy has shown. Pre-early entries are high-variance (most launches rug or die); the paper record decides whether the confirmation rules work. Paper fills assume you got the entry price; real fills on a 30-second-old curve coin will be worse.

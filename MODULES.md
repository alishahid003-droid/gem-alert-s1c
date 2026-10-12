# S1c modules, books and routing (Oct 12 2026)

Registry: `layers/modules.py`. Books: `executor/books.py`. Router: `layers/router.py`.
Telegram alerts start with the module label; dashboard **Modules** tab = one card per module.

## Books (each has its own balance; real money stays OFF, all paper)
| Book | Seed | Target | Window | What goes in |
|---|---|---|---|---|
| 🚀 Core 1 | $100 | $125,000 (`CORE1_TARGET_USD`) | 60 d (`CORE1_DAYS`) | mid-cap moonshots, 10x-50x (mcap $300K-$8M) |
| 🌙 Core 2 | $100 | $1,000,000 (`CORE2_TARGET_USD`, stretch $1.5M) | 150 d (`CORE2_DAYS`) | earliest small-cap moonshots, 100x+ (mcap <= $300K, age <= 24h) |
| 💰 Compound | $100 | daily goal 5%/day (`COMPOUND_DAILY_GOAL_PCT`) | daily | 2x-13x swings: band A/B, momentum, listing-ladder pops |
| 🏆 Leaderboard Copy | $50 | none | - | pump.fun/StonkFun/tracked-wallet copies |
| 👥 Fomo Followers | $50 | none | - | followed traders' buys, convergence |
| 🆕 Fomo New Wallets | $50 | none | - | qualified new wallets' buys (starts by itself when Fomo credits return) |
| 🧑‍💻 Dev Launch | $50 | none | - | elite/good dev list launches |

Balance = seed + realized paper P&L (runners included). Stake = % of balance, lifted to the fee floor ($30/$40/$60 at $0/$300/$700), x1.25 when >25% behind the target path, x0.8 when >50% ahead, capped at 35% (Core 1) / 25% (Core 2) of balance. Open-position limit grows with balance (1 / 2 / 3 / 4 at $0 / $300 / $3K / $30K).
Seeds: `<BOOK>_SEED_USD`.

## Finders (no balance; they route into books)
- 🧪 Own Analysis, 📡 Catalyst Radar: every coin is routed ONCE by `layers/router.py` to Core 2, Core 1, Compound or "watch". The alert carries the destination label, not "own analysis". Only coins that fit nowhere stay as watch-list.
- 🔎 Dev Intel: dev holding / wallet age / track record for any alerted coin (a view).
- 🧭 Context: news, buzz, mega-alerts (a view).

## Router rules (`ROUTER_*` env)
blocked or liquidity < $8K -> watch; listing ladder -> Compound; moonshot-class (Layer 15 pass, or radar news/launchpad/conviction/buyback): mcap <= $300K and age <= 24h -> Core 2; <= $8M -> Core 1; larger -> watch; band A/B or momentum (mcap <= $30M) -> Compound; else watch.

Caveat: pace is a gauge of what the target needs (Core 1 ~ +12%/day, Core 2 ~ +6%/day), not growth the strategy has shown. No proven edge yet.

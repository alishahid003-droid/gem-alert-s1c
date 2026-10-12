# S1c modules (Oct 12 2026)

Every alert, paper trade and dashboard row belongs to exactly one module. Registry: `layers/modules.py`.
Telegram alerts start with the module label, e.g. `[🏆 LEADERBOARD COPY] 🚨 ...`. Dashboard: **Modules** tab (one card per module), plus a Module column on the Alerts tab.

| Module | Label | What it is | Fed by |
|---|---|---|---|
| 🌙 Moonshot (Core 1 Marathon) | MOONSHOT | Escape-velocity coins, $100 -> $1M+ | layer15_moonshot, paper source `moonshot` |
| 💰 Compound Engine (Core 2 Sprint) | COMPOUND | Compounding pool, milestone locks | scalper, momentum lane |
| 📡 Catalyst Radar | RADAR | Listing ladder, news-jack, launchpad surge, conviction wallets (paper + alert only) | layer16 |
| 🏆 Leaderboard Copy-Trading | LEADERBOARD COPY | pump.fun / StonkFun / tracked wallets, convergence, large untracked buys, sells | layer2, 2b, 9, 10 |
| 👥 Fomo — Followed Traders | FOMO FOLLOWERS | Your Fomo follow list buying + theses (dashboard only) | layer13 |
| 🆕 Fomo — New High-Balance Wallets | FOMO NEW WALLETS | New wallets with big balances (dashboard only) | layer13 candidates |
| 🧑‍💻 Dev Copy-Trading | DEV LAUNCH | Dev from our elite/good list launched | layer1 |
| 🔎 Dev Analysis | DEV INTEL | Dev holding %, wallet age, track record for any alerted coin | tags on any alert |
| 🧪 Own Analysis | OWN ANALYSIS | Our own filters: score bands, momentum, revival, buyer quality | layer0/0b/0c/8/14/15 |
| 🧭 Context | CONTEXT | News, buzz, callers, mega-alerts, downgrades | layer3/4/7/11/12 |

Notes: Core 1 = Marathon (`moonshot`), Core 2 = Sprint (compound engine). Sprint target in code is $90K / 7 days (`SPRINT_TARGET_USD`); override it if the target is $125K over 1-2 months.

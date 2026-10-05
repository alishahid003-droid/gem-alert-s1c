# The two campaigns (Oct 6 2026)

Two separate $100 attempts, running side by side. Neither is a proven edge --
see HANDOFF.md (paper win rate on alerted coins is ~6-8%; the sprint needs
~76% to grow). Keep real money small until the paper record says otherwise.

| | SPRINT | MARATHON |
|---|---|---|
| Target | **$90,000** total (banked + pool) | **$1,000,000** equity |
| Window | 168 h (7 days) from first entry | 150 days default (`MARATHON_DAYS`, 90-180) |
| Engine | compound scalper, sprint mode (`executor/sprint.py`) | moonshot module on wallet equity (`executor/entrypoint.py`) |
| Money | `COMPOUND_SEED_USD=100` (own pool) | `TOTAL_WALLET_USD=100` (wallet equity) |
| Size | 90% of pool per trade, one at a time | $30 stake -> $40 at $300 -> $60 at $700 -> 8% of equity (max $2,000) |
| Exits | stop -55%, 60% off at 1.5x, 35% trail, 3 h | stake back at 2x, half rides as runner; ladder 10x/50x; campaign closes at $25k/$150k positions |
| Locks | at $3,500 keep $1,500; at $20,000 keep $5,000 (`SPRINT_MILESTONES`) | none; progress vs target path shown daily |
| Stops | 3 losses -> 90 min pause; -75% session or pool < $25 -> stop | max 1 open moonshot below $300 equity, 2 above |
| Progress | Telegram daily summary + dashboard System tab | same (`executor/targets.py`) |

Switches: `SPRINT_MODE=true` (sprint), `MOONSHOT_ENABLED=true` (marathon real buys),
`SPRINT_ONLY=false` (already set in poll-fast/poll-slow workflows so both run together;
on the PC `.env` set it too). `EXECUTION_ENABLED=true` is still the master real-money switch.
Wallet must hold both $100s. Overrides: `SPRINT_TARGET_USD`, `MARATHON_TARGET_USD`, `MARATHON_DAYS`.

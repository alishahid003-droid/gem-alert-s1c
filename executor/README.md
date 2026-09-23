# executor/ -- Part B: two-stage auto-buy + defensive auto-sell + moonbag trim

**Status: built, NOT wired into scheduler.py's live poll loop. Not tested
against any live network. Buy/sell stays 100% manual in Fomo until Ali
explicitly reviews the Part A backtest result and turns this on.**

## What's here

- `config.py` -- master switch (`EXECUTION_ENABLED`, must be the literal
  string `"true"`), position sizing, circuit-breaker thresholds, Stage 2
  mcap-floor settings, moonbag enable flag. Defaults are all safe/off
  (except `moonbag_enabled`, which defaults on since it's pure risk
  reduction with no execution risk of its own -- see below).
- `position_state.py` -- tracks Stage 1 / Stage 2 entries, and now moonbag
  trim history, per (chain, token) in the same state.py backend
  (Upstash/local JSON) every other layer uses.
- `circuit_breaker.py` -- session-level trip on consecutive losses or a %
  of the wallet lost in one sitting.
- `triggers.py` -- pure decision logic (no network) for whether Stage 1 or
  Stage 2 should fire. Fully unit-tested (`tests/test_executor_triggers.py`).
- `moonbag.py` -- **new (Sept 22, 2026)**. The upside counterpart to
  `defensive_sell.py`: as a position multiplies against its entry mcap,
  sells slices of the ORIGINAL position at 3x/10x/50x to recover capital
  and lock in gains, while a remainder -- the moonbag -- is never sold by
  this module and keeps riding uncapped. This is the actual mechanism for
  Ali's stated goal: the system isn't trying to compound a dollar target in
  a fixed window, it's taking many small filtered shots and needs to not
  auto-cap the one that actually runs to a real outlier. Fires at most one
  rung per poll cycle even if price jumps past several tiers at once.
  - **Two ladders, chosen once at entry, never mid-flight (Sept 22, 2026
    update):** `DEFAULT_TRIM_LADDER` (35%/25%/20%, 20% moonbag) for an
    ordinary runner, and `HIGH_CONVICTION_LADDER` (15%/15%/15%, 55%
    moonbag) for a position multiple independent signals flag as real
    moonshot material -- Ali's point: trimming a third of the position at
    3x the same way for every runner throws away exactly the one worth
    holding. `compute_moonshot_score()` combines deployer tier (Layer 1),
    wallet convergence strength (Layer 2), Stage1+Stage2
    double-confirmation, Layer 10's insider-holder ratio, and a news
    catalyst (Layer 4) into one score; `assess_conviction()` computes it
    once right after a Stage 1/2 fire and locks the ladder onto the
    position via `position_state.set_trim_ladder`. Heaviest single weight
    is double-confirmation (+3) -- that's Ali's own stated logic ("if one
    coin is highlighted from both platforms, it might be the actual gem"),
    not an invented heuristic. Every other weight IS a heuristic, unbacktested,
    same honesty flag as the rest of this file. A position nobody calls
    `assess_conviction` on falls back to the default ladder automatically.
  - Fully unit-tested: `tests/test_executor_moonbag.py` (12 tests, ladder
    mechanics) + `tests/test_executor_conviction.py` (12 tests, scoring +
    ladder selection).
- `swap_executor.py` -- actual on-chain send. Solana path uses Jupiter
  (already used elsewhere in this stack for quotes). BSC uses PancakeSwap's
  real public V2 router. **Robinhood Chain router address confirmed (Sept
  22, 2026)** -- Uniswap's own official developer docs
  (developers.uniswap.org/docs/protocols/v4/deployments) list a Robinhood
  Chain (4663) deployment table, cross-checked against Uniswap's own blog
  confirming the chain ID and against the table's Permit2 address matching
  the known canonical cross-chain Permit2 address -- two independent
  Uniswap sources plus a value cross-check, not one page taken on faith.
  `execute_buy_robinhood_chain` no longer raises `NotImplementedError`; it
  now follows the same guarded pattern as the Solana/BSC paths and returns
  an explicit UNTESTED result. **Fully confirmed (Sept 22, 2026):** Ali
  manually checked robinhoodchain.blockscout.com for this address -- it's
  a verified contract, explicitly labeled "UniversalRouter" by the
  explorer itself. That's independent on-chain confirmation on top of the
  two official Uniswap sources above. Only one thing stands between this
  and trusting it with real money now: one real test swap once execution
  is enabled -- same bar every other chain here has to clear.
- `defensive_sell.py` -- reuses `layers.layer6_exit_realizable.
  detect_exit_risk` (the same rug-detection Layer 6's alerts already use)
  to trigger an immediate sell on a position this executor opened, instead
  of waiting for a manual decision hours later. This is the ONLY thing that
  can close a moonbag's remaining slice -- moonbag.py never sells the last
  20% itself.
- `entrypoint.py` -- **new (Sept 22, 2026)**. The single call a live poll
  loop makes per candidate instead of having to sequence triggers.py,
  position_state.py, and moonbag.py itself correctly. `handle_stage1_candidate`
  and `handle_stage2_candidate` evaluate the trigger, and if it fires,
  record the position and lock in its conviction-scored moonbag ladder in
  one step -- including picking up double-confirmation automatically when
  a Stage 2 fire lands on a token Stage 1 already opened (re-scoring can
  only ever upgrade that position's ladder, never downgrade one already
  locked in -- see `moonbag.assess_conviction`'s guard). This is glue, not
  new decision logic, and makes no network calls of its own. Fully
  unit-tested (`tests/test_executor_entrypoint.py`, 7 tests). **Still not
  called from scheduler.py's actual poll loop** -- building this doesn't
  change that; it's the ready-to-call integration point for whenever that
  wiring happens, same "not live yet" status as everything else here.

## No RPC provider account, anywhere

Per Ali's explicit direction (Sept 22, 2026): Solana transactions go
through the default public endpoint (`api.mainnet-beta.solana.com`) via
Jupiter's API, no signup. BSC uses its public dataseed RPC. Nothing here
adds an account or a cost to the deploy checklist.

## Known gap in moonbag.py specifically

Trim sizing is correct in principle (percent of the ORIGINAL position), but
`position.amount_tokens` isn't populated anywhere yet -- `swap_executor`'s
buy paths return `ok=False` ("UNTESTED") instead of recording a real filled
quantity, same honest gap `defensive_sell.py` already has. So `moonbag.py`'s
decision logic (`evaluate_trim`) is fully correct and tested today; the
actual sell in `check_and_trim` won't move a real token amount until fill
recording is wired up alongside turning execution on. Nothing here risks
selling the wrong amount in the meantime -- it just can't sell anything at
all yet, by the same design as every other send path in this repo.

## Before this can ever go live

1. The Part A Layer 0/0b backtest needs a real, trustworthy hit rate.
2. Ali reviews and approves the position-size, Stage 2 mcap-floor, and
   moonbag ladder numbers in `config.py` / `moonbag.py`.
3. `solders`, `base58`, and `web3` need adding to `requirements.txt` and
   installing (not done yet -- deliberately, so nothing here can
   accidentally run just because the repo was pushed).
4. The Solana buy path (`execute_buy_solana`), BSC path
   (`execute_buy_bsc`), and Robinhood Chain path
   (`execute_buy_robinhood_chain`, router address now fully confirmed --
   official Uniswap docs + blog + Ali's own on-chain check on Robinhood
   Chain's block explorer showing it as a verified, labeled
   "UniversalRouter" contract) all need an actual live-network test run
   before being trusted -- all three currently return `ok=False` with an
   explicit "UNTESTED" reason rather than attempting a real send, on
   purpose. This also unlocks real fills for moonbag trims (see gap
   above).
5. A dedicated hot wallet needs funding with ONLY the automation budget
   (~$100 example in `config.py`'s `TOTAL_WALLET_USD`) -- never Ali's main
   holdings' private key.
6. `EXECUTION_ENABLED=true` gets set as a repo secret only after all of the
   above.

## Layer 10 (wallet-cluster + insider tagging)

Lives in `layers/layer10_insider_cluster.py`, not in this folder, since
it's a data/detection layer like Layers 0-9, not an execution component.
See its own module docstring for what it does and its honest chain-coverage
caveat (solid on Solana, thinner on BSC, unconfirmed on Robinhood Chain).

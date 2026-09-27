# Tasks Left — Sept 27, 2026 (supersedes TASKS_LEFT_2026-09-25.md -- read this one first)

Two separate tracks from here: Track A decides whether the detection engine is
actually trustworthy; Track B is real-money go-live. Track A should finish
before funding wallets for Track B -- no point risking real money on a
detection engine we haven't confirmed catches rugs.

## Track A -- Detection accuracy (in progress)

1. [x] Point-in-time credibility backtest v2 built -- real multi-signal engine
   (`score_solana_mint`) as primary, deployer-tier/Birdeye as secondary, not
   gating (commit a347a26)
2. [x] Real bug found + fixed: `bundler_sniper_pct` was silently reading as a
   flat 0% for every token (wrong JSON path + wrong scale in MadeOnSol's real
   `/bundle` response) -- confirmed live, fixed, 2 regression tests (commit
   1e0d3ce)
3. [x] Birdeye real launch-window price shape wired into the LIVE score (not
   just the backtest) -- `score_solana_mint` now calls `fetch_birdeye_ohlcv`
   for Solana-chain mints with a real DexScreener launch date, scores
   drawdown-from-peak as a new weighted component. Reweighted
   `vol_to_liq_ratio` 25->15 to fund it (that ratio couldn't tell a real pump
   from a post-rug quiet token). 3 new tests, 361/361 passing (commit 2e9b1ae)
4. [ ] Re-run `python backtest_point_in_time.py --skip-onchain` once
   MadeOnSol's daily budget resets (~5am PKT) -- today's run hit the 200/day
   BASIC cap mid-run (burned by repeated diagnostic + backtest runs earlier),
   so most rug/pump_dump tokens never got scored with both fixes live. Run
   this ONE time cleanly (no diagnostics first) for the real, complete
   corrected number.
5. [ ] Re-run `python backtest_categorized.py` once budget resets -- the
   broader ~20 elite-deployer + ~20 spammer-deployer sniper-credibility
   backtest, blocked on quota since before this segment started.
6. [ ] Decide: MadeOnSol PRO upgrade ($43 EUR / $49 USDC/mo) -- the only way
   to unblock top10 holder concentration (`/holders` + `/risk` are hard
   403 PRO-tier paywalls on BASIC, confirmed live, no free fix exists). This
   is the one signal most likely to catch a lone-actor rug (not a bundled
   sniper attack) that Birdeye's price-shape check might miss before a real
   collapse shows up in price. Ali's call, staying deferred until raised.
7. [ ] Build: post-alert monitoring pass -- re-check price/liquidity
   15-60 min after a coin is flagged, downgrade/cancel the alert if it
   craters. The multi-signal score is still a one-shot snapshot; this is the
   structural fix for "caught the pump, missed the dump" that reweighting
   alone can't fully solve. Not started.

## Track B -- Go-live (real money, unchanged from Sept 25)

8. [~] Robinhood Chain auto-buy/sell -- written end-to-end, untested live.
   Needs: `EXECUTION_RHC_PRIVATE_KEY` funded + set, `EXECUTION_ENABLED=true`,
   one small real test buy + sell.
9. [ ] Export Phantom's Solana private key + EVM private key
10. [ ] Fund wallets: SOL + BNB via CEX, bridge existing ~$7 Base ETH ->
    Robinhood Chain (Relay/Across/LiFi)
11. [ ] Add GitHub Actions secrets: `EXECUTION_ENABLED=true` +
    `EXECUTION_SOLANA_PRIVATE_KEY` + `EXECUTION_BSC_PRIVATE_KEY` +
    `EXECUTION_RHC_PRIVATE_KEY` + `RHC_RPC_URL`
12. [ ] Real test buy + real test sell on EACH of Solana / BSC / RHC --
    the actual go/no-go gate. Keep `EXECUTION_ENABLED=false` until all pass.
13. [ ] Check dashboard against real trade data once item 12 produces it
14. [ ] Moonbag trim test -- not blocking, needs a real grown position,
    test later once the system is live and a position has actually run up

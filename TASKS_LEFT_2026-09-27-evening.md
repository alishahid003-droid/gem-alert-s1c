# Tasks Left — Sept 27, 2026, evening (supersedes TASKS_LEFT_2026-09-27.md -- read this one first)

Two separate tracks from here: Track A decides whether the detection engine is
actually trustworthy; Track B is real-money go-live. Track A should finish
before funding wallets for Track B -- no point risking real money on a
detection engine we haven't confirmed catches rugs.

## Track A0 -- System reliability (new tonight, both items real and closed)

1. [x] poll-fast discovery cron was firing every 2.5-5.5 hours in real
   practice, not the declared every-10-min -- confirmed live via GitHub's
   own Actions API (a platform-side scheduling throttle, not a bug in this
   code). Fixed: poll-fast now self-loops internally with a real wall-clock
   sleep instead of relying on GitHub to re-invoke it, Upstash-locked so a
   second GitHub schedule fire can't double-run it. (commit f37f1f5)
2. [x] Loop cadence set to 20 min (not 10) to keep Layer 1's MadeOnSol call
   rate under the 200/day BASIC cap -- ~144 calls/day instead of ~288/day,
   leaving real headroom for same-day diagnostics/backtests. (commit 271481f)
3. [ ] Not yet pushed to GitHub -- both commits are local only, need
   `git push origin main` before the real cadence takes effect live.

## Track A -- Detection accuracy (in progress)

4. [x] Point-in-time credibility backtest v2 built (commit a347a26)
5. [x] Real bundler_sniper_pct bug found + fixed (commit 1e0d3ce)
6. [x] Birdeye real launch-window price shape wired into the LIVE score
   (commit 2e9b1ae)
7. [ ] Re-run `python backtest_point_in_time.py --skip-onchain` once
   MadeOnSol's daily budget resets (~5am PKT) -- run this ONE time cleanly
   (no diagnostics first) for the real, complete corrected number.
8. [ ] Re-run `python backtest_categorized.py` once budget resets.
9. [x] Free top10 holder concentration signal built (Solana
   getTokenLargestAccounts + getTokenSupply RPC) -- replaces the gap
   MadeOnSol's PRO-gated /holders endpoint left, for $0/month instead of
   $43-49/month. Wired as a fallback only used when MadeOnSol's own
   /holders data is missing, never second-guesses real MadeOnSol data.
   (commit 795cd40) -- DOWNGRADES the old "decide on MadeOnSol PRO"
   item from blocking to optional: the top10-concentration gap this would
   have unblocked is now covered for free. PRO upgrade could still add
   MadeOnSol's own risk `factors` (mint/freeze authority, lp_lock) beyond
   what GoPlus's fallback already covers, but that's a smaller marginal
   gain now -- Ali's call, no longer urgent.
10. [x] Free real holder-growth-rate signal built (Solana
    getProgramAccounts RPC, feeds state.record_holder_point) -- this field
    already carried 20 of every score's 100 points but was permanently
    None for Solana; this is the moonshot-EARLY signal (distinct from the
    rug-filter signals above), tracks real per-cycle holder count growth.
    (commit 795cd40) -- needs 2+ poll cycles of real history to start
    producing a rate (see compute_holder_growth_rate_per_hr), so its real
    effect won't show until it's live and running for a while.
11. [ ] Build: post-alert monitoring pass -- re-check price/liquidity
    15-60 min after a coin is flagged, downgrade/cancel if it craters.
    Not started.
12. [ ] Honest verification still needed: neither new RPC signal (items 9,
    10) could be tested against live Solana RPC from this session's sandbox
    (same proxy restriction hit earlier with DexScreener/MadeOnSol/
    Birdeye) -- unit tests pass on mocked responses and the RPC methods
    used are standard/documented, but real confirmation needs the first
    live poll cycle once pushed and running on GitHub Actions.

## Track B -- Go-live (real money)

13. [~] Robinhood Chain auto-buy/sell -- written end-to-end, untested live.
    Needs: `EXECUTION_RHC_PRIVATE_KEY` funded + set, `EXECUTION_ENABLED=true`,
    one small real test buy + sell.
14. [ ] Export Phantom's Solana private key + EVM private key
15. [~] Fund wallets -- Solana side partially done tonight: bridged ~$7
    Base ETH (MetaMask) -> Phantom's EVM address -> in-app cross-chain
    swap -> ~0.0567 SOL now sitting funded in Phantom, ready to spend.
    BSC (needs BNB) and Robinhood Chain (needs its own funded wallet)
    still entirely unfunded -- a real auto buy/sell test is only possible
    on Solana right now, not all 3 chains.
16. [ ] Add GitHub Actions secrets: `EXECUTION_ENABLED=true` +
    `EXECUTION_SOLANA_PRIVATE_KEY` + `EXECUTION_BSC_PRIVATE_KEY` +
    `EXECUTION_RHC_PRIVATE_KEY` + `RHC_RPC_URL`
17. [ ] Real test buy + real test sell on EACH of Solana / BSC / RHC --
    the actual go/no-go gate. Keep `EXECUTION_ENABLED=false` until all pass.
18. [ ] Check dashboard against real trade data once item 17 produces it
19. [ ] Moonbag trim test -- not blocking, needs a real grown position

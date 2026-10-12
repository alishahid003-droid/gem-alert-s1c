# Moonshot research -- what PONS, STONK, AMC, MARS had in common (Oct 12 2026)

Status: HYPOTHESES from public sources, NOT tested on our own data. Sources are crypto
news / exchange blogs (some promotional); numbers differ between them. Treat as leads.

## The four winners -- what actually drove each

| Coin | Chain | Type | The catalyst (public, dated) | Move |
|---|---|---|---|---|
| PONS | Robinhood Chain | launchpad token (fee share + buyback/burn: 1% fee, 80% of protocol's 30% burns PONS) | Chain mainnet Jul 1; rival launchpad Noxa stopped new launches Jul 11 (volume consolidated into Pons); live Jul 13-15; low ~$0.0033 Jul 17; Binance Wallet Alpha Sep 2; fees ~$6M/day (DefiLlama top 4) | ~$0.0033 -> ~$0.60 (~180x); later -49% in a week |
| STONK | Solana | launchpad token (60% of revenue buys/burns STONK; also buys top-15 tokens) | StonkFun live early Aug; +60% Aug 11; Sep 2 anti-sniper update; Sep 4 Solana official account "we stand behind Stonk Tokens"; Sep 5 LaunchLab routing announcement | +250% in 24h to ~$140M mcap (Sep 6); ~$126M Oct 8 |
| AMC ("A Meme Coin") | Solana / pump.fun | NEWS-JACK | Robinhood tokenised AMC stock, AMC CEO objected; Sep 4 Robinhood CEO replied "What's the concern?"; token launched on pump.fun Sep 5 | ~$127M FDV and $61M 24h volume by Sep 8 (3 days) |
| MARSCOIN | BNB Chain | exchange-listing ladder | Binance Alpha Jul 30 (+350-500% that day); Gate.io spot Aug 27; Binance perp Sep 1; Binance SPOT announced Sep 4 (+73% / 50% intraday swing); holders also earned SPCXB (tokenised SpaceX) rewards | >$170M mcap after spot; $240M ATH Sep 5 |

## What they share (the real pattern, as far as the evidence goes)
1. **None was random.** Each had a datable, PUBLIC catalyst: a new chain/launchpad going live,
   a fee-revenue/buyback mechanism, a news event, or an exchange-listing step.
2. **Three of four sit in ONE narrative: Robinhood / tokenised stocks (Sep 2026)** -- PONS (Robinhood
   Chain), STONK (stock-paired launchpad), AMC (Robinhood stock-token spat). MARS had stock-reward
   payouts. Narrative leaders of a hot sector run; followers mostly don't (StonkFun top 4 = ~78% of
   the ecosystem's value; nothing below #4 above $6M).
3. **A built-in demand mechanism**: buyback-and-burn, or holder rewards paid from trading tax.
4. **The big multiples went to people in BEFORE the first viral moment** (PONS from the Jul 17 low;
   MARS before Alpha). After the headline, the same coins gave +30-70%, then drawdowns of 25-54% in days.
5. **Speed**: AMC went from launch to $61M daily volume in ~3 days. The news-jack window is hours.

## What "insider" really means here
The repeatable edge is knowing a catalyst BEFORE the crowd. Two kinds:
- PUBLIC precursors anyone can read early (chain launch dates, launchpad fee rank on DefiLlama,
  rival shutdowns, exchange listing ladder steps, viral news). A system can watch these faster than a
  human. THIS is what we build.
- NON-PUBLIC knowledge (a Binance Alpha listing known before the announcement). That is leaked
  information; we cannot see it, and we should not try to obtain it.

## Hard numbers (base rates)
- pump.fun: ~0.63% of 655,770 launches graduated (Sep 2025 study); other sources 0.5%-1.8% (2026).
- A 2026 study of ~750,000 pump.fun mints: a model with AUROC 0.86 on development data fell to 0.46
  (chance) on later data. Simple early-feature models do NOT reliably generalise. We must test on
  unseen data (our 9.9 rule).
- "Memecoin millionaires": winners mostly bought BEFORE the viral post and sold in tranches; one
  famous PENGUIN wallet had >1,000 trades, <15% win rate, >$1.3M cumulative losses before one win.
  Survivorship bias: the lists show winners only.
- Alerts risk flag: pump.fun surge alerts with no risk flag were above their fire price 1h later 60%
  of the time vs 6% when "early buyers exiting" (MadeOnSol teaser; unverified).

## Target maths (so the plan is honest)
- Core 2: $100 -> $125,000 = 1,250x in 30-60 days.  Core 1: $100 -> ~$1.5M = 15,000x.
- Example: 5 consecutive 4.2x trades = 1,250x. A 100x coin once = $10,000, not $125,000.
- A strategy only has positive expectancy if (hit rate x average multiple) > 1 after fees/slippage.
  E.g. 5% of shots hit 10x and the rest lose everything = 0.5x per dollar: loses.
- So the system's job is: maximise the number of well-chosen early shots, cut losers fast, and
  compound only the winners. $100 gives only a handful of shots, so selection quality is everything.

## Catalyst Radar -- what to build (Phase 12 in GO_LIVE_CHECKLIST.md)
1. **Listing-ladder radar**: watch Binance Alpha / futures / spot announcements and the coins on the
   ladder; alert + paper-trade the NEXT step (MARS: Sep 1 perp, Sep 4 spot +73%). Public feed, seconds.
2. **News-jack radar**: detect a viral brand/person news event, find coins launched in the next hours
   with matching names, score them with Layer 15 buyer quality. Window = hours.
3. **Launchpad/chain radar**: track new launchpads and chains (DefiLlama fees/revenue growth, rival
   shutdowns, buyback ratio vs market cap). Platform tokens with buybacks while mcap is still small.
4. **Narrative-leader map**: when a sector is hot, rank the first/biggest token of it; ignore clones.
5. **Forensics of the 4 winners**: pull the first 24h on-chain buyers (needs a Helius key) and check
   whether Layer 15 would have flagged them. This is the first real test of the pattern.
6. **Point-in-time backtest** of each radar (backtest_point_in_time.py): count the catalysts that did
   NOT run, not only the ones that did. No radar goes live without positive results on unseen data.

## Sources
- Yahoo Finance, Pons explainer: https://finance.yahoo.com/markets/crypto/articles/pons-robinhood-chain-meme-coin-204604745.html
- TechFlow, PONS vs Pump.fun: https://www.techflowpost.com/en-US/article/33875
- Motley Fool, meme coins heating up: https://www.fool.com/investing/2026/09/27/meme-coins-are-heating-back-up-again-is-this-an-ea/
- The Block, STONK +250%: https://www.theblock.co/news/defi/2026-09-06-stonk-surges-250-to-140-million-market-cap-as-stonkfun-pulls-volume-to-raydium-and-jupiter-413621
- Blocmates, StonkFun: https://www.blocmates.com/articles/what-is-stonkfun-heres-how-it-works
- AirdropAlert, StonkFun top coins: https://airdropalert.com/blogs/best-stonkfun-coins
- BTCC, AMC explainer (promotional): https://www.btcc.com/en-CA/academy/crypto-wiki/memecoin/what-is-a-meme-coin-amc
- Crypto Briefing, Binance MARSCOIN spot: https://cryptobriefing.com/binance-lists-marscoin-memecoin-spot/
- Bloomingbit, MARS Alpha: https://en.bloomingbit.io/feed/news/117342
- KuCoin, stock-paired meme coins: https://www.kucoin.com/blog/robinhood-stock-paired-meme-coins
- arXiv 2602.14860, predicting pump.fun success: https://arxiv.org/html/2602.14860v1
- arXiv 2607.02823, pump.fun labels / model fails out of sample: https://arxiv.org/abs/2607.02823
- Webopedia, memecoin millionaires: https://www.webopedia.com/crypto/learn/memecoin-millionaires/
- MadeOnSol, first 3 minutes: https://madeonsol.com/blog/pump-fun-first-3-minutes-graduation-math

## ADDENDUM Oct 13 2026 -- what the newest evidence says about picking launches (read before trusting any rule)
- Graduation base rate: ~0.63% (arXiv 2602.14860, 655,770 launches Sep 2025; graduates take a median ~4.4 min); 1.71% in a Jun-Jul 2026 cohort of 693,331 launches.
- The only creation-time feature that survived validation in the 2026 cohort study (scorplabs): creator ALREADY graduated a token AND was quiet >= 7 days -> 98/816 = 12.0% graduated (CI 10.0-14.4%) vs 1.71% base = ~7x. Volume score, holders, socials, wallet clusters were noise (their own score AUC 0.48). Chosen from dozens of tested features on one cohort, so likely optimistic.
- The same author's LIVE trading on that kind of signal: 77 positions, 5 graduations (6.5%), net loss; break-even needed ~8-9% after entry premium and missed fills. I.e. even a 7x-lift filter was about break-even live.
- arXiv 2602.14860: reaching a given SOL-in-curve in FEW trades is the strongest predictor of graduation; trades made directly on the contract (bots) vs the website matter; creators/sophisticated traders often run short pump-and-dumps before graduation.
- arXiv 2607.02823 (already above): models that scored AUROC 0.86 on development data fell to chance on later data.
- Graduation (~$69K) is NOT a moonshot. The coins that made 100x+ (PONS, STONK, AMC, MARS, and earlier GOAT/PNUT/FARTCOIN-type runs) had datable catalysts; a launch-time filter alone is a lottery ticket with a better-than-random but small edge.
WHAT WE BUILT FROM THIS: (1) creator reputation table built from our own stream (early_creators.py): repeat-graduate creator = new "repeat_grad" confirmation that opens the dev_list lane; (2) graduation events recorded (subscribeMigration) so `python analyze_early.py` prints each feature's graduation LIFT with confidence intervals after 24h; (3) catalyst radar still routes news/launchpad/listing coins to Core 1/2 (that is where the 100x+ evidence is).

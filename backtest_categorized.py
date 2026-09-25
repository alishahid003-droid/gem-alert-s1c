"""Categorized credibility/sniper-accuracy backtest -- Ali's ask, Sept 25
2026: "the backtest should be detailed one...on which we should feed
multiple coins...the moon shot ones and the rugs one and the ones which
gained momentum and falled...we should have an extensive backtest run to
check our system credibility and sniper result."

WHAT THIS IS, honestly: backtest.py measures raw deployer-tier band
distribution on a random MadeOnSol sample. backtest_named_coins.py scores a
hand-picked list of real coins with NO labeled outcome. Neither answers
"how good is this system at catching real winners and filtering real
losers" -- that needs LABELED ground truth: real coins with a KNOWN real
outcome (did it moon, did it rug, did it pump-and-dump), scored with the
exact same engine the live system uses, then checked against what SHOULD
have happened for each category:
  - moonshot  -> system SHOULD score A/B (catch it)          -> hit  = A/B
  - rug       -> system SHOULD score C/D (filter it out)     -> hit  = C/D
  - pump_dump -> system SHOULD score C/D (filter it out)     -> hit  = C/D
  - flat      -> no strong expectation either way, reported but not scored
                 as a hit/miss (a token that just never moved isn't what
                 the structural scorer is trying to catch or reject)
A "sniper credibility score" is then just: (hits across all labeled,
non-flat tokens) / (total labeled, non-flat tokens).

Uses the EXACT SAME scoring functions the live poll loop and
backtest_named_coins.py use -- score_solana_mint (Solana/Robinhood Chain,
per-mint MadeOnSol lookup, works for ANY real mint regardless of whether
it's currently trending) and score_mobula_pulse_items (BSC/Base, but see
the honest limitation below).

HONEST LIMITATION, stated up front, same one backtest_named_coins.py
already flags: BSC/Base tokens can only be scored if they are STILL
present in Mobula's live "Pulse" trending snapshot RIGHT NOW -- there is no
per-address historical Mobula lookup in this codebase. A BSC rug from years
ago (the codebase's own BSC_COIN_NAMES example, 和平熊猫/友谊使者, already
demonstrates this) will almost never still be in the live trending list, so
BSC_LABELED below is deliberately left empty rather than padded with
entries this code cannot actually score -- adding an old BSC token here
would just print "not in current Pulse snapshot" for every run, which is
noise, not a result.

HONEST LIMITATION #2: SOLANA_LABELED below has THREE real,
independently-verified entries per Sept 25 2026 -- still too few per
category for the credibility % to be statistically meaningful, but growing:
  - SHROOM (moonshot): Ali's own real Robinhood Chain trade, ~$391 ->
    $1M+ unrealized, already used in backtest_named_coins.py.
  - USELESS COIN (moonshot): mint Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk,
    already independently verified in backtest_named_coins.py's own
    SOLANA_RHC_COINS list ("resolved live via DexScreener"). Re-confirmed
    live on DexScreener Sept 25 2026: ~1 year old, ~$298M market cap,
    sustained (not a fresh/reversible pump) -- a real settled moonshot.
  - MCAT / "Mooncat" (pump_dump): mint
    7EA8EStQETtKkqFW8rqj77nfkiLEniVag8Y2jNUTpump, confirmed live on
    DexScreener Sept 25 2026 via its own "Copy token address" control (a
    first-party DexScreener UI element, not a guess) -- 7 days old, was
    trading with real volume/liquidity, then fell -72.6% in 24h while
    liquidity stayed real ($78K), i.e. exactly the "gained momentum and
    fell" pattern Ali asked for, not a stale/dead pool.
  One candidate was found and DISCARDED rather than added: SPCX (a
  tokenized-SpaceX-stock token on Meteora) showed -99.99%/$0 mcap when
  first checked, which looked like a rug -- but re-checking the same pair
  ~3 hours later showed it back at a real $148 price, meaning the earlier
  reading was a stale/broken pool snapshot, not a real collapse. Not
  used, since this list only takes settled, real outcomes.
  No genuine RUG example (LP fully drained, not just a hard dump) has
  been independently verified and added yet -- DexScreener's own filters
  don't expose "current liquidity ~$0 but had real liquidity historically"
  directly, and guessing one from memory is against this session's
  standing no-guessing rule. Two honest ways to keep growing this list,
  NOT guessed/fabricated addresses:
  1. Ali pastes more real coins he knows the outcome of (same as he did for
     the Sept 24 2026 named-coin backtest) -- fastest, and he has direct
     first-hand knowledge of real addresses and real outcomes, especially
     for a genuine rug.
  2. Further web research per candidate token, cross-verified against an
     independent source before being added here (same two-source-plus
     discipline used for every contract address/selector in this codebase
     this session) -- slower, and was already attempted once this session
     (SQUID token on BSC and the LIBRA/Milei Solana scandal were both
     confirmed as real, well-documented cases, but SQUID can't be scored
     here per limitation #1 above, and LIBRA's exact mint address could not
     be confirmed from public reporting in the time available -- so neither
     was added rather than guess).

Usage:
    python backtest_categorized.py
"""
import sys
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import CONFIG
from layers.layer0_scoring import (
    score_solana_mint, fetch_mobula_pulse, score_mobula_pulse_items, flatten_mobula_pulse_response,
)
from utils.http import describe_fetch_failure

# Each entry: (name, chain, address, category, is_pregraduation)
# category in {"moonshot", "rug", "pump_dump", "flat"}
# Real, independently-verifiable entries only -- see module docstring for
# why this list is short today and how to grow it honestly.
SOLANA_LABELED = [
    ("SHROOM", "robinhood_chain", "0xab093dEF657F15dF31b33922A95e047aDd645B29", "moonshot", False),
    ("USELESS COIN", "solana", "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk", "moonshot", False),
    ("Mooncat (MCAT)", "solana", "7EA8EStQETtKkqFW8rqj77nfkiLEniVag8Y2jNUTpump", "pump_dump", False),
]

# See module docstring, limitation #1 -- left empty on purpose, not padded
# with tokens this code cannot actually score.
BSC_LABELED = []

CATEGORY_EXPECTATION = {
    # category -> set of bands that count as a "hit" (system did the right thing)
    "moonshot": {"A", "B"},
    "rug": {"C", "D"},
    "pump_dump": {"C", "D"},
}


def score_solana_labeled(entries: list) -> list:
    rows = []
    if not CONFIG.madeonsol_api_key:
        print("BLOCKED: MADEONSOL_API_KEY not set -- cannot score any Solana/RHC entries")
        return rows
    for name, chain, address, category, is_pregrad in entries:
        try:
            result = score_solana_mint(address, chain, is_pregraduation=is_pregrad)
        except Exception as e:
            print(f"[{category:10s}] {name} / {chain}: NETWORK ERROR: {e}")
            continue
        if "error" in result:
            print(f"[{category:10s}] {name} / {chain}: FAILED -- {result['error']}")
            continue
        sc = result["score"]
        rows.append({"name": name, "chain": chain, "address": address, "category": category,
                      "band": sc.band, "score": sc.score, "reasons": sc.reasons})
        print(f"[{category:10s}] {name} / {chain}: band={sc.band} score={sc.score}/100 "
              f"liquidity={sc.liquidity_flag}")
    return rows


def score_bsc_labeled(entries: list) -> list:
    rows = []
    if not entries:
        return rows
    if not CONFIG.mobula_api_key:
        print("BLOCKED: MOBULA_API_KEY not set -- cannot score any BSC entries")
        return rows
    by_chain_id = defaultdict(list)
    for name, chain, address, category, _ in entries:
        chain_id = {"bsc": "evm:56", "base": "evm:8453"}.get(chain)
        by_chain_id[chain_id].append((name, chain, address, category))
    for chain_id, group in by_chain_id.items():
        if chain_id is None:
            for name, chain, address, category in group:
                print(f"[{category:10s}] {name} / {chain}: SKIPPED -- unsupported chain for Mobula lookup")
            continue
        raw = fetch_mobula_pulse(chain_id)
        if not raw.get("ok"):
            for name, chain, address, category in group:
                print(f"[{category:10s}] {name} / {chain}: FAILED fetching Pulse -- "
                      f"{describe_fetch_failure({'raw': raw})}")
            continue
        items = flatten_mobula_pulse_response(raw.get("json"))
        chain_name = group[0][1]
        scored = score_mobula_pulse_items(chain_name, items)
        by_address = {i.get("address", "").lower(): s for i, s in zip(items, scored)}
        for name, chain, address, category in group:
            match = by_address.get(address.lower())
            if not match or "error" in match:
                print(f"[{category:10s}] {name} / {chain}: not in current Mobula Pulse trending "
                      f"snapshot -- cannot retroactively score (no per-address historical Mobula "
                      f"lookup exists in this codebase, see module docstring)")
                continue
            sc = match["score"]
            rows.append({"name": name, "chain": chain, "address": address, "category": category,
                          "band": sc.band, "score": sc.score, "reasons": sc.reasons})
            print(f"[{category:10s}] {name} / {chain}: band={sc.band} score={sc.score}/100 "
                  f"liquidity={sc.liquidity_flag}")
    return rows


def report(rows: list):
    print("\n=== CATEGORIZED CREDIBILITY REPORT ===")
    if not rows:
        print("No rows scored -- nothing to report.")
        return

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)

    total_hits = 0
    total_judged = 0
    for category in ("moonshot", "rug", "pump_dump", "flat"):
        cat_rows = by_cat.get(category, [])
        if not cat_rows:
            continue
        bands = defaultdict(int)
        for r in cat_rows:
            bands[r["band"]] += 1
        print(f"\n{category} (n={len(cat_rows)}): band distribution "
              f"{dict(bands)}")
        expected = CATEGORY_EXPECTATION.get(category)
        if expected is None:
            print(f"  (no pass/fail expectation for '{category}' -- reported for visibility only)")
            continue
        hits = sum(1 for r in cat_rows if r["band"] in expected)
        rate = hits / len(cat_rows) * 100
        verb = "caught" if category == "moonshot" else "filtered out"
        print(f"  {verb}: {hits}/{len(cat_rows)} ({rate:.1f}%) scored in expected bands {sorted(expected)}")
        total_hits += hits
        total_judged += len(cat_rows)

    if total_judged:
        print(f"\nOVERALL SNIPER CREDIBILITY: {total_hits}/{total_judged} "
              f"({total_hits / total_judged * 100:.1f}%) of labeled moonshot/rug/pump_dump tokens "
              f"scored in the band the system SHOULD have given them.")
        if total_judged < 15:
            print(f"CAVEAT: n={total_judged} labeled tokens is too small a sample for this percentage "
                  f"to be statistically meaningful -- treat it as a directional smoke test, not a real "
                  f"credibility measurement, until the labeled list grows (see module docstring for how).")
    else:
        print("\nNo category had both a scored result AND a pass/fail expectation -- nothing to summarize.")


def main():
    print("=" * 70)
    print("CATEGORIZED BACKTEST -- moonshot / rug / pump_dump credibility check")
    print("=" * 70)
    rows = []
    print(f"\n--- Solana / Robinhood Chain ({len(SOLANA_LABELED)} labeled tokens) ---")
    rows += score_solana_labeled(SOLANA_LABELED)
    print(f"\n--- BSC / Base ({len(BSC_LABELED)} labeled tokens) ---")
    rows += score_bsc_labeled(BSC_LABELED)
    report(rows)
    print("\nDone. To grow this into a real statistical measurement, add more labeled tokens to "
          "SOLANA_LABELED/BSC_LABELED at the top of this file -- see the module docstring for why "
          "each entry must be a real, verifiable token, not a guess.")


if __name__ == "__main__":
    sys.exit(main())

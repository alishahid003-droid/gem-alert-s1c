"""
Named-coin backtest -- Ali's ask (Sept 24 2026): "run a backtest tonight for
these coins and the other earlier coins I've given you... see what our
system would have reacted to, how effective our gem-based system is."

Unlike backtest.py (which pulls a RANDOM sample from MadeOnSol's own
elite/good vs spammer tier lists to measure raw hit-rate), this scores a
SPECIFIC, hand-picked list of real coins Ali named or that came up tonight
researching his Fomo screenshot -- the actual question is "would OUR system
have caught THESE specific winners", not a random sample.

Uses the exact same scoring code the live poll loop uses
(layers.layer0_scoring.score_solana_mint for Solana/Robinhood Chain,
score_mobula_pulse_items for BSC/Base) -- no separate/duplicate scoring
logic, so a good score here means the live system really would have scored
it the same way.

Honest limitation, stated up front: BSC/Base tokens can only be scored if
they're STILL present in Mobula's live "Pulse" trending snapshot right now
-- there is no per-address historical Mobula lookup in this codebase (the
live system only ever scores what's in the current Pulse response, see
score_mobula_pulse_items's docstring). A BSC coin that's cooled off and
dropped out of Pulse's trending list can't be retroactively scored with the
current code -- flagged per-coin below, not silently skipped.

All addresses below were resolved live via DexScreener on Sept 24 2026 --
real mint/contract addresses, not guessed.

Usage:
    python backtest_named_coins.py
"""
import sys

# Same fix as scheduler.py (Sept 24 2026): load .env BEFORE importing config,
# since config.py builds CONFIG from os.environ at import time.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import CONFIG
from layers.layer0_scoring import score_solana_mint, fetch_mobula_pulse, score_mobula_pulse_items

# Each entry: (display_name, chain, address, is_pregraduation)
# is_pregraduation is always False here -- every coin below has already
# graduated / is trading on a real DEX, not still on a pump.fun bonding curve.
SOLANA_RHC_COINS = [
    # From tonight's Fomo-screenshot investigation:
    ("familiars", "solana", "EnivH49xwRm3cu9N4tP6cGyNcfxf3Mz6ce3grmvk1Lh4", False),
    ("Holdoween", "solana", "Apy5FshcDsLDyySh4HWgY8ZsFj4At8FbKEhZf3d72e9k", False),
    ("USELESS COIN", "solana", "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk", False),
    ("PAID", "solana", "98kfF7rmsg1QDUEoCqNE7g7M1FdrTt92TEp2CLzypump", False),
    # SHROOM -- Ali's own real trade (~$391 -> $1M+ unrealized per his
    # earlier account). Confirmed on Robinhood Chain.
    ("SHROOM", "robinhood_chain", "0xab093dEF657F15dF31b33922A95e047aDd645B29", False),
    ("PONS", "robinhood_chain", "0x39dBED3a2bd333467115dE45665cC57F813C4571", False),
]

# --- BSC coins from tonight's screenshot -- can only score if still present
# in Mobula's LIVE Pulse snapshot right now (see module docstring). ---
BSC_COIN_NAMES = ["和平熊猫", "友谊使者"]  # Peace Panda, Friendship Envoy

# --- Ali also named these -- could not resolve real addresses tonight,
# DexScreener's search UI wasn't returning a clean match for either (both
# low-liquidity/ambiguous names -- multiple unrelated tokens share "Mars",
# and SUICAT didn't surface a confident match in the trending set). Not
# faked/guessed -- flagged honestly instead. ---
UNRESOLVED = ["Mars coin", "SUICAT"]


def score_bsc_by_name(names: list) -> dict:
    """Looks up each name in the CURRENT Mobula BSC Pulse snapshot (one
    call, covers the whole trending list) and scores it with the exact same
    function the live system uses. Returns {name: result_dict_or_reason}."""
    out = {}
    if not CONFIG.mobula_api_key:
        for n in names:
            out[n] = {"error": "MOBULA_API_KEY not set"}
        return out
    try:
        raw = fetch_mobula_pulse("bnb:bnb")
    except Exception as e:
        for n in names:
            out[n] = {"error": f"network error: {e}"}
        return out
    if not raw.get("ok"):
        for n in names:
            out[n] = {"error": f"Mobula Pulse fetch failed: {raw.get('reason')}"}
        return out
    items = (raw.get("json") or {}).get("data", []) if isinstance(raw.get("json"), dict) else []
    scored_all = score_mobula_pulse_items("bsc", items)
    by_name = {}
    for item, scored in zip(items, scored_all):
        nm = (item.get("name") or item.get("symbol") or "").strip()
        by_name[nm] = scored
    for n in names:
        match = by_name.get(n)
        if match:
            out[n] = match
        else:
            out[n] = {"error": "not in current Mobula BSC Pulse trending snapshot -- "
                                "cannot retroactively score with current code (no per-address "
                                "historical Mobula lookup exists in this codebase)"}
    return out


def main():
    print("=" * 70)
    print("NAMED-COIN BACKTEST -- would S1c's real scoring code have caught these?")
    print("=" * 70)

    print(f"\n--- Solana / Robinhood Chain ({len(SOLANA_RHC_COINS)} coins, via MadeOnSol) ---")
    if not CONFIG.madeonsol_api_key:
        print("BLOCKED: MADEONSOL_API_KEY not set")
    else:
        for name, chain, address, is_pregrad in SOLANA_RHC_COINS:
            try:
                result = score_solana_mint(address, chain, is_pregraduation=is_pregrad)
            except Exception as e:
                print(f"[{name} / {chain}] NETWORK ERROR: {e}")
                continue
            if "error" in result:
                print(f"[{name} / {chain}] FAILED: {result['error']}")
            else:
                sc = result["score"]
                print(f"[{name} / {chain}] band={sc.band} score={sc.score}/100 "
                      f"liquidity={sc.liquidity_flag} reasons={sc.reasons}")

    print(f"\n--- BSC ({len(BSC_COIN_NAMES)} coins, via Mobula Pulse) ---")
    bsc_results = score_bsc_by_name(BSC_COIN_NAMES)
    for name, result in bsc_results.items():
        if "error" in result:
            print(f"[{name} / bsc] {result['error']}")
        else:
            sc = result["score"]
            print(f"[{name} / bsc] band={sc.band} score={sc.score}/100 "
                  f"liquidity={sc.liquidity_flag} reasons={sc.reasons}")

    if UNRESOLVED:
        print(f"\n--- Could not resolve real addresses tonight, not run: {', '.join(UNRESOLVED)} ---")

    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())

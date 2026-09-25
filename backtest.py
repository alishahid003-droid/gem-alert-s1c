"""
Layer 0/0b rug-scoring backtest.

Pulls ~20 tokens from MadeOnSol's own confirmed elite/good-tier deployers
and ~20 from confirmed rising-tier (newer, less-established) deployers,
runs all ~40 through the Layer 0/0b structural scoring engine, and reports
the real band distribution for each group -- across a real sample, not
validated on hand-picked winners.

This is the end-to-end test #3 already specified in the original build
prompt (Notion "S1c -- Fomo Gem-Alert System" page, section titled
"FINAL REQUIREMENT -- THREE END-TO-END TESTS BEFORE CALLING THIS DONE").
It was never run because the original build sandbox had zero outbound
network access. GitHub Actions runners do have real network access, which
is why this script is meant to run there (see .github/workflows/backtest.yml),
not locally in a sandboxed shell.

METHODOLOGY NOTE, corrected Sept 25 2026 after a live diagnostic (real
MADEONSOL_API_KEY, Ali's own terminal): this originally compared
elite/good deployers against a "spammer"/"spam" tier, assuming MadeOnSol's
Deployer Hunter tracks a rug/scam blacklist tier the way a lot of similar
tools do. It doesn't. Querying the live /deployer-hunter/alerts endpoint
for a bare, unfiltered sample and inspecting every deployer.tier value
actually present showed only THREE real values: "elite", "good", "rising"
-- and every guessed bad-tier value tried (spammer, spam, bad, low, risky,
flagged, unranked, unknown, new) got 400 "Invalid query parameters", i.e.
rejected as not a real enum member, not just empty. MadeOnSol's Deployer
Hunter is a good-actor tracker (does this wallet have a track record of
tokens that bond/succeed), not a scam blacklist -- there is no bulk
"known-bad deployer" list exposed anywhere in this codebase's MadeOnSol
integration (the only other endpoint used, /tokens/{mint}/risk, scores
individual tokens' structural risk factors, not deployer reputation, and
has no bulk listing).

So the real, honest comparison this script can run is elite/good
(proven-track-record deployers) vs. rising (real tier, fetchable, but
newer/less-established deployers -- NOT a confirmed-bad/rug group, just a
lower-confidence one). Results are reported as band distributions for
each group rather than an asserted "should score C/D" hit rate, since
"rising" isn't ground-truth-bad the way the original "spammer" comparison
assumed.

Requires MADEONSOL_API_KEY. Currently scoped to Solana only (Robinhood
Chain deployer-tier coverage on MadeOnSol is unconfirmed as of this build --
if RHC works the same way, extending this to include RHC alerts is a
one-line change to CHAINS below).

Usage (in CI or anywhere with real network + the key set):
    python backtest.py
    python backtest.py --sample-size 20   # tokens per tier, default 20
"""
import argparse
import sys
from collections import Counter

# Same fix as scheduler.py/backtest_named_coins.py (Sept 24-25, 2026): load
# .env BEFORE importing config, since config.py builds CONFIG from
# os.environ at import time. Without this, MADEONSOL_API_KEY (and every
# other .env-only secret) is invisible even when the real key is present in
# .env -- this script previously only worked when run as a GitHub Actions
# step (where the secret is a real env var, not a .env file). Fixed so it
# also runs locally/on-device with a real .env, per Ali's Sept 25 2026
# "clear the bugs and blockers" instruction.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import CONFIG
from layers.layer1_deployer import fetch_deployer_alerts
from layers.layer0_scoring import score_solana_mint

GOOD_TIERS = {"elite", "good"}
# Real, confirmed-live tier (see module docstring) -- NOT a proven-bad/rug
# group, just MadeOnSol's lowest tier that this endpoint will actually
# return. There is no rug/scam blacklist tier available via this endpoint.
COMPARISON_TIERS = {"rising"}

CHAINS = ["solana"]  # extend to "robinhood_chain" once RHC tier coverage is confirmed


def fetch_tier_sample(chain: str, tiers: set, sample_size: int) -> list:
    """Reuses the same /deployer-hunter/alerts endpoint Layer 1 already
    calls, but asks for a different tier set -- MadeOnSol's own tier data
    is the ground truth here, same endpoint family, just a different
    `tier` query param.

    FIXED Sept 25, 2026, in three rounds: (1) this used to also send a
    `limit` query param (`sample_size * 3`) which turned out to be
    harmless -- removed anyway since it's unused by the API; sample-size
    capping happens entirely client-side in the loop below. (2) the real
    bug: a live diagnostic against MadeOnSol proved the endpoint rejects a
    comma-joined `tier` value ("tier=elite,good" -> 400 "Invalid query
    parameters") and wants the tier param repeated once per value instead
    ("tier=elite&tier=good") -- see the tier_list fix below.
    layers/layer1_deployer.py's fetch_deployer_alerts() had this exact
    same comma-join bug live in production; fixed there too. (3) even
    correctly shaped, a "spammer"/"spam" tier value doesn't exist in
    MadeOnSol's real vocabulary -- see COMPARISON_TIERS and the module
    docstring."""
    if not CONFIG.madeonsol_api_key:
        print(f"BLOCKED: MADEONSOL_API_KEY not configured -- cannot pull {tiers} sample for {chain}")
        return []

    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    from utils.http import get_json

    # tier as a list -- requests encodes this as a repeated query param
    # (tier=elite&tier=good), which is the shape MadeOnSol's validator
    # actually accepts (see module docstring).
    tier_list = sorted(tiers)
    result = get_json(
        f"{CONFIG.madeonsol_base_url}{prefix}/deployer-hunter/alerts",
        headers=headers,
        params={"tier": tier_list},
    )
    if not result.get("ok"):
        tier_param = ",".join(tier_list)
        print(f"FAILED fetching tier={tier_param} chain={chain}: "
              f"status={result.get('status_code')} body={str(result.get('json'))[:300]}")
        return []

    body = result.get("json") or {}
    alerts = body.get("alerts", [])
    seen = set()
    out = []
    for a in alerts:
        tier = a.get("deployer_tier") or (a.get("deployers") or {}).get("tier")
        if tier not in tiers:
            continue
        mint = a.get("token_mint") or a.get("token_address")
        if not mint or mint in seen:
            continue
        seen.add(mint)
        out.append({"mint": mint, "tier": tier, "deployer": a.get("deployer_address")})
        if len(out) >= sample_size:
            break
    return out


def run_backtest(sample_size: int = 20):
    print(f"=== S1c Layer 0/0b backtest -- {sample_size} tokens/tier, chains={CHAINS} ===\n")

    if not CONFIG.madeonsol_api_key:
        print("MADEONSOL_API_KEY is not set. This must run as a GitHub Actions step with the repo "
              "secret configured -- see .github/workflows/backtest.yml. Exiting.")
        sys.exit(1)

    rows = []
    for chain in CHAINS:
        good = fetch_tier_sample(chain, GOOD_TIERS, sample_size)
        comparison = fetch_tier_sample(chain, COMPARISON_TIERS, sample_size)

        if not good and not comparison:
            print(f"[{chain}] No samples returned for either tier group -- check MadeOnSol's actual "
                  f"tier vocabulary (this script assumes 'elite'/'good' and 'rising'; if MadeOnSol "
                  f"uses different labels, adjust GOOD_TIERS/COMPARISON_TIERS at the top of this file).")
            continue

        print(f"[{chain}] fetched {len(good)} good-tier, {len(comparison)} rising-tier tokens\n")

        for label, sample in (("good_deployer", good), ("rising_deployer", comparison)):
            for item in sample:
                is_pregrad = False  # deployer-alert tokens are generally already trading; adjust if
                                     # MadeOnSol's alert payload later exposes a graduation flag
                result = score_solana_mint(item["mint"], chain, is_pregrad)
                if "error" in result:
                    print(f"  SKIP {item['mint']}: {result['error']}")
                    continue
                score = result["score"]
                rows.append({
                    "label": label,
                    "tier": item["tier"],
                    "mint": item["mint"],
                    "score": score.score,
                    "band": score.band,
                })
                print(f"  [{label:15s}] {item['mint'][:10]}... tier={item['tier']:8s} "
                      f"score={score.score:3d} band={score.band}")

    print("\n=== RESULTS ===")
    if not rows:
        print("No rows scored -- nothing to report. Check the errors above.")
        sys.exit(1)

    good_rows = [r for r in rows if r["label"] == "good_deployer"]
    comparison_rows = [r for r in rows if r["label"] == "rising_deployer"]

    good_bands = Counter(r["band"] for r in good_rows)
    comparison_bands = Counter(r["band"] for r in comparison_rows)

    print(f"\nGood-deployer tokens (elite/good, n={len(good_rows)}): band distribution {dict(good_bands)}")
    print(f"Rising-deployer tokens (n={len(comparison_rows)}): band distribution {dict(comparison_bands)}")
    print("\nNote: 'rising' is a real MadeOnSol tier for newer/less-established deployers, NOT a "
          "confirmed-bad/rug group -- MadeOnSol's Deployer Hunter has no rug/scam blacklist tier "
          "exposed via this endpoint (see module docstring). So there is no asserted 'should score "
          "C/D' expectation for this group -- what matters is whether the two distributions differ "
          "in the expected direction (elite/good skewing higher) at all, not an exact hit-rate target.")

    good_hit = sum(1 for r in good_rows if r["band"] in ("A", "B"))
    good_rate = (good_hit / len(good_rows) * 100) if good_rows else 0
    print(f"\nHit rate: {good_rate:.1f}% of good-deployer tokens scored A/B "
          f"({good_hit}/{len(good_rows)})")

    print("\nThis is the real number to compare against SQUEEZE's independently-verified 52.62% "
          "win rate before any auto-buy gate gets set from this scoring engine.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=20)
    args = parser.parse_args()
    run_backtest(args.sample_size)

"""
Layer 0/0b rug-scoring backtest.

Pulls ~20 tokens from MadeOnSol's own confirmed elite/good-tier deployers
and ~20 from confirmed spammer-tier deployers, runs all ~40 through the
Layer 0/0b structural scoring engine, and reports the real hit rate: did it
score the good-deployer tokens high (A/B) and the spam-deployer tokens low
(C/D), across a real sample -- not validated on hand-picked winners.

This is the end-to-end test #3 already specified in the original build
prompt (Notion "S1c -- Fomo Gem-Alert System" page, section titled
"FINAL REQUIREMENT -- THREE END-TO-END TESTS BEFORE CALLING THIS DONE").
It was never run because the original build sandbox had zero outbound
network access. GitHub Actions runners do have real network access, which
is why this script is meant to run there (see .github/workflows/backtest.yml),
not locally in a sandboxed shell.

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

from config import CONFIG
from layers.layer1_deployer import fetch_deployer_alerts
from layers.layer0_scoring import score_solana_mint

GOOD_TIERS = {"elite", "good"}
BAD_TIERS = {"spammer", "spam"}

CHAINS = ["solana"]  # extend to "robinhood_chain" once RHC tier coverage is confirmed


def fetch_tier_sample(chain: str, tiers: set, sample_size: int) -> list:
    """Reuses the same /deployer-hunter/alerts endpoint Layer 1 already
    calls, but asks for the opposite tier set (spammer) as well as the
    normal elite/good set -- MadeOnSol's own tier data is the ground truth
    here, same endpoint family, just a different `tier` query param."""
    if not CONFIG.madeonsol_api_key:
        print(f"BLOCKED: MADEONSOL_API_KEY not configured -- cannot pull {tiers} sample for {chain}")
        return []

    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    from utils.http import get_json

    tier_param = ",".join(sorted(tiers))
    result = get_json(
        f"{CONFIG.madeonsol_base_url}{prefix}/deployer-hunter/alerts",
        headers=headers,
        params={"tier": tier_param, "limit": sample_size * 3},  # over-fetch, dedupe below
    )
    if not result.get("ok"):
        print(f"FAILED fetching tier={tier_param} chain={chain}: "
              f"status={result.get('status_code')} body={str(result.get('json'))[:300]}")
        return []

    body = result.get("json") or {}
    alerts = body.get("alerts", [])
    seen = set()
    out = []
    for a in alerts:
        tier = a.get("deployer_tier")
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
        bad = fetch_tier_sample(chain, BAD_TIERS, sample_size)

        if not good and not bad:
            print(f"[{chain}] No samples returned for either tier -- check MadeOnSol's actual tier "
                  f"vocabulary (this script assumes 'elite'/'good' and 'spammer'/'spam'; if MadeOnSol "
                  f"uses different labels, adjust GOOD_TIERS/BAD_TIERS at the top of this file).")
            continue

        print(f"[{chain}] fetched {len(good)} good-tier, {len(bad)} bad-tier tokens\n")

        for label, sample in (("good_deployer", good), ("bad_deployer", bad)):
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
                print(f"  [{label:14s}] {item['mint'][:10]}... tier={item['tier']:8s} "
                      f"score={score.score:3d} band={score.band}")

    print("\n=== RESULTS ===")
    if not rows:
        print("No rows scored -- nothing to report. Check the errors above.")
        sys.exit(1)

    good_rows = [r for r in rows if r["label"] == "good_deployer"]
    bad_rows = [r for r in rows if r["label"] == "bad_deployer"]

    good_bands = Counter(r["band"] for r in good_rows)
    bad_bands = Counter(r["band"] for r in bad_rows)

    print(f"\nGood-deployer tokens (n={len(good_rows)}): band distribution {dict(good_bands)}")
    print(f"Bad-deployer tokens  (n={len(bad_rows)}): band distribution {dict(bad_bands)}")

    good_hit = sum(1 for r in good_rows if r["band"] in ("A", "B"))
    bad_hit = sum(1 for r in bad_rows if r["band"] in ("C", "D"))

    good_rate = (good_hit / len(good_rows) * 100) if good_rows else 0
    bad_rate = (bad_hit / len(bad_rows) * 100) if bad_rows else 0

    print(f"\nHit rate: {good_rate:.1f}% of good-deployer tokens scored A/B "
          f"({good_hit}/{len(good_rows)})")
    print(f"Hit rate: {bad_rate:.1f}% of bad-deployer tokens scored C/D "
          f"({bad_hit}/{len(bad_rows)})")
    print(f"\nCombined accuracy: {(good_hit + bad_hit)}/{len(rows)} "
          f"({(good_hit + bad_hit) / len(rows) * 100:.1f}%)")

    print("\nThis is the real number to compare against SQUEEZE's independently-verified 52.62% "
          "win rate before any auto-buy gate gets set from this scoring engine.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=20)
    args = parser.parse_args()
    run_backtest(args.sample_size)

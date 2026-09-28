"""Ground-truth check: dump the RAW, unparsed JSON bodies of MadeOnSol's
/holders and /bundle endpoints for one real token, to confirm or refute the
suspected nesting bug in signals_from_madeonsol_risk (top10_share and
held_pct_of_supply read as top-level keys, but the SDK docs describe them
as nested under "concentration" and "bundle" respectively). No guessing --
this prints the exact real shape so the fix (if any) is based on what the
API actually returns, not on doc wording.

Usage: python diag_raw_json.py
"""
import json
from dotenv import load_dotenv
load_dotenv()
import config
from utils.http import get_json

MINT = "2sztT8K9Xu3cfp6WTEEB44Hdibmj3Pv6G2pWGM1vqzXP"  # ELONCOIN, real token from the labeled set
CHAIN = "solana"

def main():
    headers = {"Authorization": f"Bearer {config.CONFIG.madeonsol_api_key}"}
    prefix = ""
    for name in ("holders", "bundle", "risk"):
        url = f"{config.CONFIG.madeonsol_base_url}{prefix}/tokens/{MINT}/{name}"
        result = get_json(url, headers=headers)
        print(f"\n{'='*70}\n{name.upper()} -- {url}")
        print(f"ok={result.get('ok')}")
        if result.get("ok"):
            print(json.dumps(result.get("json"), indent=2)[:3000])
        else:
            print(f"FAILED: {result}")

if __name__ == "__main__":
    main()

"""
Central config for the S1c gem-alert system.

Everything comes from environment variables so the exact same code runs
locally, in GitHub Actions (as repo secrets), or on Render.com (as env vars).
Nothing here executes trades. This module only decides WHICH layers can run
given what's configured -- if a key is missing, that layer reports itself as
"unconfigured" instead of crashing the whole poll cycle.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name, default)
    if val is not None:
        val = val.strip()
        if val == "":
            val = None
    return val


@dataclass
class Config:
    # --- Part A accounts/keys ---
    madeonsol_api_key: Optional[str] = field(default_factory=lambda: _env("MADEONSOL_API_KEY"))
    mobula_api_key: Optional[str] = field(default_factory=lambda: _env("MOBULA_API_KEY"))
    # Swapped from LunarCrush (Sept 7, 2026) -- LunarCrush's real price is
    # $90/month (Ali's earlier $24/mo was a mis-statement, confirmed against
    # LunarCrush's own pricing page), too much for a $0-target build. Adanos
    # (adanos.org) has a real free tier with actual API access -- see
    # layers/layer3_backing_check.py's docstring for what changed in the
    # actual layer logic, not just the account.
    adanos_api_key: Optional[str] = field(default_factory=lambda: _env("ADANOS_API_KEY"))
    fomoapi_api_key: Optional[str] = field(default_factory=lambda: _env("FOMOAPI_API_KEY"))
    telegram_bot_token: Optional[str] = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: Optional[str] = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # Ali's own wallet addresses, one per chain, PUBLIC address only.
    # Comma-separated "chain:address" pairs, e.g.
    # "solana:Abc123...,base:0xdef...,ethereum:0xdef..."
    wallet_addresses_raw: Optional[str] = field(default_factory=lambda: _env("WALLET_ADDRESSES"))

    # --- Optional / not in Part A but needed for Layer 4 ---
    # CryptoPanic requires its own free auth_token (Ali didn't list this in
    # Part A -- flagged separately in the README/report).
    cryptopanic_auth_token: Optional[str] = field(default_factory=lambda: _env("CRYPTOPANIC_AUTH_TOKEN"))

    # Birdeye is explicitly optional-only per spec -- never a hard dependency.
    birdeye_api_key: Optional[str] = field(default_factory=lambda: _env("BIRDEYE_API_KEY"))

    # Upstash Redis (REST API) -- cross-poll-cycle state for Layers 6/8/9
    # (previous balances/prices/MC history to diff against). Chosen over
    # GitHub's own actions/cache (not built for read-update-save-back within
    # a workflow, 7-day eviction) and committing state back via git (merge
    # conflicts on overlapping runs, rewrites the repo every cycle). Not in
    # the original Part A list -- the one new account beyond the original 8.
    upstash_redis_rest_url: Optional[str] = field(default_factory=lambda: _env("UPSTASH_REDIS_REST_URL"))
    upstash_redis_rest_token: Optional[str] = field(default_factory=lambda: _env("UPSTASH_REDIS_REST_TOKEN"))

    # --- Base URLs (verified against public docs as of Sep 2026) ---
    madeonsol_base_url: str = "https://madeonsol.com/api/v1"
    mobula_base_url: str = "https://api.mobula.io"
    adanos_base_url: str = "https://api.adanos.org"
    fomoapi_base_url: str = "https://api.fomoapi.io"
    cryptopanic_base_url: str = "https://cryptopanic.com/api/developer/v2"
    jupiter_quote_base_url: str = "https://lite-api.jup.ag/swap/v1"  # free tier, no key
    # StonkFun (Sept 22, 2026 addition) -- confirmed public, free, no API key,
    # no signup, 300 req/min per IP (see layers/layer0c_stonkfun_scoring.py
    # docstring). Not gated behind a config flag/key like MadeOnSol/Mobula
    # because there's genuinely no account or key involved.
    stonkfun_base_url: str = "https://www.stonkfun.xyz/api/public/v1"
    binance_announcements_url: str = (
        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    )
    coinbase_products_url: str = "https://api.exchange.coinbase.com/products"

    http_timeout_seconds: int = 20

    def wallet_addresses(self) -> dict:
        """Returns {chain: address} from WALLET_ADDRESSES env var."""
        out = {}
        if not self.wallet_addresses_raw:
            return out
        for pair in self.wallet_addresses_raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            chain, addr = pair.split(":", 1)
            out[chain.strip().lower()] = addr.strip()
        return out

    # --- Layer readiness checks, used by the report/checklist output ---
    def layer0_ready(self) -> dict:
        return {
            "solana_rhc": bool(self.madeonsol_api_key),
            "base_bsc_ton_eth": bool(self.mobula_api_key),
        }

    def layer1_ready(self) -> bool:
        return bool(self.madeonsol_api_key)

    def layer4_ready(self) -> dict:
        return {
            "cryptopanic": bool(self.cryptopanic_auth_token),
            "exchange_feeds": True,  # no key needed for Binance/Coinbase public endpoints
        }

    def layer3_ready(self) -> bool:
        return bool(self.adanos_api_key)

    def state_backend(self) -> str:
        if self.upstash_redis_rest_url and self.upstash_redis_rest_token:
            return "upstash"
        return "local_json"

    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


CONFIG = Config()

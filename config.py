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

    # Layer 12 -- Telegram caller-channel monitoring (Ali, Sept 27 2026 push:
    # "there IS a free path for the same underlying goal" as Twitter buzz,
    # via the public Telegram channels most real pump.fun call activity
    # actually happens in). A SEPARATE bot from telegram_bot_token above --
    # that bot sends alerts OUT to Ali's own chat; this one needs to be
    # invited as an ADMIN of each target caller channel to receive its posts
    # via getUpdates (Telegram's Bot API has no way to read a channel's
    # messages otherwise -- confirmed against their own docs; MTProto/
    # Telethon with a real phone-authenticated user session could read
    # public channel history without that step, but that's materially
    # heavier and riskier -- a real user account, not just a bot token --
    # so this starts with the plain Bot API path). See
    # layers/layer12_caller_channels.py's module docstring for the real,
    # honest reason the channel list itself is NOT filled in here with
    # anything scraped from "best telegram groups" listicle sites -- those
    # have no verified call/outcome track record, several are paid
    # placements, and this codebase has a standing rule against building on
    # unverified data. Ali fills this in himself with channels he already
    # knows have a real track record (he already follows 13-20+ traders on
    # the Fomo app for the same underlying signal -- likely overlapping
    # people/channels).
    telegram_caller_bot_token: Optional[str] = field(default_factory=lambda: _env("TELEGRAM_CALLER_BOT_TOKEN"))
    # Comma-separated numeric Telegram chat IDs (NOT @usernames -- getUpdates'
    # channel_post.chat.id is always numeric). Get a channel's real ID by
    # adding the caller bot as admin, having anyone post once, then reading
    # https://api.telegram.org/bot<TOKEN>/getUpdates in a browser -- the
    # chat.id shown there is the real one to use, never guessed.
    telegram_caller_channel_ids_raw: Optional[str] = field(default_factory=lambda: _env("TELEGRAM_CALLER_CHANNEL_IDS"))

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

    # GoPlus Security (added Sept 25 2026) -- fallback security scan for BSC/
    # Base tokens when Mobula's own Pulse response has no "security" object
    # for a token (caught live: Ali's real named-coin backtest had LP/curve
    # and mint/freeze authority coming back "unknown" for BOTH real winners
    # scored tonight -- Mobula genuinely wasn't returning security data for
    # them, not a code bug). GoPlus's token_security API now requires an
    # Authorization: Bearer <key> header per its current docs
    # (docs.gopluslabs.io/reference/tokensecurityusingget_1) -- optional here,
    # same pattern as every other key in this file: without it, this layer
    # just tries the endpoint unauthenticated (works for some accounts/rate
    # limits per GoPlus's own historical public access, unconfirmed for sure
    # until tested live) and falls back to "unknown" exactly like before if
    # that also fails -- never a hard dependency, never blocks scoring.
    goplus_api_key: Optional[str] = field(default_factory=lambda: _env("GOPLUS_API_KEY"))

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
    # CryptoPanic's free plan was discontinued (Sept 28 2026, see
    # layers/layer4_news.py's fetch_cryptopanic_posts docstring) -- CoinDesk's
    # public RSS feed is the free, keyless, no-signup replacement Layer 4 now
    # uses. Been running for years, real uptime, no key/account needed.
    coindesk_rss_url: str = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    jupiter_quote_base_url: str = "https://lite-api.jup.ag/swap/v1"  # free tier, no key
    # StonkFun (Sept 22, 2026 addition) -- confirmed public, free, no API key,
    # no signup, 300 req/min per IP (see layers/layer0c_stonkfun_scoring.py
    # docstring). Not gated behind a config flag/key like MadeOnSol/Mobula
    # because there's genuinely no account or key involved.
    stonkfun_base_url: str = "https://www.stonkfun.xyz/api/public/v1"
    goplus_base_url: str = "https://api.gopluslabs.io/api/v1"
    # GeckoTerminal (Ali, Sept 28 2026) -- Mobula's free plan is confirmed
    # dead (HTTP 403 on /api/2/pulse), leaving Layer 0b with zero real
    # BSC/Base discovery. GeckoTerminal's public API (apiguide.
    # geckoterminal.com) is free, keyless, 30 calls/min, no signup --
    # used as scheduler.py's fallback discovery source for those two
    # chains specifically (see layer0_scoring.fetch_geckoterminal_new_pools).
    geckoterminal_base_url: str = "https://api.geckoterminal.com/api/v2"
    binance_announcements_url: str = (
        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    )
    coinbase_products_url: str = "https://api.exchange.coinbase.com/products"
    # Birdeye Data Services -- free Standard tier ($0/mo, 30K compute
    # units, 1 req/sec, no card), used only by backtest_point_in_time.py
    # for historical OHLCV around a token's real launch window. See that
    # file's docstring for the honest caveat: Birdeye's own docs disagree
    # with themselves on whether /defi/ohlcv is included at the free
    # Standard tier or Starter+ only -- unconfirmed until Ali's real free
    # key hits it.
    birdeye_base_url: str = "https://public-api.birdeye.so"

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

    def telegram_caller_channel_ids(self) -> list:
        raw = self.telegram_caller_channel_ids_raw
        if not raw:
            return []
        out = []
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                out.append(int(piece))
            except ValueError:
                continue
        return out

    def layer12_ready(self) -> bool:
        return bool(self.telegram_caller_bot_token and self.telegram_caller_channel_ids())


CONFIG = Config()

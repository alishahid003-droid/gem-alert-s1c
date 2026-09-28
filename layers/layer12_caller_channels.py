"""
Layer 12 -- Telegram caller-channel monitoring.

Ali's own push, Sept 27 2026: Layer 11's DexScreener-boosts buzz proxy is
honest but not what was actually asked for (real Twitter/X buzz), and
Twitter/X itself is confirmed genuinely paid-only in 2026 (see
layer11_social_buzz.py's docstring -- free tier is write-only, real
read/search needs $100+/mo, and every scraping workaround researched is
either dead or metered/paid). Ali's real insight: most actual pump.fun
"caller"/signal activity in 2026 doesn't happen on Twitter at all -- it
happens in public Telegram channels, which Telegram's own Bot API covers
for FREE, with no ToS risk (unlike scraping X).

HOW THIS ACTUALLY WORKS, READ BEFORE WIRING IT UP:
Telegram's Bot API has no "read any public channel's history" call -- a
bot only receives a channel's posts via getUpdates/webhook once it has
been added as an ADMIN of that channel (confirmed against
core.telegram.org/bots/api). That's a real, one-time manual step per
channel, not something this code can do for you. MTProto (Telethon) with a
real phone-authenticated user session CAN read any public channel's
history without that step, but that trades a bot token for a real user
account's session -- materially heavier and riskier, so this starts with
the plain Bot API + admin-invite path, which is what Ali's own framing
("a Telegram bot/client reading their public messages") describes anyway.

THE CHANNEL LIST ITSELF IS NOT GUESSED HERE. Real research done while
building this (Sept 28 2026) turned up only SEO listicle sites ("Best
Solana Telegram Groups 2026," etc.) ranking channels -- none of them cite
a verified call/outcome track record, several read like paid placements,
and this codebase has a standing rule against building on unconfirmed data
(see pumpfun_trades.py, layer0d_point_in_time.py for the same discipline
applied elsewhere). Filling TELEGRAM_CALLER_CHANNEL_IDS with names pulled
from a listicle would be exactly the kind of guess this system otherwise
refuses to make. Ali already follows 13-20+ traders on the Fomo app for
this same underlying signal (see the S1c profile) -- he is the one with
real visibility into which channels actually have a track record, so this
layer is built channel-agnostic and CONFIG-driven: it works the moment
real channel IDs are supplied, and does nothing (fails closed, not
guessed) until they are. See config.py's own docstring for
TELEGRAM_CALLER_CHANNEL_IDS for how to get a channel's real numeric ID.

WHAT THIS BUYS: a "Caller" tag riding alongside a real alert, same
convention as Backing/Buzz/Dev holding -- NEVER folded into the 100-point
structural score itself (a token still has to pass real structural scoring
on its own merits; a caller mention is corroborating color, not a
detection mechanism of its own -- a channel calling a token says nothing
about whether that token is actually safe).
"""
import re
from dataclasses import dataclass
from typing import List, Optional

from config import CONFIG
from utils.http import get_json

TELEGRAM_API_BASE = "https://api.telegram.org"

# Solana addresses are base58-encoded, 32-44 chars (base58 excludes 0, O,
# I, l to avoid visual ambiguity). EVM addresses are a fixed-width 0x + 40
# hex chars. HONEST CAVEAT: a base58 string this length could be something
# else entirely (e.g. a transaction signature is also base58, just longer
# at 87-88 chars, so the length bound here excludes those, but a
# coincidentally-matching random word is still possible) -- this is a
# pattern match on free-form chat text, not verification. Nothing
# downstream trusts an extracted address by itself; see module docstring.
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# How long a caller mention stays "live" for scheduler._handle_scored to
# tag onto a real alert -- 2 hours, same rough order as Layer 8's momentum
# window, since a call from yesterday isn't "just called," it's history.
CALLER_SIGNAL_WINDOW_SECONDS = 2 * 3600


@dataclass
class CallerPost:
    channel_id: int
    channel_name: str
    text: str
    date: Optional[int]  # Telegram's own unix timestamp for the post


def fetch_caller_channel_posts(offset: Optional[int] = None) -> dict:
    """One getUpdates call against the caller bot, filtered down to
    channel_post updates from CONFIG.telegram_caller_channel_ids() only --
    a bot added as admin to unrelated channels for some other reason
    shouldn't leak those posts in here. `offset` is Telegram's own
    at-least-this-update_id-onward cursor (same pagination model as
    pumpfun_trades.fetch_recent_signatures' `before` cursor, just the
    opposite direction) -- pass state's stored next_offset to avoid
    reprocessing the same updates every cycle; Telegram also auto-clears
    any update already acknowledged via a higher offset. Returns
    {"ok": True, "posts": [...], "next_offset": N} on success, or
    {"ok": False, "reason": ...} if not configured or the call failed --
    never raises, same fail-closed convention as every other optional
    layer in this codebase."""
    if not CONFIG.telegram_caller_bot_token:
        return {"ok": False, "reason": "TELEGRAM_CALLER_BOT_TOKEN not configured"}
    channel_ids = CONFIG.telegram_caller_channel_ids()
    if not channel_ids:
        return {"ok": False, "reason": "TELEGRAM_CALLER_CHANNEL_IDS not configured -- "
                                        "see config.py's docstring for how to get real channel IDs"}
    params = {"timeout": 0, "allowed_updates": '["channel_post"]'}
    if offset is not None:
        params["offset"] = offset
    result = get_json(f"{TELEGRAM_API_BASE}/bot{CONFIG.telegram_caller_bot_token}/getUpdates",
                       params=params, timeout=CONFIG.http_timeout_seconds)
    if not result.get("ok"):
        return {"ok": False, "reason": f"Telegram getUpdates request failed (HTTP {result.get('status_code')})"}
    body = result.get("json") or {}
    if not body.get("ok"):
        return {"ok": False, "reason": f"Telegram API error: {body.get('description', 'unknown')}"}

    updates = body.get("result") or []
    posts: List[CallerPost] = []
    max_update_id = (offset - 1) if offset is not None else None
    for u in updates:
        update_id = u.get("update_id")
        if isinstance(update_id, int) and (max_update_id is None or update_id > max_update_id):
            max_update_id = update_id
        post = u.get("channel_post")
        if not isinstance(post, dict):
            continue
        chat = post.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id not in channel_ids:
            continue  # not one of the configured caller channels -- ignore
        text = post.get("text") or post.get("caption") or ""
        if not text:
            continue
        posts.append(CallerPost(channel_id=chat_id, channel_name=chat.get("title") or str(chat_id),
                                 text=text, date=post.get("date")))
    next_offset = (max_update_id + 1) if max_update_id is not None else offset
    return {"ok": True, "posts": posts, "next_offset": next_offset}


def extract_token_addresses(text: str) -> dict:
    """Pure function -- pulls candidate Solana/EVM addresses out of one
    message's free text. Returns {"solana": [...], "evm": [...]}, each a
    sorted list of distinct matches (EVM addresses lowercased for
    consistent comparison elsewhere). See module docstring's caveat on
    false positives -- this is a pattern match, not a verified extraction."""
    solana_matches = set(SOLANA_ADDRESS_RE.findall(text or ""))
    evm_matches = {m.lower() for m in EVM_ADDRESS_RE.findall(text or "")}
    return {"solana": sorted(solana_matches), "evm": sorted(evm_matches)}

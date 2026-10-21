"""layers/layer21_celeb_signal_watch.py -- Layer 21: celebrity / known-influencer
real-time signal watch.

THE GAP THIS CLOSES
--------------------
Ali's question: "Trump posting something, Musk posting, saying something,
known influencers, known developers, known celebrities -- how are we getting
this sorted?"

Two things ALREADY exist that are adjacent but don't cover this:
  - layer17's EARLY_FAMOUS_NAMES: matches a famous name against a brand-new
    coin's own TICKER/DESCRIPTION. It fires when someone ELSE launches a coin
    *named* "Trump" or "Musk" -- it does not watch Trump or Musk themselves.
  - layer18 (virality/catalyst): watches Google Trends / Wikipedia / Bluesky /
    Mastodon trending-term feeds for a name waking up, matched against a
    DORMANT coin (age >= 12h). It has no visibility into same-minute posts.

The actual blind spot is X/Twitter itself -- that's where Trump, Musk, and
almost every crypto-relevant influencer or dev posts FIRST, and this
project's own trend-feed research already proved X returns nothing (no
official API without payment, no reliable scrape). That blind spot is real
and this layer does not pretend to close it directly.

THE WORKABLE FIX
-----------------
On-chain-intel accounts (Lookonchain, Whale Insider, SpotOnChain, EmberCN,
Watcher.Guru, etc.) exist specifically to catch "known person did X on-chain"
moments FAST, and most of them cross-post the same alpha to a PUBLIC Telegram
channel, often within seconds of their X post -- because Telegram is their
distribution channel too, not just X's. Telegram is not banned for Ali and
needs no paid API: a free developer credential (api_id/api_hash from
https://my.telegram.org, free, self-serve, one-time) plus the Telethon
library lets us read public channel messages in real time as a normal
Telegram user, legally and for free. No X account, no X API, no cost.

This layer:
  1. Reads a list of public Telegram channel usernames (CHANNELS below --
     grow it any time, same convention as notable_wallets.json).
  2. Scans each new message for TWO things together: a known/famous name
     (same EARLY_FAMOUS_NAMES pattern layer17 already uses, so "Trump",
     "Musk", "Vitalik", a tracked dev/influencer, etc.) AND something
     that looks like a fresh Solana mint address or a "launched/sent/buy"
     verb near it.
  3. On a hit, fires an immediate alert (never claims the named person did
     anything themselves unless the message says so verbatim) and logs the
     outcome, same shadow-mode discipline as layer18/19/20.

SETUP (one-time, free, Ali does this himself -- no third party, no cost):
  1. Go to https://my.telegram.org, log in with your own phone number,
     create an app -> copy the api_id and api_hash it gives you.
  2. Add to .env:  TELEGRAM_API_ID=...   TELEGRAM_API_HASH=...
  3. Run once, interactively, to create the session file (asks for the
     Telegram login code sent to your phone -- one time only):
         python telegram_login_setup.py
  4. From then on, run_cycle() below runs headlessly forever, same as every
     other layer.

Until that one-time setup is done, run_cycle() fails closed (same pattern as
layer20 with an empty registry) rather than silently doing nothing.
"""
import json
import os
import re
import time

OUT_DIR = "grad_runners"
LOG_PATH = f"{OUT_DIR}/layer21_celeb_signal_log.jsonl"

# Grow this list any time -- these are real, existing public Telegram channels
# known for fast on-chain "known wallet / known person" alpha. Verify/add more
# via each account's own profile (most on-chain-intel X accounts link their
# Telegram in their bio).
CHANNELS = [
    "lookonchain",
    "whale_insider",
    "spotonchain",
    "embercn",
]

# Same famous-name pattern layer17 already uses (politics/tech leaders/
# AI+brands) plus Ali's own tracked-trader roster, so a hit on either a world
# celebrity or one of the wallets/traders Ali already follows counts.
try:
    from layers.layer17_early_entry import _FAMOUS_DEFAULT
except Exception:
    _FAMOUS_DEFAULT = (
        "trump|elon|musk|vance|biden|obama|putin|zelensky|zuckerberg|bezos|altman|durov|vitalik|satoshi"
    )

try:
    from layers.roster import SELL_WATCH_ROSTER
except Exception:
    SELL_WATCH_ROSTER = set()

_NAME_RX = re.compile(
    _FAMOUS_DEFAULT + ("|" + "|".join(re.escape(n) for n in SELL_WATCH_ROSTER) if SELL_WATCH_ROSTER else ""),
    re.I,
)

# A Solana mint address: base58, 32-44 chars, no 0/O/I/l.
_MINT_RX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

# Verbs that suggest the message is about a FRESH on-chain event, not just
# generic chat mentioning a famous name.
_ACTION_RX = re.compile(
    r"\b(launch|launched|sent|transfer|deploy|deployed|buy|bought|sniped|mint|minted|wallet|holds?|holding)\b",
    re.I,
)


def _log(entry: dict):
    os.makedirs(OUT_DIR, exist_ok=True)
    entry = dict(entry)
    entry.setdefault("ts", time.time())
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def scan_message(channel: str, text: str, msg_id=None, msg_ts=None):
    """Return a hit dict if this message names a known person/trader AND a
    fresh on-chain action/mint, else None. Pure function -- no network, no
    side effects -- so it's trivially testable without a live Telegram
    connection."""
    if not text:
        return None
    names = sorted(set(m.group(0) for m in _NAME_RX.finditer(text)))
    if not names:
        return None
    mints = sorted(set(_MINT_RX.findall(text)))
    has_action = bool(_ACTION_RX.search(text))
    if not mints and not has_action:
        return None
    return {
        "channel": channel,
        "msg_id": msg_id,
        "msg_ts": msg_ts,
        "names": names,
        "mints": mints,
        "text": text[:500],
    }


def format_alert(hit: dict) -> str:
    """Never claims the named person did anything themselves beyond what the
    source message itself says -- this is an early-warning flag on a public
    message, not a confirmation of involvement."""
    names = ", ".join(hit["names"])
    mint_line = f"\nMint(s) seen: {', '.join(hit['mints'])}" if hit["mints"] else ""
    return (
        f"[CELEB-SIGNAL] \"{names}\" mentioned with an on-chain action in "
        f"@{hit['channel']} (unverified -- this is the channel's own post, not "
        f"a confirmation the named person is involved).{mint_line}\n"
        f"Source text: {hit['text']}"
    )


def fetch_recent_messages(client, channel: str, limit: int = 30):
    """Thin Telethon integration point. `client` is a connected
    telethon.TelegramClient; kept as a separate function so tests can pass a
    fake client with zero network/session dependency."""
    messages = []
    for m in client.iter_messages(channel, limit=limit):
        if getattr(m, "text", None):
            messages.append((channel, m.text, m.id, m.date.timestamp() if m.date else None))
    return messages


def run_cycle(client=None, send_alert=None):
    """One poll cycle. Fails closed (same discipline as layer20) if no
    Telegram client/session is set up yet, rather than silently no-op'ing
    forever without telling anyone why nothing is happening."""
    if client is None:
        try:
            api_id = os.environ.get("TELEGRAM_API_ID")
            api_hash = os.environ.get("TELEGRAM_API_HASH")
            if not api_id or not api_hash:
                return "layer21 disabled: TELEGRAM_API_ID/TELEGRAM_API_HASH not set in .env -- run telegram_login_setup.py once (see module docstring)"
            from telethon.sync import TelegramClient  # type: ignore
            session_path = f"{OUT_DIR}/celeb_watch_session"
            client = TelegramClient(session_path, int(api_id), api_hash)
            client.connect()
            if not client.is_user_authorized():
                return "layer21 disabled: Telegram session not authorized yet -- run telegram_login_setup.py once interactively"
        except ImportError:
            return "layer21 disabled: telethon not installed -- pip install telethon"

    hits = []
    for ch in CHANNELS:
        try:
            for channel, text, msg_id, msg_ts in fetch_recent_messages(client, ch):
                hit = scan_message(channel, text, msg_id, msg_ts)
                if hit:
                    hits.append(hit)
                    _log(hit)
                    if send_alert:
                        send_alert(format_alert(hit))
        except Exception as e:
            _log({"channel": ch, "error": f"{type(e).__name__}: {e}"})
    return f"layer21 cycle done: {len(hits)} hit(s) across {len(CHANNELS)} channel(s)"

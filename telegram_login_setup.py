"""telegram_login_setup.py -- run this ONCE, interactively, to turn on Layer 21
(celebrity/known-person signal watch via public Telegram channels).

What it does: logs your own Telegram account in via Telethon and saves a
session file (grad_runners/celeb_watch_session.session) so every future run
is headless -- no code prompt again unless you log out or delete that file.

Before running this:
  1. Go to https://my.telegram.org (free, your own phone number, no payment).
  2. Log in -> "API development tools" -> create an app (any name/platform).
  3. Copy the api_id (a number) and api_hash (a long string) it shows you.
  4. Add two lines to your .env file:
         TELEGRAM_API_ID=12345678
         TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789

Then run:  python telegram_login_setup.py
It will ask for the Telegram login code sent to your phone (same as logging
into Telegram Web) -- enter it, and you're done. Never run this on a shared
or untrusted machine; the session file it creates has full read access to
your Telegram account exactly like being logged into Telegram Web.
"""
import os


def _load_dotenv():
    try:
        for line in open(".env", encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


_load_dotenv()

api_id = os.environ.get("TELEGRAM_API_ID")
api_hash = os.environ.get("TELEGRAM_API_HASH")

if not api_id or not api_hash:
    raise SystemExit(
        "TELEGRAM_API_ID / TELEGRAM_API_HASH not found in .env.\n"
        "Get them free from https://my.telegram.org first -- see this file's docstring."
    )

try:
    from telethon.sync import TelegramClient
except ImportError:
    raise SystemExit("telethon not installed. Run: pip install telethon")

os.makedirs("grad_runners", exist_ok=True)
session_path = "grad_runners/celeb_watch_session"

with TelegramClient(session_path, int(api_id), api_hash) as client:
    me = client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username or 'no-username'})")
    print("Session saved. Layer 21 can now run headlessly -- no need to run this again.")

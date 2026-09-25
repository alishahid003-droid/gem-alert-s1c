"""
Layer 0b speed upgrade (Ali, Sept 25 2026 recommendation list, item 1 of 3:
"Speed -- Layer 0b REST polling -> Mobula's real-time Pulse WebSocket
stream"). This module is the WebSocket CLIENT only -- connect, subscribe,
receive, reconnect. worker_pulse_websocket.py (separate file) is the
runnable daemon that wires received tokens into the exact same scoring/
alert/execution pipeline scheduler.py's REST-based Layer 0b already uses.

REAL, HARD BLOCKER, confirmed against Mobula's own docs (Sept 25 2026,
docs.mobula.io/indexing-stream/stream/websocket/pulse-stream-v2):

    "This endpoint is only available to Growth and Enterprise plans."

A free-tier MOBULA_API_KEY (which is what this system has used everywhere
else so far) will NOT be accepted here -- the server will reject the
subscribe message or close the connection. This module still handles
that failure honestly (surfaces the raw close/error, does not retry
forever on an auth rejection, does not pretend it's connected) rather
than silently doing nothing. Whether to upgrade the Mobula plan to unlock
this is Ali's call, not something built around or guessed at.

SECOND real caveat: Mobula's own docs page for this endpoint listed
`evm:8453` (Base) and `solana:solana` (Solana) as its example supported
chains -- `evm:56` (BSC) was NOT mentioted as confirmed supported. BSC is
included in the default subscription here anyway (it's what Layer 0b's
REST path already covers), but this is UNVERIFIED against a live
Growth-tier connection. The worker logs the raw `init` message it gets
back for each view on connect specifically so this can be checked
immediately once real access exists, rather than assumed either way.

Message schema note: the docs describe `new-token`/`update-token` payloads
as carrying a `token` object in the same `TokenDataSchema` shape as
Mobula's REST Pulse response (the same schema `layers/layer0_scoring.py`'s
signals_from_mobula_pulse already parses correctly, per its own Sept 24-25
2026 field-name fixes). This module reuses that exact same parsing/scoring
code (score_mobula_pulse_items) rather than writing a second, parallel
parser -- so if the WS schema really does match the REST schema, scoring
is correct on day one; if it doesn't, the mismatch shows up as signals
reading None/unknown (same visible failure mode bug #5 had), not a silent
wrong number, and is fixable in one place.

Requires the `websockets` package (already present in this dev environment,
added to requirements.txt).
"""
import asyncio
import json
import time
from typing import AsyncIterator, Callable, Optional

WS_URL = "wss://api.mobula.io"
PING_INTERVAL_SECONDS = 30
RECONNECT_BACKOFF_SECONDS = [5, 15, 30, 60, 120]  # caps at 120s, does not give up

# Mirrors scheduler.py's MOBULA_PULSE_CHAINS -- kept as a separate constant
# here (not imported) so this module has no dependency on scheduler.py,
# only the reverse: worker_pulse_websocket.py depends on both.
DEFAULT_CHAIN_IDS = ["evm:56", "evm:8453"]  # BSC, Base -- see module docstring re: BSC unconfirmed on this endpoint
CHAIN_ID_TO_NAME = {"evm:56": "bsc", "evm:8453": "base", "solana:solana": "solana"}


def build_subscribe_message(api_key: str, chain_ids: list = None,
                             max_updates_per_minute: int = 30) -> dict:
    """The exact message to send as the FIRST frame after connecting --
    see module docstring for the source of this shape. max_updates_per_minute
    throttles update-token spam per the docs' `maxUpdatesPerMinute`/`coalesce`
    mechanism; 30/min (one update per token per ~2s at most) is a deliberate
    middle ground -- fast enough to be a real speed upgrade over REST's
    multi-minute poll interval, not so fast it re-triggers scoring on every
    micro price tick."""
    return {
        "type": "pulse-v2",
        "authorization": api_key,
        "payload": {
            "model": "default",  # auto-creates the new/bonding/bonded views, same 3-bucket
                                  # split flatten_mobula_pulse_response already expects from REST
            "assetMode": True,
            "chainId": chain_ids or DEFAULT_CHAIN_IDS,
            "compressed": False,
            "maxUpdatesPerMinute": max_updates_per_minute,
            "coalesce": True,
        },
    }


async def stream_pulse_events(
    api_key: str,
    on_new_token: Callable[[dict, str], None],
    on_status: Optional[Callable[[str], None]] = None,
    chain_ids: list = None,
    max_updates_per_minute: int = 30,
    _connect_fn=None,
    _max_iterations: Optional[int] = None,
) -> None:
    """Runs forever (until cancelled), reconnecting with backoff on any
    disconnect. Calls on_new_token(token_payload, chain_name) for every
    real "new-token" event -- deliberately NOT for "update-token"/"sync"
    (see module docstring: those fire far more often than REST ever did
    and would turn "faster discovery" into alert/execution spam on every
    price tick for a token already seen). on_status(msg) is an optional
    callback for human-readable connection-state lines (connecting,
    connected, subscribed <view>, auth rejected, disconnected: <reason>,
    reconnecting in Ns) -- the worker script uses this to print/log.

    _connect_fn and _max_iterations exist only so tests can inject a fake
    websocket connection and bound the loop instead of running forever.
    """
    try:
        import websockets  # type: ignore
    except ImportError:
        raise RuntimeError("websockets package not installed -- pip install websockets (see requirements.txt)")

    connect_fn = _connect_fn or websockets.connect
    attempt = 0
    iterations = 0

    while True:
        if _max_iterations is not None and iterations >= _max_iterations:
            return
        iterations += 1
        try:
            if on_status:
                on_status(f"connecting to {WS_URL}")
            async with connect_fn(WS_URL) as ws:
                attempt = 0  # reset backoff on a successful connect
                subscribe_msg = build_subscribe_message(api_key, chain_ids, max_updates_per_minute)
                await ws.send(json.dumps(subscribe_msg))
                if on_status:
                    on_status(f"subscribed: chains={chain_ids or DEFAULT_CHAIN_IDS} "
                               f"max_updates_per_minute={max_updates_per_minute}")

                last_ping = time.monotonic()
                async for raw in ws:
                    now = time.monotonic()
                    if now - last_ping >= PING_INTERVAL_SECONDS:
                        await ws.send(json.dumps({"event": "ping"}))
                        last_ping = now

                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    _dispatch_message(msg, on_new_token, on_status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any connection/protocol error triggers reconnect, never a crash
            if on_status:
                on_status(f"disconnected: {exc}")
            delay = RECONNECT_BACKOFF_SECONDS[min(attempt, len(RECONNECT_BACKOFF_SECONDS) - 1)]
            attempt += 1
            if on_status:
                on_status(f"reconnecting in {delay}s")
            if _max_iterations is not None:
                return  # tests: don't actually sleep/loop forever
            await asyncio.sleep(delay)


def _dispatch_message(msg: dict, on_new_token: Callable[[dict, str], None],
                       on_status: Optional[Callable[[str], None]]) -> None:
    msg_type = msg.get("type")
    if msg_type == "new-token":
        payload = msg.get("payload") or {}
        token = payload.get("token") or {}
        chain_id = token.get("chainId") or token.get("chain_id")
        chain_name = CHAIN_ID_TO_NAME.get(chain_id, chain_id)
        if token:
            on_new_token(token, chain_name)
    elif msg_type == "init":
        payload = msg.get("payload") or {}
        view = payload.get("viewName") or payload.get("name")
        count = len(payload.get("tokens") or payload.get("data") or [])
        if on_status:
            on_status(f"init: view={view} tokens={count} -- confirms this view is live for this account/plan")
    elif msg_type in ("sync", "update-token", "remove-token"):
        pass  # deliberately not scored -- see stream_pulse_events docstring
    elif msg_type == "error":
        if on_status:
            on_status(f"server error message: {msg.get('payload') or msg}")
    # any other/unknown message type is ignored rather than raising -- a
    # protocol addition on Mobula's side should never crash this worker

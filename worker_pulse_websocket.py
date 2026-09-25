"""
Layer 0b speed upgrade -- runnable daemon (Ali, Sept 25 2026: "Speed --
Layer 0b REST polling -> Mobula's real-time Pulse WebSocket stream").

WHAT THIS IS: a long-lived process that keeps one WebSocket connection to
Mobula's Pulse Stream V2 open and scores/alerts on each genuinely NEW token
the instant it's seen, instead of waiting for the next REST poll cycle
(several minutes, per poll-fast.yml's schedule). Reuses scheduler.py's
existing `_handle_scored` -- the exact same scoring/alert/Stage-1-execution
wiring the REST Layer 0b path already uses -- so a token discovered here is
handled identically to one discovered via REST, just faster.

MUST run as a persistent process on Ali's own machine (or a small VPS),
NOT on GitHub Actions -- same reasoning as run_poll_madeonsol's docstring,
one level further: GitHub Actions runs are short-lived jobs on a schedule
(poll-fast.yml/poll-slow.yml), they start, run once, and exit. A WebSocket
connection needs a process that stays up between events, which a scheduled
job fundamentally cannot provide. Schedule via Windows Task Scheduler as a
process that starts once and keeps running (not a recurring short task):

    schtasks /create /tn "GemAlert PulseStream" /sc onstart ^
        /tr "cmd /c cd /d C:\\path\\to\\gem-alert-s1c_6 && python worker_pulse_websocket.py" ^
        /ru "%USERNAME%"

...or simpler for now: just run `python worker_pulse_websocket.py` in its
own terminal window and leave it open.

TWO REAL BLOCKERS, stated plainly rather than guessed around (see
mobula_pulse_stream.py's docstring for the full detail):
  1. Pulse Stream V2 is Growth/Enterprise-plan only per Mobula's own docs.
     A free MOBULA_API_KEY will not be accepted -- this worker will print
     the exact rejection it gets back and keep retrying with backoff
     rather than silently hanging, so the failure is visible immediately.
  2. BSC (evm:56) coverage on this specific endpoint is unconfirmed by
     Mobula's docs (only Base and Solana were listed as examples) -- the
     worker logs the raw `init` message per view on connect specifically
     so this is verifiable the moment real access exists.

Deduplication: only "new-token" events trigger scoring (see
mobula_pulse_stream.py's stream_pulse_events docstring) -- update-token/
sync events (which fire far more often than a REST poll ever did) are
intentionally not re-scored, so this is a speed upgrade for DISCOVERY,
not a firehose that re-fires Stage 1/2 on every price tick for tokens
already seen. state.set_last_score / handle_stage1_candidate's own
already-fired checks (unchanged, same as the REST path) still guard
against a genuine duplicate new-token event re-buying.

The boost board (Layer 11 buzz enrichment) is refreshed on a timer rather
than once at startup, since this process runs for hours/days, not one
poll cycle -- see BOARD_REFRESH_SECONDS below.
"""
import asyncio
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import CONFIG
from mobula_pulse_stream import stream_pulse_events, DEFAULT_CHAIN_IDS
from layers.layer0_scoring import score_mobula_pulse_items
from layers.layer11_social_buzz import fetch_boost_board
import state

BOARD_REFRESH_SECONDS = 5 * 60  # Layer 11's boost board is cheap/keyless -- refreshed
                                 # every 5 min so buzz enrichment doesn't go stale over
                                 # a multi-hour run, without hammering DexScreener

_board_state = {"board": None, "fetched_at": 0.0}


def _current_board():
    now = time.time()
    if _board_state["board"] is None or (now - _board_state["fetched_at"]) >= BOARD_REFRESH_SECONDS:
        try:
            board = fetch_boost_board()
        except Exception as exc:  # noqa: BLE001 -- buzz enrichment is optional, must never take the worker down
            print(f"[pulse-ws] boost board refresh failed (non-fatal): {exc}")
            board = None
        _board_state["board"] = board if isinstance(board, dict) else None
        _board_state["fetched_at"] = now
    return _board_state["board"]


def _make_on_new_token(handle_scored_fn):
    def on_new_token(token_payload: dict, chain_name: str):
        try:
            scored_list = score_mobula_pulse_items(chain_name, [token_payload])
        except Exception as exc:  # noqa: BLE001 -- one bad payload must never kill the stream
            print(f"[pulse-ws] scoring failed for one token on {chain_name}: {exc}")
            return
        if not scored_list:
            return
        scored = scored_list[0]
        mint = scored.get("address")
        mc = (scored.get("raw") or {}).get("marketCap") or (scored.get("raw") or {}).get("market_cap")
        if mint and mc is not None:
            state.record_mc_point(mint, mc)
        try:
            sent = handle_scored_fn(scored, chain_name, source="mobula", mc=mc, board=_current_board())
        except Exception as exc:  # noqa: BLE001 -- alert/execution errors must not kill the stream
            print(f"[pulse-ws] _handle_scored raised for {mint}: {exc}")
            return
        tag = "ALERT SENT" if sent else "scored, no alert"
        print(f"[pulse-ws:{chain_name}] new-token {mint} -- {tag}")
    return on_new_token


def _on_status(msg: str):
    print(f"[pulse-ws] {msg}")


async def _run():
    if not CONFIG.mobula_api_key:
        print("[pulse-ws] BLOCKED: MOBULA_API_KEY not set -- nothing to connect with")
        return 1

    # Imported lazily, inside the function, not at module top -- scheduler.py
    # does its own heavy multi-layer import chain at import time, which is
    # fine for a one-shot poll process but this worker is meant to start
    # fast and stay up; deferring the import doesn't change correctness,
    # just keeps startup errors (if any layer's import fails) reported
    # clearly at the point this worker actually needs it.
    from scheduler import _handle_scored

    print(f"[pulse-ws] starting -- chains={DEFAULT_CHAIN_IDS} "
          f"(BSC coverage on this endpoint is UNCONFIRMED by Mobula's docs, see module docstring)")
    await stream_pulse_events(
        api_key=CONFIG.mobula_api_key,
        on_new_token=_make_on_new_token(_handle_scored),
        on_status=_on_status,
    )
    return 0


def main():
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[pulse-ws] stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())

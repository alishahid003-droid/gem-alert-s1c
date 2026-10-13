"""PumpPortal real-time stream client (free, no key): every new pump.fun launch
and the trades on the coins we choose to watch. Client only -- connect,
subscribe, receive, reconnect (same split as mobula_pulse_stream.py). The
decision logic lives in layers/layer17_early_entry.py.

Message shapes (PumpPortal docs, from memory -- VERIFY on your PC with
`python diag_early_stream.py`, it prints the raw first events):
  new token : {"txType":"create","mint","traderPublicKey","initialBuy","solAmount",
               "marketCapSol","name","symbol","pool":"pump", ...}
  trade     : {"txType":"buy"|"sell","mint","traderPublicKey","solAmount","tokenAmount",
               "marketCapSol", ...}
subscribe   : {"method":"subscribeNewToken"} / {"method":"subscribeTokenTrade","keys":[mints]}
"""
import asyncio
import json
import os
import time
from typing import Callable, Iterable, List, Optional

WS_URL = "wss://pumpportal.fun/api/data"


class EarlyStream:
    def __init__(self, on_event: Callable[[dict], None], url: str = WS_URL, connect: Optional[Callable] = None):
        self.on_event = on_event
        key = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
        self.api_key = key
        # The launch/graduation stream stays KEYLESS (free). Only the separate trade socket carries the key, because
        # PumpPortal bills the keyed feed per 10,000 messages (0.01 SOL, taken from the key's linked wallet).
        self.url = url
        self.trade_url = (url + "?api-key=" + key) if key and "api-key=" not in url else None
        self.max_msgs_day = int(os.environ.get("PUMPPORTAL_MAX_MSG_PER_DAY", "10000") or 0)   # 10,000 msgs = ~0.01 SOL
        self.max_watched = int(os.environ.get("PUMPPORTAL_MAX_WATCHED", "20") or 0)
        self.trade_ws = None
        self.trade_msgs = 0
        self.trade_day = int(time.time() // 86400)
        self.budget_exhausted = False
        self.trades_blocked = False          # PumpPortal: per-coin trade feed needs an API key funded >= 0.02 SOL
        self._connect = connect
        self.ws = None
        self.watched: set = set()
        self.connected_at: Optional[float] = None
        self.last_event_ts: Optional[float] = None
        self.reconnects = 0
        self.last_error: Optional[str] = None
        self.stop = False

    async def _send(self, payload: dict):
        if self.ws is not None:
            await self.ws.send(json.dumps(payload))

    @property
    def spend_est_sol(self) -> float:
        return round(self.trade_msgs / 10000.0 * 0.01, 6)

    async def _send_trade(self, payload: dict):
        ws = self.trade_ws if self.trade_url else self.ws
        if ws is not None:
            await ws.send(json.dumps(payload))

    async def subscribe_trades(self, mints: Iterable[str]):
        new = [m for m in mints if m not in self.watched]
        if self.trade_url:                       # metered feed: obey the budget
            if self.budget_exhausted:
                return
            if self.max_watched:
                new = new[:max(0, self.max_watched - len(self.watched))]
        if not new:
            return
        self.watched.update(new)
        await self._send_trade({"method": "subscribeTokenTrade", "keys": new})

    async def unsubscribe_trades(self, mints: Iterable[str]):
        gone = [m for m in mints if m in self.watched]
        if not gone:
            return
        self.watched.difference_update(gone)
        await self._send_trade({"method": "unsubscribeTokenTrade", "keys": gone})

    async def _run_trades(self):
        """Keyed, metered trade socket. Stops itself for the rest of the UTC day when PUMPPORTAL_MAX_MSG_PER_DAY is hit."""
        backoff = 1.0
        while not self.stop:
            day = int(time.time() // 86400)
            if day != self.trade_day:
                self.trade_day, self.trade_msgs, self.budget_exhausted = day, 0, False
            if self.budget_exhausted:
                await asyncio.sleep(60)
                continue
            try:
                connect = self._connect
                if connect is None:
                    import websockets  # type: ignore
                    connect = websockets.connect
                async with connect(self.trade_url) as ws:
                    self.trade_ws = ws
                    backoff = 1.0
                    if self.watched:
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": sorted(self.watched)}))
                    async for raw in ws:
                        self.trade_msgs += 1
                        if self.max_msgs_day and self.trade_msgs >= self.max_msgs_day:
                            self.budget_exhausted = True
                            self.watched.clear()
                            self.last_error = f"PumpPortal daily message budget reached ({self.max_msgs_day}); trade feed paused until 00:00 UTC"
                            break
                        if int(time.time() // 86400) != self.trade_day:
                            self.trade_day, self.trade_msgs = int(time.time() // 86400), 0
                        try:
                            evt = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(evt, dict):
                            continue
                        msg = str(evt.get("message") or "")
                        if "only available" in msg or "API key" in msg or "insufficient" in msg.lower():
                            self.trades_blocked = True
                            self.last_error = "PumpPortal: " + msg[:120]
                            continue
                        self.on_event(evt)
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
            finally:
                self.trade_ws = None
            if self.stop:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def run_forever(self, max_messages: Optional[int] = None):
        task = asyncio.ensure_future(self._run_trades()) if self.trade_url else None
        try:
            await self._run_main(max_messages)
        finally:
            if task is not None:
                task.cancel()

    async def _run_main(self, max_messages: Optional[int] = None):
        """Connect -> subscribe -> pump messages to on_event; reconnect with backoff.
        on_event may return a list of mints to start watching (awaited inline)."""
        backoff, seen = 1.0, 0
        while not self.stop:
            try:
                connect = self._connect
                if connect is None:
                    import websockets  # type: ignore
                    connect = websockets.connect
                async with connect(self.url) as ws:
                    self.ws = ws
                    self.connected_at = time.time()
                    backoff = 1.0
                    await self._send({"method": "subscribeNewToken"})
                    await self._send({"method": "subscribeMigration"})      # graduations: ground truth for the recorder
                    if self.watched and not self.trade_url:     # resubscribe after a drop (keyed feed has its own socket)
                        await self._send({"method": "subscribeTokenTrade", "keys": sorted(self.watched)})
                    async for raw in ws:
                        try:
                            evt = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(evt, dict):
                            continue
                        self.last_event_ts = time.time()
                        msg = str(evt.get("message") or "")
                        if "only available" in msg or "API key" in msg:
                            self.trades_blocked = True
                            continue
                        res = self.on_event(evt)
                        if res:
                            await self.subscribe_trades(res)
                        seen += 1
                        if max_messages and seen >= max_messages:
                            self.stop = True
                            return
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
            finally:
                self.ws = None
            if self.stop:
                return
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

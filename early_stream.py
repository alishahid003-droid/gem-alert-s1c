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
import time
from typing import Callable, Iterable, List, Optional

WS_URL = "wss://pumpportal.fun/api/data"


class EarlyStream:
    def __init__(self, on_event: Callable[[dict], None], url: str = WS_URL, connect: Optional[Callable] = None):
        self.on_event = on_event
        import os
        key = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
        self.url = url + (("?api-key=" + key) if key and "api-key=" not in url else "")
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

    async def subscribe_trades(self, mints: Iterable[str]):
        new = [m for m in mints if m not in self.watched]
        if not new:
            return
        self.watched.update(new)
        await self._send({"method": "subscribeTokenTrade", "keys": new})

    async def unsubscribe_trades(self, mints: Iterable[str]):
        gone = [m for m in mints if m in self.watched]
        if not gone:
            return
        self.watched.difference_update(gone)
        await self._send({"method": "unsubscribeTokenTrade", "keys": gone})

    async def run_forever(self, max_messages: Optional[int] = None):
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
                    if self.watched:                       # resubscribe after a drop
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

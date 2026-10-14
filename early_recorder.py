"""Launch recorder + outcome sampler (S1c, Oct 12 2026).

Writes every launch the stream sees, every sniper decision, and the price of each
launch at +5 / +15 / +60 minutes (DexScreener, batched) to early_log/YYYY-MM-DD.jsonl.
This is the dataset the rules are measured on: it needs no trade feed and covers
ALL launches (tens of thousands a day), not just the few we enter.
Analyse with:  python analyze_early.py
"""
import json
import os
import time
from typing import Callable, Dict, List, Optional

DIR = "early_log"
CHECKPOINTS_MIN = (5, 15, 60)


class Recorder:
    def __init__(self, directory: str = DIR, max_pending: int = 60000):
        self.dir = directory
        self.pending: Dict[str, list] = {}     # mint -> [created_ts, next_checkpoint_index]
        self.max_pending = max_pending
        os.makedirs(self.dir, exist_ok=True)

    def _write(self, row: dict, now: Optional[float] = None):
        now = now if now is not None else time.time()
        path = os.path.join(self.dir, time.strftime("%Y-%m-%d", time.gmtime(now)) + ".jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def on_grad(self, mint: str, now: Optional[float] = None):
        self._write({"t": "grad", "ts": round(now or time.time(), 1), "mint": mint}, now)

    def on_create(self, e: dict, tracked: bool, dev_list: bool, now: Optional[float] = None, extra: Optional[dict] = None):
        now = now if now is not None else time.time()
        mint = e.get("mint")
        if not mint:
            return
        self._write({"t": "create", "ts": round(now, 1), "mint": mint, "creator": e.get("traderPublicKey"),
                     "init_sol": e.get("solAmount"), "init_tokens": e.get("initialBuy"),
                     "mcap_sol": e.get("marketCapSol"), "sym": str(e.get("symbol") or "")[:16],
                     "name": str(e.get("name") or "")[:30], "tracked": tracked, "dev_list": dev_list, **(extra or {})}, now)
        if len(self.pending) < self.max_pending:
            self.pending[mint] = [now, 0]

    def on_decision(self, d: dict, now: Optional[float] = None):
        self._write({"t": "decision", "ts": round(now or time.time(), 1), **{k: d.get(k) for k in (
            "mint", "lane", "lanes", "age_s", "mcap_sol", "buyers", "buys", "sells", "confirmations", "fb", "fs", "fv")}}, now)

    def on_run(self, info: dict, now: Optional[float] = None):
        """Version stamp written at every worker start: later analysis splits the data by code/settings version."""
        self._write({"t": "run", "ts": now or __import__("time").time(), **info}, now)

    def on_snap(self, mint: str, age_s: float, buys: int, sells: int, vol: float, mc_usd: float, now: Optional[float] = None):
        """Free DexScreener 5-min snapshot of a young coin (14 Oct): lets us learn which early profiles become winners."""
        self._write({"t": "snap", "ts": round(now or time.time(), 1), "mint": mint, "age": round(age_s, 1),
                     "b": buys, "s": sells, "v": round(vol), "mc": round(mc_usd)}, now)

    def on_final(self, rec, now: Optional[float] = None):
        """Trade-feed summary of a tracked coin when it leaves the tracker."""
        self._write({"t": "final", "ts": round(now or time.time(), 1), "mint": rec.mint, "buyers": len(rec.buyers),
                     "buys": rec.buys, "sells": rec.sells, "fast": len(rec.fast), "peak_sol": rec.peak_sol,
                     "last_sol": rec.mcap_sol, "creator_sold": rec.creator_sold}, now)

    def due(self, now: Optional[float] = None, limit: int = 150) -> List[str]:
        now = now if now is not None else time.time()
        out = []
        for mint, (ts, i) in self.pending.items():
            if i < len(CHECKPOINTS_MIN) and now - ts >= CHECKPOINTS_MIN[i] * 60:
                out.append(mint)
                if len(out) >= limit:
                    break
        return out

    def sample(self, batch_fn: Callable, now: Optional[float] = None, limit: int = 150) -> int:
        """Prices due coins in batches and records the outcome rows. Returns rows written."""
        now = now if now is not None else time.time()
        mints = self.due(now, limit)
        if not mints:
            return 0
        try:
            prices = batch_fn("solana", mints) or {}
        except Exception:  # noqa: BLE001
            prices = {}
        n = 0
        for mint in mints:
            ts, i = self.pending[mint]
            pair = prices.get(mint)
            self._write({"t": "outcome", "ts": round(now, 1), "mint": mint, "cp_min": CHECKPOINTS_MIN[i],
                         "mcap_usd": float((pair or {}).get("marketCap") or (pair or {}).get("fdv") or 0) or None,
                         "liq_usd": ((pair or {}).get("liquidity") or {}).get("usd"), "listed": bool(pair)}, now)
            n += 1
            if i + 1 >= len(CHECKPOINTS_MIN):
                del self.pending[mint]
            else:
                self.pending[mint][1] = i + 1
        return n

"""Creator reputation built from our own stream (Oct 13 2026).
Evidence (public 2026 study, ~693K launches): launches by a creator who ALREADY graduated a token and
has been quiet for 7+ days graduated 12.0% of the time vs 1.71% base (~7x lift). We cannot fetch creator
history, so we build it: every launch and every graduation (PumpPortal subscribeMigration) updates this
table. Cold start: it fills over days. Persisted to early_log/creators.json."""
import json
import os
import time
from typing import Dict, Optional

FILE = os.path.join("early_log", "creators.json")


class Creators:
    def __init__(self, path: str = FILE):
        self.path = path
        self.t: Dict[str, dict] = {}            # creator -> {n, grads, last, prev_last, last_grad}
        self.mint_creator: Dict[str, list] = {}  # mint -> [creator, ts]  (24h)
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.t = json.load(f)
        except (OSError, ValueError):
            self.t = {}

    def save(self, now: Optional[float] = None):
        now = now if now is not None else time.time()
        keep = {c: s for c, s in self.t.items() if s.get("grads") or now - s.get("last", 0) < 3 * 86400}
        self.t = keep
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.t, f, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError:
            pass

    def on_create(self, creator: str, mint: str, now: Optional[float] = None):
        now = now if now is not None else time.time()
        s = self.t.setdefault(creator, {"n": 0, "grads": 0, "last": 0.0, "prev_last": 0.0, "last_grad": 0.0})
        s["prev_last"], s["last"], s["n"] = s.get("last", 0.0), now, s.get("n", 0) + 1
        self.mint_creator[mint] = [creator, now]
        if len(self.mint_creator) > 80000:
            for m in [m for m, (_, ts) in self.mint_creator.items() if now - ts > 86400]:
                del self.mint_creator[m]

    def on_graduate(self, mint: str, now: Optional[float] = None) -> Optional[str]:
        now = now if now is not None else time.time()
        hit = self.mint_creator.get(mint)
        if not hit:
            return None
        s = self.t.get(hit[0])
        if s is not None:
            s["grads"] = s.get("grads", 0) + 1
            s["last_grad"] = now
        return hit[0]

    def repeat_grad(self, creator: str, now: Optional[float] = None) -> bool:
        """True if this creator graduated before AND was quiet >= EARLY_QUIET_DAYS before this launch."""
        now = now if now is not None else time.time()
        s = self.t.get(creator)
        if not s or s.get("grads", 0) < 1:
            return False
        quiet = float(os.environ.get("EARLY_QUIET_DAYS", "1")) * 86400
        prev = s.get("prev_last") or 0.0
        return (now - prev) >= quiet if prev else True

    def snapshot(self, creator: str) -> dict:
        s = self.t.get(creator) or {}
        return {"creator_launches": s.get("n", 0), "creator_grads": s.get("grads", 0)}

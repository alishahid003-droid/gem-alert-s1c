"""Known bot / sybil wallets (bot_wallets.txt). Never treat them as smart money."""
import os
_C = None
def load():
    global _C
    if _C is None:
        try:
            _C = {l.strip() for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_wallets.txt"), encoding="utf-8") if l.strip() and not l.startswith("#")}
        except Exception:
            _C = set()
    return _C

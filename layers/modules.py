"""Module registry: one place that says which module every alert, paper
trade and signal belongs to, so Telegram, the dashboard and the ledger all
use the same names (Ali, Oct 12 2026: "clear-cut modules ... this is a dev
launch, this is copy trading from the leaderboards, this is Fomo followers,
this is our own analysis, this is Moonshot core one / core two").

Pure data + lookups. No network, no state, no side effects.
"""
from typing import Dict, List, Optional

# id -> definition. Order = display order on the dashboard.
MODULES: List[Dict] = [
    # ---- trading books (own balance) ----
    {"id": "core1", "kind": "book", "emoji": "🚀", "short": "CORE 1", "name": "Moonshot Core 1 — $100 → $125K",
     "blurb": "Fast-compounding moonshot book: catalyst-driven 10x-50x plays, mid-cap momentum with escape velocity. ~60-day target.",
     "layers": ["layer15_core1"], "sources": ["core1"], "signals": []},
    {"id": "core2", "kind": "book", "emoji": "🌙", "short": "CORE 2", "name": "Moonshot Core 2 — $100 → $1M-$1.5M",
     "blurb": "Asymmetric moonshot book: the earliest, smallest-cap plays (100x+ potential), runners ride. ~150-day target.",
     "layers": ["layer15_core2", "layer15_moonshot"], "sources": ["core2", "moonshot"], "signals": []},
    {"id": "compound", "kind": "book", "emoji": "💰", "short": "COMPOUND", "name": "Compound Aggregator — daily profit",
     "blurb": "Daily-profit book: 2x-13x swings and quick momentum plays, small steady gains compounded every day.",
     "layers": ["layer15_compound"], "sources": ["scalper", "momentum_lane", "stage1", "revival", "geckoterminal", "madeonsol", "mobula", "radar_ladder"],
     "signals": ["scalper", "momentum_lane", "score_band_A", "score_band_B", "radar_ladder"]},
    {"id": "leaderboard", "kind": "book", "emoji": "🏆", "short": "LEADERBOARD COPY", "name": "Leaderboard Copy-Trading (pump.fun / StonkFun / tracked wallets)",
     "blurb": "Wallets from the pump.fun / StonkFun leaderboards and our tracked list buying — single buys, convergence, large untracked buys, sells.",
     "layers": ["layer2", "layer2_single", "layer2_untracked_large", "layer2b", "layer2b_single", "layer9", "layer10"],
     "sources": ["leaderboard"], "signals": ["wallet_convergence"]},
    {"id": "fomo_followers", "kind": "book", "emoji": "👥", "short": "FOMO FOLLOWERS", "name": "Fomo — Followed Traders",
     "blurb": "Traders on your Fomo follow list buying a coin, plus their written theses.",
     "layers": ["layer13"], "sources": ["stage2", "fomo_followers"], "signals": []},
    {"id": "fomo_new", "kind": "book", "emoji": "🆕", "short": "FOMO NEW WALLETS", "name": "Fomo — New High-Balance Wallets",
     "blurb": "New wallets with a big balance and positive 7d/30d PnL, not yet on your list: the coins they just bought. Starts by itself when Fomo credits are live.",
     "layers": ["layer13_new"], "sources": ["fomo_new"], "signals": []},
    {"id": "dev_list", "kind": "book", "emoji": "🧑‍💻", "short": "DEV LAUNCH", "name": "Dev Copy-Trading — Our Quality-Dev List",
     "blurb": "A developer from our elite/good list just launched a token.",
     "layers": ["layer1"], "sources": ["dev_list"], "signals": ["deployer_trusted"]},
    # ---- finders / views (no balance; they feed the books above) ----
    {"id": "dev_intel", "kind": "view", "emoji": "🔎", "short": "DEV INTEL", "name": "Dev Analysis — Any Dev, Not Just the List",
     "blurb": "Dev holding %, wallet age and track record for every coin we alert on or buy — including devs not on our list.",
     "layers": [], "sources": [], "signals": []},
    {"id": "radar", "kind": "finder", "emoji": "📡", "short": "RADAR", "name": "Catalyst Radar (finder)",
     "blurb": "Finds public catalysts: listing ladder, news-jack, launchpad surges, conviction wallets. Coins it finds are routed straight into Core 1, Core 2 or Compound.",
     "layers": ["radar", "radar_ladder", "radar_news", "radar_launchpad", "radar_conviction", "radar_buyback"],
     "sources": ["radar_news", "radar_launchpad", "radar_conviction", "radar_buyback"], "signals": []},
    {"id": "own", "kind": "finder", "emoji": "🧪", "short": "OWN ANALYSIS", "name": "Own Analysis (finder)",
     "blurb": "Our filters. A coin that passes is routed straight to Core 1 / Core 2 / Compound; only coins that fit nowhere stay here as watch-list.",
     "layers": ["layer0", "layer0b", "layer0c", "layer0c_momentum", "layer8", "layer14", "layer15", "layer16_momentum"],
     "sources": [], "signals": []},
    {"id": "context", "kind": "view", "emoji": "🧭", "short": "CONTEXT", "name": "Context — News, Buzz, Mega-alerts",
     "blurb": "Not picks by themselves: news, social buzz, caller channels, multi-layer convergence and downgrades.",
     "layers": ["layer4_news", "layer7", "layer3", "layer11", "layer12", "post_alert_downgrade"],
     "sources": [], "signals": []},
]

_BY_ID = {m["id"]: m for m in MODULES}
_LAYER = {l: m["id"] for m in MODULES for l in m["layers"]}
_SOURCE = {s: m["id"] for m in MODULES for s in m["sources"]}
_SIGNAL = {s: m["id"] for m in MODULES for s in m["signals"]}
DEV_TAGS = ("Dev holding", "Deployer age", "Deployer track record")


def get(module_id: Optional[str]) -> Optional[Dict]:
    return _BY_ID.get(module_id or "")


def for_layer(layer: Optional[str]) -> str:
    """Module id for an alert-feed layer key; unknown layers -> 'own'."""
    return _LAYER.get(layer or "", "own")


_SIGNAL_FIRST = ("deployer_trusted", "wallet_convergence")


def for_paper(source: Optional[str], signal: Optional[str] = None) -> str:
    """Module id for a paper position. A signal that names a copy-trading
    origin (dev list, wallet convergence) wins over the generic source."""
    if signal in _SIGNAL_FIRST:
        return _SIGNAL[signal]
    return _SOURCE.get(source or "") or _SIGNAL.get(signal or "") or "own"


def books() -> List[Dict]:
    return [m for m in MODULES if m.get("kind") == "book"]


def has_dev_intel(tags: Optional[dict]) -> bool:
    return any(k in (tags or {}) for k in DEV_TAGS)


def label(module_id: Optional[str]) -> str:
    m = get(module_id)
    return f"{m['emoji']} {m['short']}" if m else ""


def label_for_layer(layer: Optional[str]) -> str:
    return label(for_layer(layer))

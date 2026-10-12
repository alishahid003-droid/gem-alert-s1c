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
    {"id": "moonshot", "emoji": "🌙", "short": "MOONSHOT", "name": "Moonshot — Core 1 Marathon",
     "blurb": "Escape-velocity coins for the $100 -> $1M+ marathon. Paper first; real money only when switched on.",
     "layers": ["layer15_moonshot"], "sources": ["moonshot"], "signals": ["moonshot"]},
    {"id": "compound", "emoji": "💰", "short": "COMPOUND", "name": "Compound Engine — Core 2 Sprint",
     "blurb": "The compounding pool: one trade at a time, locks profit at milestones, stops after losses.",
     "layers": [], "sources": ["scalper", "momentum_lane"], "signals": ["scalper", "momentum_lane"]},
    {"id": "radar", "emoji": "📡", "short": "RADAR", "name": "Catalyst Radar",
     "blurb": "Public catalysts: exchange listing ladder, news-jack, launchpad surges, conviction wallets. Paper + alert only.",
     "layers": ["radar", "radar_ladder", "radar_news", "radar_launchpad", "radar_conviction", "radar_buyback"],
     "sources": ["radar_ladder", "radar_news", "radar_launchpad", "radar_conviction", "radar_buyback"],
     "signals": ["radar_ladder", "radar_news", "radar_launchpad", "radar_conviction", "radar_buyback"]},
    {"id": "leaderboard", "emoji": "🏆", "short": "LEADERBOARD COPY", "name": "Leaderboard Copy-Trading (pump.fun / StonkFun / chain wallets)",
     "blurb": "Wallets from the pump.fun / StonkFun leaderboards and our tracked list buying — single buys, convergence, large untracked buys, sells.",
     "layers": ["layer2", "layer2_single", "layer2_untracked_large", "layer2b", "layer2b_single", "layer9", "layer10"],
     "sources": ["stage2"], "signals": ["stage2"]},
    {"id": "fomo_followers", "emoji": "👥", "short": "FOMO FOLLOWERS", "name": "Fomo — Followed Traders",
     "blurb": "Traders on your Fomo follow list buying a coin, plus their written theses. Dashboard-only.",
     "layers": ["layer13"], "sources": [], "signals": []},
    {"id": "fomo_new", "emoji": "🆕", "short": "FOMO NEW WALLETS", "name": "Fomo — New High-Balance Wallets",
     "blurb": "New wallets climbing the Fomo leaderboard with a big balance, not yet on your list. Auto-promoted when 7d and 30d PnL are positive.",
     "layers": ["layer13_new"], "sources": [], "signals": []},
    {"id": "dev_list", "emoji": "🧑‍💻", "short": "DEV LAUNCH", "name": "Dev Copy-Trading — Our Quality-Dev List",
     "blurb": "A developer from our elite/good list just launched a token.",
     "layers": ["layer1"], "sources": [], "signals": []},
    {"id": "dev_intel", "emoji": "🔎", "short": "DEV INTEL", "name": "Dev Analysis — Any Dev, Not Just the List",
     "blurb": "Dev holding %, wallet age and track record for every coin we alert on or buy — including devs not on our list.",
     "layers": [], "sources": [], "signals": []},
    {"id": "own", "emoji": "🧪", "short": "OWN ANALYSIS", "name": "Own Analysis — Our Filters",
     "blurb": "Coins that pass our own safety/quality filters (score bands, momentum, revival, buyer quality).",
     "layers": ["layer0", "layer0b", "layer0c", "layer0c_momentum", "layer8", "layer14", "layer15", "layer16_momentum"],
     "sources": ["stage1", "revival", "geckoterminal", "madeonsol", "mobula"],
     "signals": ["stage1", "revival", "geckoterminal"]},
    {"id": "context", "emoji": "🧭", "short": "CONTEXT", "name": "Context — News, Buzz, Mega-alerts",
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


def for_paper(source: Optional[str], signal: Optional[str] = None) -> str:
    """Module id for a paper position (source first, then signal)."""
    return _SOURCE.get(source or "") or _SIGNAL.get(signal or "") or "own"


def has_dev_intel(tags: Optional[dict]) -> bool:
    return any(k in (tags or {}) for k in DEV_TAGS)


def label(module_id: Optional[str]) -> str:
    m = get(module_id)
    return f"{m['emoji']} {m['short']}" if m else ""


def label_for_layer(layer: Optional[str]) -> str:
    return label(for_layer(layer))

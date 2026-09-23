"""
Tracked-trader roster (from Notion S1c page, section 6 -- 16 Fomo screenshots +
26 friends + global leaderboard). Names are matched against MadeOnSol's
`kol_name` field on KOL feed/trade records.

Per spec: Tier 1/2 are used for Layer 2's BUY-side convergence signal only.
ALL tiers + the watchlist are used for Layer 9's SELL-side mirror -- any
tracked entity selling matters, even ones not used for buy-side convergence.
"""

TIER_1 = {"Unipcs", "point farm capital", "AJC", "Avast", "TheSolstice"}

TIER_2 = {
    "Aurelius", "zeri_terminal", "Werey", "Ethermonk", "GiantLoyalFox",
    "Eagle_0X", "Logan Lim", "frank",
}

TIER_3 = {
    "DumbCrayonEater", "Salem", "Burgz", "Nate", "Wood", "eric.eth", "orangie",
}

TIER_4 = {"Avocado", "Qwerty", "WuKong", "change", "DopamineFeenFr1"}

WATCHLIST_CANDIDATES = {
    "cosby", "Frogman", "dreamloader", "Old Man Pervert", "RugDalio", "RZD",
    "Dazzle Novak", "MrFernando", "lordarbiter", "Zakum", "Albus",
}

# Layer 2 (buy-side convergence): Tier 1/2 ONLY, per spec.
CONVERGENCE_ROSTER = TIER_1 | TIER_2

# Layer 9 (sell-side mirror): every tier + watchlist candidates.
SELL_WATCH_ROSTER = TIER_1 | TIER_2 | TIER_3 | TIER_4 | WATCHLIST_CANDIDATES


def tier_of(name: str) -> str:
    if name in TIER_1:
        return "Tier 1"
    if name in TIER_2:
        return "Tier 2"
    if name in TIER_3:
        return "Tier 3"
    if name in TIER_4:
        return "Tier 4"
    if name in WATCHLIST_CANDIDATES:
        return "watchlist"
    return "untracked"

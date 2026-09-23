"""
Layer 9 -- Tracked-entity sell mirror.

For any coin currently in the active alert feed OR in Ali's holdings, watch
the SAME roster + deployer + KOL wallets already pulled for Layer 2 -- but
for SELL-side trades this time, not just buys. Fires immediately, naming
who and how much: [SELL: <name/role> sold <X>% of position].

Uses roster.SELL_WATCH_ROSTER (all tiers + watchlist), unlike Layer 2 which
is Tier 1/2 only for buy-side convergence -- per spec, any tracked entity
selling matters even if it isn't used for the buy-side signal.

Per the spec's own constraint ("fail closed... never guess"): if the % of
position sold can't actually be computed (no prior balance on file for that
wallet+token), the alert still fires but says so explicitly rather than
inventing a percentage.
"""
from config import CONFIG
from utils.http import get_json
from layers.roster import SELL_WATCH_ROSTER, tier_of
from layers.layer2_convergence import _token_id, _parse_time  # reuse, same feed shape


def fetch_sell_feed(chain: str = "solana", limit: int = 100) -> dict:
    if not CONFIG.madeonsol_api_key:
        return {"ok": False, "reason": "MADEONSOL_API_KEY not configured"}
    prefix = "/rhc" if chain == "robinhood_chain" else ""
    headers = {"Authorization": f"Bearer {CONFIG.madeonsol_api_key}"}
    result = get_json(f"{CONFIG.madeonsol_base_url}{prefix}/kol/feed",
                       headers=headers, params={"limit": limit, "action": "sell"})
    return {"ok": result["ok"], "raw": result}


def pct_of_position(wallet: str, token: str, amount_sold, prior_balances: dict):
    """prior_balances: {(wallet, token): balance_before_sell}. Returns a
    float pct or None if it genuinely can't be computed -- never a guess."""
    key = (wallet, token)
    prior = prior_balances.get(key)
    if prior is None or prior <= 0 or amount_sold is None:
        return None
    return min(100.0, 100.0 * amount_sold / prior)


def detect_sell_events(trades: list, relevant_tokens: set,
                        deployer_wallet_by_token: dict = None,
                        prior_balances: dict = None,
                        roster: set = SELL_WATCH_ROSTER,
                        resolve_unknown=None) -> list:
    """trades: raw /kol/feed (action=sell) trade dicts.
    relevant_tokens: set of token addresses currently in the active feed OR
    in Ali's holdings -- sells outside this set are not this layer's job.
    deployer_wallet_by_token: {token_address: deployer_wallet} so a
    deployer's own sell is caught even though they have no kol_name.
    resolve_unknown: optional callable(wallet_address) -> tier_str, wired
    to layers.layer10_insider_cluster.resolve_identity by scheduler.py
    (Ali, Sept 23 2026 -- Layer 10 was built but never called). Before this,
    a sell from a wallet not on the static roster and not the deployer was
    silently invisible to this layer -- exactly the blind spot Layer 10
    exists to close (funding-chain tracing catches a wallet that's really a
    known trader/deployer operating under a fresh address). Left optional
    and defaulting to None so this function stays pure/network-free for
    every existing caller and test; only scheduler.py's live poll passes a
    real resolver."""
    deployer_wallet_by_token = deployer_wallet_by_token or {}
    prior_balances = prior_balances or {}
    token_to_deployer_wallet = deployer_wallet_by_token
    wallet_to_deployer_token = {v: k for k, v in token_to_deployer_wallet.items()}

    events = []
    for t in trades:
        if t.get("action") != "sell":
            continue
        token = _token_id(t)
        if token is None or token not in relevant_tokens:
            continue
        wallet = t.get("wallet_address")
        name = t.get("kol_name")

        is_roster_member = name in roster
        is_deployer = wallet_to_deployer_token.get(wallet) == token
        cluster_tier = None
        if not (is_roster_member or is_deployer) and resolve_unknown and wallet:
            cluster_tier = resolve_unknown(wallet)
            if cluster_tier in (None, "untracked"):
                cluster_tier = None
        if not (is_roster_member or is_deployer or cluster_tier):
            continue

        if is_roster_member:
            who, role = name, tier_of(name)
        elif is_deployer:
            who, role = "deployer", "deployer"
        else:
            who, role = f"wallet-cluster:{cluster_tier}", cluster_tier
        # Prefer token_amount (a token quantity, the same unit a wallet-portfolio
        # balance is in) over sol_amount (a SOL/USD value at trade time) for the
        # pct-of-position math -- these are NOT interchangeable units. Previously
        # this always read sol_amount first, which silently didn't matter because
        # pct_of_position was always fed an empty prior_balances dict anyway (no
        # real balance data existed yet). Now that Layer 9 records real balances
        # (see scheduler.py), the unit has to be right. sol_amount stays as the
        # fallback only for display when token_amount isn't present.
        amount_for_pct = t.get("token_amount")
        if amount_for_pct is None:
            amount_for_pct = t.get("sol_amount")  # unit mismatch vs a token-qty balance -- best effort only
        amount_display = t.get("sol_amount") or t.get("token_amount")
        pct = pct_of_position(wallet, token, amount_for_pct, prior_balances)

        events.append({
            "token": token,
            "wallet": wallet,
            "who": who,
            "role": role,
            "amount": amount_display,
            "amount_for_pct": amount_for_pct,  # exposed so the scheduler can update the stored balance
            "pct_of_position": pct,  # None means "not computable yet", never faked
            "is_deployer_sell": is_deployer,
            "traded_at": t.get("traded_at"),
        })
    return events


def update_balance_after_sell(wallet: str, token: str, amount_sold, prior_balances: dict):
    """Arithmetic-only balance update after a sell -- NO extra Mobula call
    needed here. We already know the pre-sell balance (whatever was last
    recorded, e.g. from a buy sighting) and the amount just sold, so the
    post-sell balance is prior - amount_sold. Returns the new balance, or
    None if there was no prior balance to update (nothing to write in that
    case -- stays "unknown" until a future buy sighting establishes one,
    same fail-closed behavior as pct_of_position itself)."""
    prior = prior_balances.get((wallet, token))
    if prior is None or amount_sold is None:
        return None
    return max(0.0, prior - amount_sold)


def poll_layer9(chain: str, relevant_tokens: set, deployer_wallet_by_token: dict = None,
                 prior_balances: dict = None, trades: list = None, resolve_unknown=None) -> dict:
    """If `trades` is given (already-fetched sell trades, e.g. from
    layers.kol_feed.fetch_kol_feed_both shared across Layers 2+9), uses them
    directly and makes no network call. Otherwise fetches its own -- kept for
    backward compatibility / standalone testing. resolve_unknown: see
    detect_sell_events's docstring -- passed straight through."""
    if trades is None:
        fetched = fetch_sell_feed(chain)
        if not fetched["ok"]:
            return {"ok": False, "reason": fetched.get("reason", "fetch failed"), "events": []}
        body = fetched["raw"].get("json")
        if body is None:
            return {"ok": False, "reason": f"non-JSON response, status {fetched['raw']['status_code']}", "events": []}
        trades = body.get("trades", [])
    events = detect_sell_events(trades, relevant_tokens, deployer_wallet_by_token, prior_balances,
                                 resolve_unknown=resolve_unknown)
    return {"ok": True, "events": events}

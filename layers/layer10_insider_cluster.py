"""
Layer 10 -- Wallet-cluster tracking + insider tagging.

Added Sept 22, 2026 per Ali's direction: developers and traders rotate
wallets, sometimes specifically to dodge the exact kind of tracking Layers
1/2/9 do by name or a static address. This layer makes the system resilient
to that by tracing FUNDING relationships instead of relying only on a fixed
identity.

Two capabilities:

1. FUNDING-CHAIN CLUSTERING (resolve_identity)
   When a wallet has no MadeOnSol tier and doesn't match a tracked trader
   name, check who funded it -- its first-ever inbound transfer. If that
   funder is already a known/tracked wallet, the new wallet inherits that
   identity, even with zero trading history of its own. Every confirmed
   link is persisted (state.py) so the roster grows on its own over time --
   no manual list maintenance, and no static address list to go stale.

2. INSIDER TAGGING (tag_insider_holders)
   For a token's top holders, check whether each holder's first inbound
   transfer came directly from the DEPLOYER wallet (got tokens/funding
   handed to them) rather than from an independent buy. That's the insider
   signature -- how they got in, not how much they hold (top10_holder_pct
   in layer0_scoring already covers "how much"). Layer 9 uses this to
   escalate an insider-tagged wallet's sell above an ordinary holder's sell.

Data source: Mobula's wallet-transactions endpoint (same account/key
Layers 6/9 already use via wallet_balance.py -- no new API, no RPC).
Endpoint and response shape are per Mobula's public docs as of Sep 2026 but,
like every other live-network claim in this codebase, UNCONFIRMED until a
real call succeeds -- this fails closed (returns None / "untracked") on
anything it can't confidently parse, same convention as wallet_balance.py's
_assets_by_wallet.

Chain coverage, stated honestly: this tooling is mature on Solana (Mobula's
transaction history is well-documented there), thinner on BSC, and
unconfirmed on Robinhood Chain -- RHC's indexing/API coverage is newer.
Expect Solana results to be trustworthy first; treat BSC/RHC results as
provisional until a live run confirms the endpoint actually returns useful
data for those chains.

NOT wired into scheduler.py's live poll loop yet. This module is called
directly by executor/triggers.py once Ali decides to test it, and can also
be used to enrich Layer 9 alerts manually -- neither is automatic yet.
"""
import time
from typing import Optional

from config import CONFIG
from utils.http import get_json
import state
from layers.roster import tier_of

# How far back we're willing to look for a wallet's first inbound transfer.
# Mobula's transaction endpoint is paginated; this caps how many pages we'll
# walk before giving up rather than paying for an unbounded history fetch.
MAX_TX_PAGES = 5
TX_PAGE_SIZE = 100


def fetch_wallet_transactions(chain: str, address: str, page: int = 1) -> dict:
    """GET /api/1/wallet/transactions -- per Mobula's public docs. Same
    MOBULA_API_KEY as everything else in this stack, no new account."""
    if not CONFIG.mobula_api_key:
        return {"ok": False, "reason": "MOBULA_API_KEY not configured"}
    headers = {"Authorization": f"Bearer {CONFIG.mobula_api_key}"}
    params = {
        "wallet": address,
        "blockchain": chain,
        "limit": TX_PAGE_SIZE,
        "offset": (page - 1) * TX_PAGE_SIZE,
        "order": "asc",  # oldest first -- we want the FIRST transfer, not the latest
    }
    result = get_json(f"{CONFIG.mobula_base_url}/api/1/wallet/transactions", headers=headers, params=params)
    return {"ok": result["ok"], "raw": result}


def _first_inbound_transfer(chain: str, address: str) -> Optional[str]:
    """Walks transaction pages oldest-first, returns the sender address of
    the first inbound (received) transfer, or None if it can't be found
    within MAX_TX_PAGES or the response shape doesn't match what's expected.
    Fails closed -- never guesses."""
    for page in range(1, MAX_TX_PAGES + 1):
        fetched = fetch_wallet_transactions(chain, address, page)
        if not fetched.get("ok"):
            return None
        body = fetched["raw"].get("json") or {}
        txs = body.get("data") if isinstance(body, dict) else None
        if not txs:
            return None
        for tx in txs:
            to_addr = tx.get("to") or tx.get("to_address")
            from_addr = tx.get("from") or tx.get("from_address")
            if to_addr and from_addr and str(to_addr).lower() == str(address).lower():
                return from_addr
        if len(txs) < TX_PAGE_SIZE:
            return None  # last page, nothing found
    return None


# --- persisted cluster map (state.py) ---
# Key shape: wallet_cluster:{chain}:{address} -> {"linked_to", "tier", "source", "ts"}

def _cluster_key(chain: str, address: str) -> str:
    return f"wallet_cluster:{chain}:{address.lower()}"


def get_cluster_link(chain: str, address: str) -> Optional[dict]:
    return state.get_value(_cluster_key(chain, address))


def _set_cluster_link(chain: str, address: str, linked_to: str, tier: str, source: str):
    state.set_value(_cluster_key(chain, address), {
        "linked_to": linked_to, "tier": tier, "source": source, "ts": time.time(),
    })


def resolve_identity(chain: str, address: str, kol_name: Optional[str] = None) -> dict:
    """Returns {"tier": ..., "source": ...} for a wallet, checking in order:
      1. Already-known Fomo trader name (roster.py, via MadeOnSol's kol_name
         match -- pass kol_name if the caller already has it from a KOL feed
         event).
      2. A previously-confirmed cluster link (this wallet was linked to a
         tracked identity on an earlier run).
      3. Funding-chain trace: if this wallet's first funder is a tracked
         wallet (or one previously linked to a tracked identity), link it
         now and persist so future lookups are instant (no re-trace needed).
      4. "untracked" if none of the above resolve.
    """
    if kol_name:
        tier = tier_of(kol_name)
        if tier != "untracked":
            return {"tier": tier, "source": "kol_name_match"}

    existing = get_cluster_link(chain, address)
    if existing:
        return {"tier": existing["tier"], "source": f"cached:{existing['source']}"}

    funder = _first_inbound_transfer(chain, address)
    if not funder:
        return {"tier": "untracked", "source": "no_funding_history"}

    # Is the funder itself already tracked (directly or via its own cluster link)?
    funder_link = get_cluster_link(chain, funder)
    if funder_link:
        _set_cluster_link(chain, address, funder_link["linked_to"], funder_link["tier"], "funding_chain")
        return {"tier": funder_link["tier"], "source": "funding_chain_inherited"}

    # funder isn't in the cluster map -- this module doesn't independently
    # know funder's kol_name without a caller supplying it, so this path
    # only resolves once the funder itself has been positively identified
    # by the caller (e.g. via a Layer 2 KOL-feed event carrying kol_name)
    # and registered with register_known_wallet below.
    return {"tier": "untracked", "source": "funder_not_tracked"}


def register_known_wallet(chain: str, address: str, tier: str, source: str = "kol_name_match"):
    """Called once a wallet is positively identified (e.g. MadeOnSol's
    kol_name matched it via roster.tier_of), so future funding-chain traces
    from wallets IT funds can resolve. This is what makes the roster grow
    itself -- each newly-confirmed wallet becomes a link other wallets can
    inherit from."""
    _set_cluster_link(chain, address, address, tier, source)


# --- insider tagging ---

def tag_insider_holders(chain: str, deployer_address: str, holder_addresses: list) -> dict:
    """Returns {holder_address: is_insider (bool)}. A holder is insider-
    tagged if their first inbound transfer came directly from the deployer
    wallet -- they got tokens/funding handed to them, not an independent
    buy. Fails closed: a holder whose funding source can't be determined is
    NOT tagged either way (excluded from the result) rather than guessed."""
    out = {}
    for holder in holder_addresses:
        if not holder or holder.lower() == deployer_address.lower():
            continue
        funder = _first_inbound_transfer(chain, holder)
        if funder is None:
            continue  # can't determine -- omit, don't guess
        out[holder] = (funder.lower() == deployer_address.lower())
    return out


# ---------------------------------------------------------------------------
# Free Solana-RPC insider check for Fomo new-trader candidates (Sept 30 2026)
# ---------------------------------------------------------------------------
# Ali's ask: before trusting a new trader, check whether they're an insider.
# The Mobula-based _first_inbound_transfer above can't run today (Mobula's
# free plan returns 403 -- see layer0_scoring's GeckoTerminal fallback
# notes), so this does the same funding-chain test over the free Solana RPC
# pool already used by layer0_scoring (executor.rpc_pool): the wallet's
# OLDEST transaction, and whoever's SOL balance dropped in it, is its first
# funder. A trader whose first funder is the deployer of a coin they bought
# -- or who IS that deployer -- is trading their own/insider supply.
# Same single-page limit as fetch_solana_wallet_first_seen_ts: a wallet with
# 1000+ transactions can't have its true first funder found in one page, so
# that returns None ("unknown"), never a guess.

def solana_first_funder(wallet: str) -> Optional[str]:
    from executor.rpc_pool import rpc_call
    sigs = rpc_call("solana", "getSignaturesForAddress", [wallet, {"limit": 1000}])
    if not sigs.get("ok"):
        return None
    rows = sigs.get("result") or []
    if not rows or len(rows) >= 1000:
        return None  # empty, or too active to see the first tx in one page
    oldest = rows[-1].get("signature") if isinstance(rows[-1], dict) else None
    if not oldest:
        return None
    tx = rpc_call("solana", "getTransaction",
                  [oldest, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    if not tx.get("ok") or not tx.get("result"):
        return None
    try:
        res = tx["result"]
        keys = [k.get("pubkey") if isinstance(k, dict) else k for k in res["transaction"]["message"]["accountKeys"]]
        pre, post = res["meta"]["preBalances"], res["meta"]["postBalances"]
    except (KeyError, TypeError):
        return None
    best, best_drop = None, 0
    for addr, a, b in zip(keys, pre, post):
        if addr == wallet:
            continue
        drop = (a or 0) - (b or 0)
        if drop > best_drop:
            best, best_drop = addr, drop
    return best


def check_trader_insider(wallets: list, bought_mints: list, max_mints: int = 3) -> dict:
    """Returns {"insider": True/False/None, "detail": str}. True = a
    wallet deployed, or was first funded by the deployer of, a coin the
    trader bought. None = couldn't determine (no wallet, RPC failure)."""
    from layers.layer0_scoring import fetch_solana_token_deployer
    if not wallets:
        return {"insider": None, "detail": "no Solana wallet on the trader's profile"}
    if not bought_mints:
        return {"insider": None, "detail": "no recent Solana buys to check against"}
    deployers = {}
    for mint in list(dict.fromkeys(bought_mints))[:max_mints]:
        dep = fetch_solana_token_deployer(mint)
        if dep:
            deployers[mint] = dep
    if not deployers:
        return {"insider": None, "detail": "could not resolve any token deployer"}
    determined = False
    for w in wallets:
        for mint, dep in deployers.items():
            if w == dep:
                return {"insider": True, "detail": f"wallet {w[:6]}.. DEPLOYED {mint[:6]}.. which they bought"}
        funder = solana_first_funder(w)
        if funder is None:
            continue
        determined = True
        for mint, dep in deployers.items():
            if funder == dep:
                return {"insider": True,
                        "detail": f"wallet {w[:6]}.. was first funded by {mint[:6]}..'s deployer"}
    if determined:
        return {"insider": False, "detail": f"funding checked against {len(deployers)} deployer(s): independent"}
    return {"insider": None, "detail": "wallet funding source undeterminable (RPC/too active)"}

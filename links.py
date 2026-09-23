"""
Builds direct, clickable links to a coin's page so an alert can be acted on
immediately instead of requiring a manual search.

Verified URL formats (checked live, Sept 23 2026, not assumed):
  - pump.fun:   https://pump.fun/coin/<mint>            (real example seen:
                pump.fun/coin/PuMpCJzJVKPzmTrVksuufJAbbyQAb4uGKuyyFzynP7T)
  - StonkFun:   https://www.stonkfun.xyz/token/<mint>    (real examples seen:
                stonkfun.xyz/token/52oLi6CMWZN3odfwnfBUrjvmZFcre735frdpoSo57QWw)
  - DexScreener: https://dexscreener.com/<chain_slug>/<address>  -- universal,
                works for every chain this system covers and accepts either a
                pair address or a token/mint address (redirects to the top
                pair for that token). Chain slugs reused from
                layers/layer11_social_buzz.py's CHAIN_ID_MAP, confirmed
                against DexScreener's own API this session (robinhood chain's
                real slug is "robinhood", not a guess).

HONEST GAP -- do not extend without verifying first: Pons, flap.sh, and
four.meme (the Robinhood Chain / BSC launchpads) do NOT have a confirmed
coin-page URL format as of this build. Live browser + web research on
Sept 23 2026 could not confirm a working pattern for any of the three in
the time available. Only DexScreener is offered for RHC/BSC coins today --
it is a real, working, verified link, just not the native launchpad page.
Add Pons/flap.sh/four.meme links only once someone has actually loaded a
real coin page on each site and captured its URL.
"""

DEXSCREENER_CHAIN_SLUG = {
    "solana": "solana",
    "bsc": "bsc",
    "robinhood_chain": "robinhood",
}


def build_links(chain: str, token_address: str, is_pregrad: bool = False) -> dict:
    """Returns {label: url} for every link this system can confidently offer
    for a given chain + token address. Never guesses a URL it hasn't
    verified -- an unsupported chain/platform is simply left out rather than
    filled with a made-up link."""
    links = {}
    if not token_address or token_address == "n/a":
        return links

    slug = DEXSCREENER_CHAIN_SLUG.get(chain)
    if slug:
        links["DexScreener"] = f"https://dexscreener.com/{slug}/{token_address}"

    if chain == "solana":
        links["pump.fun"] = f"https://pump.fun/coin/{token_address}"
        links["StonkFun"] = f"https://www.stonkfun.xyz/token/{token_address}"

    return links


def render_links_line(chain: str, token_address: str, is_pregrad: bool = False) -> str:
    """Markdown line for Alert.render() -- empty string if nothing to show."""
    links = build_links(chain, token_address, is_pregrad)
    if not links:
        return ""
    parts = [f"[{label}]({url})" for label, url in links.items()]
    return "🔗 " + " | ".join(parts)

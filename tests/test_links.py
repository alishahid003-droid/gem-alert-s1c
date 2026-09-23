from links import build_links, render_links_line


def test_solana_pregrad_gets_pumpfun_stonkfun_and_dexscreener():
    links = build_links("solana", "MintAddr1111111111111111111111111111111")
    assert links["pump.fun"] == "https://pump.fun/coin/MintAddr1111111111111111111111111111111"
    assert links["StonkFun"] == "https://www.stonkfun.xyz/token/MintAddr1111111111111111111111111111111"
    assert links["DexScreener"] == "https://dexscreener.com/solana/MintAddr1111111111111111111111111111111"


def test_bsc_gets_dexscreener_only():
    links = build_links("bsc", "0xABC123")
    assert links == {"DexScreener": "https://dexscreener.com/bsc/0xABC123"}
    assert "pump.fun" not in links


def test_robinhood_chain_gets_dexscreener_with_real_slug():
    links = build_links("robinhood_chain", "0xDEF456")
    assert links == {"DexScreener": "https://dexscreener.com/robinhood/0xDEF456"}


def test_unknown_chain_returns_nothing_not_a_guess():
    assert build_links("ethereum", "0x123") == {}


def test_no_token_address_returns_nothing():
    assert build_links("solana", "n/a") == {}
    assert build_links("solana", "") == {}


def test_render_links_line_format():
    line = render_links_line("bsc", "0xABC123")
    assert line == "🔗 [DexScreener](https://dexscreener.com/bsc/0xABC123)"


def test_render_links_line_empty_when_no_links():
    assert render_links_line("ethereum", "0x123") == ""

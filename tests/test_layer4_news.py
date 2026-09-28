import json
import os

import layers.layer4_news as layer4_news
from layers.layer4_news import parse_cryptopanic_posts, parse_binance_new_listings, diff_coinbase_new_symbols, parse_coindesk_rss, fetch_cryptopanic_posts

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_cryptopanic_parse():
    posts = parse_cryptopanic_posts(_load("cryptopanic_posts_sample.json"))
    assert len(posts) == 1
    assert posts[0]["currencies"] == ["SOL"]


def test_cryptopanic_parse_includes_post_id():
    # id is needed for state.cryptopanic_seen_posts dedup -- without it, every
    # post would re-alert every cycle the same way Binance's feed did before it
    # got a seen-set (Ali, Sept 24 2026).
    posts = parse_cryptopanic_posts(_load("cryptopanic_posts_sample.json"))
    assert posts[0]["id"] == 1


def test_binance_listing_parse():
    listings = parse_binance_new_listings(_load("binance_listings_sample.json"))
    assert len(listings) == 1
    assert "SampleGem" in listings[0]["title"]


def test_coinbase_diff_detects_new_symbol():
    previous = {"BTC-USD", "ETH-USD"}
    current = [{"id": "BTC-USD"}, {"id": "ETH-USD"}, {"id": "SGEM-USD"}]
    new = diff_coinbase_new_symbols(previous, current)
    assert new == ["SGEM-USD"]


def test_coinbase_diff_empty_when_nothing_new():
    previous = {"BTC-USD"}
    current = [{"id": "BTC-USD"}]
    assert diff_coinbase_new_symbols(previous, current) == []


# CoinDesk RSS -- Layer 4's CryptoPanic replacement (Sept 28 2026, see
# fetch_cryptopanic_posts's docstring for why CryptoPanic died).

_SAMPLE_COINDESK_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>CoinDesk</title>
<item>
<title>Bitcoin Surges Past $70K as ETF Inflows Accelerate</title>
<link>https://www.coindesk.com/markets/2026/09/28/bitcoin-surges/</link>
<guid>https://www.coindesk.com/markets/2026/09/28/bitcoin-surges/</guid>
<pubDate>Sun, 28 Sep 2026 12:00:00 GMT</pubDate>
<description>Bitcoin (BTC) rallied sharply Sunday.</description>
</item>
<item>
<title>Solana Ecosystem Sees Record Memecoin Volume</title>
<link>https://www.coindesk.com/markets/2026/09/28/solana-memecoin/</link>
<guid>https://www.coindesk.com/markets/2026/09/28/solana-memecoin/</guid>
<pubDate>Sun, 28 Sep 2026 11:00:00 GMT</pubDate>
<description>Solana-based tokens saw huge activity this week.</description>
</item>
<item>
<title>US Congress Debates New Crypto Regulation Bill</title>
<link>https://www.coindesk.com/policy/2026/09/28/congress-bill/</link>
<guid>https://www.coindesk.com/policy/2026/09/28/congress-bill/</guid>
<pubDate>Sun, 28 Sep 2026 10:00:00 GMT</pubDate>
<description>Lawmakers proposed a new framework for digital assets.</description>
</item>
<item>
<title>Dogecoin and Shiba Inu Rally as Meme Season Returns</title>
<link>https://www.coindesk.com/markets/2026/09/28/meme-rally/</link>
<guid>https://www.coindesk.com/markets/2026/09/28/meme-rally/</guid>
<pubDate>Sun, 28 Sep 2026 09:00:00 GMT</pubDate>
<description>DOGE and SHIB both posted double-digit gains.</description>
</item>
</channel></rss>"""


def test_coindesk_parse_extracts_correct_tickers():
    posts = parse_coindesk_rss(_SAMPLE_COINDESK_RSS)
    assert len(posts) == 4
    by_title = {p["title"]: p["currencies"] for p in posts}
    assert by_title["Bitcoin Surges Past $70K as ETF Inflows Accelerate"] == ["BTC"]
    assert by_title["Solana Ecosystem Sees Record Memecoin Volume"] == ["SOL"]
    assert by_title["Dogecoin and Shiba Inu Rally as Meme Season Returns"] == ["DOGE", "SHIB"]


def test_coindesk_parse_does_not_false_positive_on_substrings():
    # Real bug caught live Sept 28 2026 before this ever ran against a real
    # feed: naive substring matching tagged "ETF Inflows" as FLOW (matched
    # inside "in-FLOW-s") and "Lawmakers" as MKR (matched inside
    # "law-MAKER-s"). Word-boundary matching must not repeat either.
    posts = parse_coindesk_rss(_SAMPLE_COINDESK_RSS)
    by_title = {p["title"]: p["currencies"] for p in posts}
    assert "FLOW" not in by_title["Bitcoin Surges Past $70K as ETF Inflows Accelerate"]
    assert by_title["US Congress Debates New Crypto Regulation Bill"] == []


def test_coindesk_parse_includes_post_id_for_dedup():
    posts = parse_coindesk_rss(_SAMPLE_COINDESK_RSS)
    assert posts[0]["id"] == "https://www.coindesk.com/markets/2026/09/28/bitcoin-surges/"


def test_coindesk_parse_empty_or_malformed_xml_returns_empty_list():
    assert parse_coindesk_rss("") == []
    assert parse_coindesk_rss("not valid xml <<<") == []


def test_cryptopanic_now_fails_fast_with_explanatory_reason(monkeypatch):
    # CryptoPanic's free Developer plan was discontinued (Sept 28 2026) --
    # fetch_cryptopanic_posts must fail closed with a clear reason instead
    # of attempting a call to a dead endpoint every cycle. Token is
    # deliberately set here so the "token not configured" branch isn't
    # what's being tested.
    monkeypatch.setattr(layer4_news.CONFIG, "cryptopanic_auth_token", "fake_token_for_test")
    result = fetch_cryptopanic_posts()
    assert result["ok"] is False
    assert "discontinued" in result["reason"].lower()


def test_cryptopanic_still_reports_missing_token_separately(monkeypatch):
    monkeypatch.setattr(layer4_news.CONFIG, "cryptopanic_auth_token", None)
    result = fetch_cryptopanic_posts()
    assert result["ok"] is False
    assert "not configured" in result["reason"].lower()

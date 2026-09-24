import json
import os

from layers.layer4_news import parse_cryptopanic_posts, parse_binance_new_listings, diff_coinbase_new_symbols

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

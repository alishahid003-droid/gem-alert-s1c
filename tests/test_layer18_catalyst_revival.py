import json
import os

import pytest

import state
from layers import layer18_catalyst_revival as c18

NOW = 1_800_000_000.0
H = 3600.0


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    yield


def _pair(mint, sym, name, liq, age_h, vol_h1=0, vol_h24=0, h1=0, h24=0, price=0.001, mcap=50_000):
    return {"chainId": "solana", "baseToken": {"address": mint, "symbol": sym, "name": name},
            "liquidity": {"usd": liq}, "marketCap": mcap, "priceUsd": str(price),
            "pairCreatedAt": (NOW - age_h * H) * 1000, "volume": {"h1": vol_h1, "h24": vol_h24},
            "priceChange": {"h1": h1, "h24": h24}, "txns": {"h1": {"buys": 3, "sells": 1}}}


def test_trusted_single_mention_is_hot():
    feeds = {"https://t.me/s/elonfans": ["Elon posts Roadster in space photo"], "https://hnrss.org/frontpage": ["Unrelated thing"]}
    hot = c18.build_hot(feeds, trusted=["https://t.me/s/elonfans"])
    assert hot["roadster"]["trusted"] and hot["roadster"]["fresh"]
    hot2 = c18.build_hot(feeds, trusted=[], prev=["roadster"])
    assert "roadster" not in hot2            # one untrusted mention is not enough


def test_two_untrusted_feeds_make_hot_and_fresh_flag():
    feeds = {"a": ["Roadster seen again"], "b": ["Why the Roadster matters"]}
    hot = c18.build_hot(feeds, prev=["roadster"])
    assert "roadster" in hot and hot["roadster"]["fresh"] is False


def test_parse_tme_and_url_normalise():
    html = '<div class="tgme_widget_message_text js-message_text" dir="auto">Big <b>Roadster</b> pic<br/>wow</div>'
    assert c18.parse_tme_messages(html) == ["Big Roadster pic. wow"]
    assert c18.normalise_feed_url("@foo_bar") == "https://t.me/s/foo_bar"
    assert c18.normalise_feed_url("https://t.me/foo_bar") == "https://t.me/s/foo_bar"
    assert c18.normalise_feed_url("https://x.com/rss") == "https://x.com/rss"


def test_name_matching_rules():
    assert c18.name_matches("roadster", "FTR", "Flying Tesla Roadster") is True
    assert c18.name_matches("tesla roadster", "FTR", "Flying Tesla Roadster") is True
    assert c18.name_matches("car", "CAR", "Car") is False          # too short / generic
    assert c18.name_matches("roadster", "DOGE", "Doge Coin") is False


def test_classify_and_surge():
    dead = c18.pair_to_coin(_pair("m", "S", "Sn", 20_000, 30, vol_h1=10, vol_h24=240), NOW)
    assert c18.classify(dead) == "HEADS-UP"
    waking = c18.pair_to_coin(_pair("m", "S", "Sn", 20_000, 30, vol_h1=6000, vol_h24=6500, h1=60), NOW)
    assert c18.surge_ratio(waking) > 4 and c18.classify(waking) == "CONFIRMED"
    late = c18.pair_to_coin(_pair("m", "S", "Sn", 20_000, 30, vol_h1=60000, vol_h24=61000, h1=900), NOW)
    assert c18.classify(late) == "LATE"


def test_young_and_illiquid_coins_skipped_and_leader_is_deepest():
    hot = {"roadster": {"score": 8, "feeds": 1, "trend": False, "trusted": True, "fresh": True}}
    coins = [c18.pair_to_coin(_pair("A", "FTR", "Flying Tesla Roadster", 40_000, 30), NOW),
             c18.pair_to_coin(_pair("B", "ROAD", "Roadster", 12_000, 40), NOW),
             c18.pair_to_coin(_pair("C", "NEW", "Roadster New", 90_000, 2), NOW),       # <12h: layer16's job
             c18.pair_to_coin(_pair("D", "TINY", "Roadster Tiny", 900, 50), NOW)]       # illiquid
    ms = c18.pick_matches(hot, coins)
    by = {m["coin"]["mint"]: m for m in ms}
    assert set(by) == {"A", "B"} and by["A"]["leader"] and not by["B"]["leader"] and by["A"]["cluster"] == 2


def test_registry_load_and_hits(tmp_path):
    p = tmp_path / "2026-10-17.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [
        {"t": "create", "mint": "A", "sym": "FTR", "name": "Flying Tesla Roadster"},
        {"t": "create", "mint": "B", "sym": "DOG", "name": "Dog"}, {"t": "snap", "mint": "A"}]) + "\nnot json\n")
    reg = c18.load_registry(directory=str(tmp_path))
    assert set(reg) == {"A", "B"}
    hot = {"roadster": {"score": 8}}
    assert c18.registry_hits(hot, reg) == ["A"]


FEED = '<rss><channel><item><title>Elon Musk shares Roadster photo, fans go wild</title></item></channel></rss>'
FEED2 = '<rss><channel><item><title>Tesla Roadster back in news</title></item></channel></rss>'


def _run(tmp_path, pair, sent, monkeypatch, text=FEED, registry=None):
    monkeypatch.setenv("CATALYST_TRUSTED_FEEDS", "https://t.me/s/x")
    def ftext(u):
        return text if ("reddit" in u or "hnrss" in u) else (FEED2 if "news.google" in u else None)
    return c18.run_cycle(send=lambda t: sent.append(t) or {"ok": True},
                         fetch_json=lambda u, p=None: {"pairs": [pair]}, fetch_text=ftext,
                         dex_batch=lambda a: {m: pair for m in a if m == pair["baseToken"]["address"]},
                         registry=registry or {}, now=NOW, log_dir=str(tmp_path / "log"))


def test_end_to_end_ftr_case_alert_then_outcome(tmp_path, monkeypatch):
    pair = _pair("FTRmint", "FTR", "Flying Tesla Roadster", 30_000, 30, vol_h1=20, vol_h24=480, price=0.001)
    sent = []
    out = _run(tmp_path, pair, sent, monkeypatch)
    assert out["alerts"] == 1 and "HEADS-UP" in sent[0] and "FTRmint" in sent[0]
    rows = [json.loads(l) for l in open(next((tmp_path / "log").glob("*.jsonl")))]
    assert rows[0]["t"] == "alert" and rows[0]["verdict"] == "HEADS-UP"
    # same coin again right away: cooldown, no duplicate alert
    assert _run(tmp_path, pair, sent, monkeypatch)["alerts"] == 0
    # coin now pumped; 70 minutes later outcomes (15m,60m) are logged with the multiple
    pump = _pair("FTRmint", "FTR", "Flying Tesla Roadster", 60_000, 30, vol_h1=9000, vol_h24=9500, h1=200, price=0.005)
    n = c18.update_outcomes(lambda ms: {"FTRmint": 0.005}, NOW + 4200, str(tmp_path / "log"))
    assert n == 2
    outs = [json.loads(l) for f in (tmp_path / "log").glob("*.jsonl") for l in open(f) if '"outcome"' in l]
    assert {o["horizon_min"] for o in outs} == {15, 60} and all(o["mult"] == 5.0 for o in outs)


def test_heads_up_upgrades_to_confirmed(tmp_path, monkeypatch):
    sent = []
    _run(tmp_path, _pair("M", "FTR", "Flying Tesla Roadster", 30_000, 30, vol_h1=20, vol_h24=480), sent, monkeypatch)
    out = _run(tmp_path, _pair("M", "FTR", "Flying Tesla Roadster", 30_000, 30, vol_h1=7000, vol_h24=7400, h1=80), sent, monkeypatch)
    assert out["alerts"] == 1 and "CONFIRMED" in sent[-1]


def test_late_is_logged_not_alerted_and_failures_dont_raise(tmp_path, monkeypatch):
    sent = []
    out = _run(tmp_path, _pair("M", "FTR", "Flying Tesla Roadster", 30_000, 30, vol_h1=60000, vol_h24=61000, h1=900), sent, monkeypatch)
    assert out["alerts"] == 0 and out["logged"] == 1
    bad = c18.run_cycle(send=lambda t: {"ok": True}, fetch_json=lambda *a, **k: None, fetch_text=lambda u: None,
                        dex_batch=lambda a: {}, registry={}, now=NOW, log_dir=str(tmp_path / "x"))
    assert bad["alerts"] == 0 and "no feed returned titles" in bad["errors"]

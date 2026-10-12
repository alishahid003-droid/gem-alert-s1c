import time
import dashboard
import state
from layers import modules as mods
from telegram_alert import Alert
from executor import paper_ledger


def test_registry_ids_unique_and_complete():
    ids = [m["id"] for m in mods.MODULES]
    assert len(ids) == len(set(ids))
    for need in ("moonshot", "compound", "leaderboard", "fomo_followers", "fomo_new", "dev_list", "dev_intel", "own", "radar"):
        assert need in ids


def test_layer_and_paper_mapping():
    assert mods.for_layer("layer1") == "dev_list"
    assert mods.for_layer("layer2b") == "leaderboard"
    assert mods.for_layer("layer0b") == "own"
    assert mods.for_layer("radar_news") == "radar"
    assert mods.for_layer("something_new") == "own"       # unknown never crashes
    assert mods.for_paper("moonshot", "moonshot") == "moonshot"
    assert mods.for_paper("scalper") == "compound"
    assert mods.for_paper("stage2") == "leaderboard"
    assert mods.has_dev_intel({"Dev holding": "low (3%)"}) and not mods.has_dev_intel({"Chain": "solana"})


def test_telegram_prefix():
    a = Alert("ABC", "mint1", "solana", "headline")
    assert a.render().startswith("🚨")                      # unchanged when no module
    a.module_label = mods.label_for_layer("layer1")
    assert a.render().startswith("[🧑‍💻 DEV LAUNCH] 🚨 headline")


def test_paper_positions_carry_module(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    pos = paper_ledger.open_paper("solana", "TokX", "moonshot", "moonshot", 30, 50000)
    assert pos and pos["module"] == "moonshot"
    sb = paper_ledger.module_scoreboard()
    assert sb["moonshot"]["open"] == 1 and sb["radar"]["open"] == 0


def test_module_cards_shape_and_routing():
    now = time.time()
    feed = [
        {"module": "dev_list", "token_symbol": "AAA", "chain": "solana", "ago": "1m ago", "headline": "dev launched",
         "band": None, "links": {}, "ts": now, "tags": {}},
        {"module": "own", "token_symbol": "BBB", "chain": "solana", "ago": "2m ago", "headline": "score",
         "band": "A", "links": {}, "ts": now - 10, "tags": {"Dev holding": "low (2%)", "Deployer age": "old"}},
    ]
    fs = [{"token_symbol": "CCC", "trader": "whale", "tier": "elite", "detail": "bought", "ts": now, "ago": "now", "chain": "solana"}]
    fc = [{"display_name": "newguy", "balance_usd": 12000, "pnl_7d": 5, "pnl_30d": 9, "ts": now, "ago": "now"}]
    cards = {c["id"]: c for c in dashboard._module_cards(feed, fs, fc, [])}
    assert cards["dev_list"]["latest"][0]["symbol"] == "AAA"
    assert cards["own"]["latest"][0]["symbol"] == "BBB"
    assert cards["dev_intel"]["latest"][0]["symbol"] == "BBB"        # dev info surfaced separately
    assert cards["fomo_followers"]["latest"][0]["symbol"] == "CCC"
    assert cards["fomo_new"]["latest"][0]["symbol"] == "newguy"
    assert cards["radar"]["alerts_24h"] == 0 and cards["radar"]["last_ago"] is None


def test_safe_cards_never_raises(monkeypatch):
    monkeypatch.setattr(dashboard, "_module_cards", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert dashboard._safe_cards([], [], [], {}) == []


def test_page_has_modules_tab():
    html = dashboard.PAGE if hasattr(dashboard, "PAGE") else open(dashboard.__file__, encoding="utf-8").read()
    assert 'data-tab="modules"' in html and 'id="module_cards"' in html

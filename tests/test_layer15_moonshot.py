"""Layer 15 moonshot detector, moonshot exit profile, and entry handler."""
import time
import pytest

import state
from layers import layer15_moonshot as l15
import executor.exit_rules as er
import executor.entrypoint as ep
import executor.paper_ledger as pl

NOW = 1_790_700_000.0


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.delenv("MOONSHOT_ENABLED", raising=False)
    yield


def pair(mcap=800_000, liq=90_000, age_h=10, h1=40, h6=150, m5=5, buys=900, sells=500,
         vol_h1=120_000, vol_h24=900_000):
    return {"marketCap": mcap, "liquidity": {"usd": liq}, "pairCreatedAt": (NOW - age_h * 3600) * 1000,
            "priceChange": {"m5": m5, "h1": h1, "h6": h6, "h24": h6 * 2},
            "txns": {"h1": {"buys": buys, "sells": sells}, "h24": {"buys": 9000, "sells": 6000}},
            "volume": {"h1": vol_h1, "h24": vol_h24}, "priceUsd": "0.0008"}


def test_escape_velocity_qualifies():
    ok, score, fails = l15.evaluate(l15.metrics(pair()), now=NOW)
    assert ok and not fails and score >= 50


@pytest.mark.parametrize("kw,needle", [
    ({"mcap": 40_000}, "mcap"),                  # still in the rug zone
    ({"mcap": 20_000_000}, "mcap"),              # already big
    ({"age_h": 0.3}, "age"),
    ({"h6": 30}, "6h"),                          # one-hour pump, not a sustained run
    ({"m5": 80}, "5m"),                          # vertical candle
    ({"m5": -30}, "5m"),                         # dumping
    ({"buys": 400, "sells": 500}, "buys"),
    ({"buys": 60, "sells": 40}, "trades"),
    ({"liq": 20_000}, "liquidity"),
    ({"vol_h24": 100_000}, "24h volume"),
])
def test_gates(kw, needle):
    ok, _, fails = l15.evaluate(l15.metrics(pair(**kw)), now=NOW)
    assert not ok and any(needle in f for f in fails)


def ev(mult, peak=None, remaining=1.0, locked=False, profile="moonshot"):
    return er.evaluate_exit(100_000, 100_000 * mult, 100_000 * (peak or mult), NOW, NOW + 600,
                            remaining, locked, 0.03, profile=profile)


def test_moonshot_profile_locks_at_2x_and_half_rides():
    assert ev(1.6).action == "hold"                               # normal profile would lock at 1.5x
    d = ev(2.1)
    assert d.exit_type == "breakeven_lock" and abs(d.pct_of_original - 1.03 / 2.1) < 1e-9
    rem = 1 - d.pct_of_original                                   # ~51% left
    d = ev(3.0, peak=8.0, remaining=rem, locked=True)             # trail hits: sells only down to the 50% runner
    assert d.action == "sell_partial" and abs(d.pct_of_original - (rem - 0.5)) < 1e-9
    assert ev(3.0, peak=8.0, remaining=0.5, locked=True).action == "hold"   # runner survives -62%


def test_handler_paper_only_by_default():
    res = ep.handle_moonshot_candidate("solana", "MOON", "B", 800_000, 90_000, 70,
                                       {"change_m5": 5, "change_h1": 40, "signals": 1})
    assert res["fired"] is False and "MOONSHOT_ENABLED" in res["reason"]
    book = pl._open()
    assert "moonshot:solana:MOON" in book and book["moonshot:solana:MOON"]["exit_profile"] == "moonshot"


def test_handler_base_paper_tracked_but_never_bought(monkeypatch):
    monkeypatch.setenv("MOONSHOT_ENABLED", "true")
    res = ep.handle_moonshot_candidate("base", "0xMOON", "B", 800_000, 90_000, 70, {})
    assert res["fired"] is False and "no buy path" in res["reason"]
    assert "moonshot:base:0xMOON" in pl._open()


def test_handler_real_buy_when_enabled(monkeypatch):
    monkeypatch.setenv("MOONSHOT_ENABLED", "true")
    calls = []
    monkeypatch.setattr(ep, "_attempt_buy_and_record_fill",
                        lambda c, t, usd, stage=None: calls.append((c, t, usd, stage)) or {"ok": True})
    monkeypatch.setattr(ep.triggers, "_concurrency_block", lambda: None)
    res = ep.handle_moonshot_candidate("solana", "MOON2", "B", 800_000, 400_000, 70, {"signals": 1})
    assert res["fired"] and calls == [("solana", "MOON2", 5.0, "moonshot")]
    assert ep.position_state.get_position("solana", "MOON2")["exit_profile"] == "moonshot"
    # band C never gets real money
    res = ep.handle_moonshot_candidate("solana", "MOON3", "C", 800_000, 400_000, 70, {"signals": 1})
    assert not res["fired"] and "band" in res["reason"]


def test_scheduler_screen_alerts_once(monkeypatch):
    import scheduler
    from layers import layer15_moonshot
    sent = []
    live = time.time()
    p = pair()
    p["pairCreatedAt"] = (live - 10 * 3600) * 1000
    p["baseToken"] = {"symbol": "PAWNS"}
    monkeypatch.setattr(scheduler.layer14, "fetch_dexscreener_batch", lambda chain, addrs: {"MOON": p})

    class SR:
        band = "B"
    monkeypatch.setattr(scheduler, "score_geckoterminal_pools", lambda chain, items: [{"address": "MOON", "score": SR()}])
    monkeypatch.setattr(scheduler, "send_telegram_message", lambda text: sent.append(text))
    items = [{"address": "MOON", "market_cap_usd": 800_000, "liquidity_usd": 90_000},
             {"address": "TINY", "market_cap_usd": 20_000, "liquidity_usd": 5_000}]
    out = scheduler._run_moonshot_screen("solana", items)
    assert out == {"screened": 1, "qualified": 1}
    assert len(sent) == 1 and "MOONSHOT" in sent[0] and "PAWNS" in sent[0]
    assert "moonshot:solana:MOON" in pl._open()
    scheduler._run_moonshot_screen("solana", items)          # cooldown: no repeat alert
    assert len(sent) == 1

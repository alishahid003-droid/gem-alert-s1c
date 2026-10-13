import backtest_sniper as b


def _c(prices, step=60):
    return [{"t": i * step, "o": p, "h": p, "l": p, "c": p} for i, p in enumerate(prices)]


def test_runner_pays_and_loser_stops():
    run = _c([1, 1, 2.2, 5, 11, 20, 8, 8])
    pnl, how = b.simulate(run, 0, "core1")
    assert pnl > 100 and how in ("trailing_stop", "open_at_end", "fully_sold")
    pnl, how = b.simulate(_c([1, 0.9, 0.5, 0.3]), 0, "core1")
    assert how == "stop_loss" and pnl < -50

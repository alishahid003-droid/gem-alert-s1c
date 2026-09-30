"""Sanity checks for backtest_trades' candle simulators (no network)."""
import backtest_trades as bt


def mk(prices, step=300):
    out = []
    for i, (o, h, l, c) in enumerate(prices):
        out.append({"o": o, "h": h, "l": l, "c": c, "unixTime": 1_000_000 + i * step})
    return out


def test_stage_rug_is_stopped_near_minus_25():
    c = mk([(1, 1, 1, 1), (1, 1.05, 0.95, 1.0), (1.0, 1.0, 0.05, 0.05)])
    pnl, how = bt.sim_stage(c, 300, 0, 0.05)
    assert how == "stop_loss" and -0.35 < pnl / bt.POSITION_USD < -0.25


def test_stage_pump_then_dump_is_a_locked_win():
    c = mk([(1, 1, 1, 1), (1, 1.7, 0.99, 1.6), (1.6, 1.6, 0.3, 0.3)])
    pnl, how = bt.sim_stage(c, 300, 0, 0.05)
    assert pnl > 0


def test_scalper_take_profit_then_trail():
    c = mk([(1, 1, 1, 1), (1, 2.6, 0.99, 2.5), (2.5, 2.6, 1.9, 1.9)])
    pnl, how = bt.sim_scalper(c, 300, 0, 0.05)
    assert how == "trail_stop" and pnl > 0


def test_scalper_time_stop():
    c = mk([(1, 1.05, 0.98, 1.0)] * 10)
    pnl, how = bt.sim_scalper(c, 300, 0, 0.05)
    assert how == "time_stop"

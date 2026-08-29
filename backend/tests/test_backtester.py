from datetime import datetime, timezone

import pytest

from app.backtest.backtester import BacktestDataError, Backtester
from app.backtest.cost import CostParams


def _ts(minute):
    return datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp() + minute * 60


def make_spec():
    from app.schemas.strategy import (
        ExecutionFilters,
        MarketRegime,
        RiskManagement,
        Rule,
        SessionWindow,
        StrategyFamily,
        StrategySpec,
    )

    return StrategySpec(
        name="test long pullback",
        version="1.0.0",
        strategy_family=StrategyFamily.trend_pullback,
        supported_pairs=["EURUSD"],
        supported_timeframes=["M5"],
        sessions_utc=[SessionWindow(name="24h", start="00:00", end="23:59")],
        market_regime=MarketRegime(preferred=["trending"]),
        entry_rules=[Rule(id="long_c1", description="bullish bar", expression="close > open")],
        exit_rules=[Rule(id="exit_bear", description="bearish bar", expression="close < open")],
        risk_management=RiskManagement(
            risk_per_trade_pct=0.25,
            max_daily_loss_pct=1.0,
            max_consecutive_losses=3,
            max_open_positions=1,
            max_trades_per_day=5,
            stop_loss_method="ATR",
            stop_loss_parameters={"atr_period": 14, "atr_multiplier": 1.2},
            take_profit_method="risk_reward",
            take_profit_parameters={"risk_reward_ratio": 1.5},
        ),
        execution_filters=ExecutionFilters(
            max_spread_pips=3.0,
            max_slippage_pips=0.5,
            minimum_atr_pips=0.5,
            news_blackout_minutes_before=0,
            news_blackout_minutes_after=0,
        ),
    )


def make_backtester(spread_pips=0.8, slippage_pips=0.0):
    return Backtester(
        spec=make_spec(),
        cost=CostParams(
            spread_pips=spread_pips,
            commission_per_lot=6.0,
            slippage_pips=slippage_pips,
            contract_size=100000.0,
            pip_size=0.0001,
        ),
    )


def candle(i, close, open_px=None, high=None, low=None):
    open_px = float(open_px if open_px is not None else close)
    high = float(high if high is not None else max(open_px, close))
    low = float(low if low is not None else min(open_px, close))
    return {"ts": _ts(i), "open": open_px, "high": high, "low": low, "close": close}


# -- look-ahead bias -----------------------------------------------------

def test_fill_uses_next_bar_open_not_signal_bar_close():
    bt = make_backtester(slippage_pips=0.0)
    # bars 0..4 drift down (no long signal), bar 5 is a bullish close>open
    # -> signal fires on bar 5 close, fill must be bar 6 open (1.1000)
    candles = [candle(i, 1.1100 - i * 0.001) for i in range(5)]
    candles += [candle(5, 1.1000, open_px=1.0995)]           # signal bar
    candles += [candle(6, 1.1030, open_px=1.1000)]           # fill bar w/ gap
    candles += [candle(7, 1.1035), candle(8, 1.1040)]
    result = bt.run(candles, "EURUSD", "M5", starting_balance=100000.0)
    assert result["trades"], "expected at least one trade"
    trade = result["trades"][0]
    assert trade["entry_ts"] == _ts(6), "entry must occur on next bar after signal"
    assert trade["entry_price"] == pytest.approx(1.1000, abs=1e-5)


def test_no_signal_on_last_bar():
    bt = make_backtester()
    candles = [candle(i, 1.1000 + i * 0.001) for i in range(3)]
    # last bar is bullish but there is no next bar to fill on -> no pending order
    candles += [candle(3, 1.1050, open_px=1.1030)]
    result = bt.run(candles, "EURUSD", "M5")
    assert result["trades"] == []


# -- stop loss / take profit --------------------------------------------

def _flat_until_candle(n):
    # ensure no long signal before bar index n (force bearish closes)
    return [candle(i, 1.1000 - i * 0.002) for i in range(n)]


def test_stop_loss_wins_when_both_hit_same_bar():
    bt = make_backtester()
    candles = _flat_until_candle(5)
    candles += [candle(5, 1.1000, open_px=1.0995)]    # signal bar (close>open)
    # fill bar: opens 1.1000, pegs stop AND target in the same candle
    candles += [candle(6, 1.1010, open_px=1.1000, high=1.1060, low=1.0960)]
    candles += [candle(7, 1.1000, open_px=1.1000)]
    res = bt.run(candles, "EURUSD", "M5")
    trade = res["trades"][0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == pytest.approx(trade["stop"], abs=1e-5)
    assert trade["net_pnl"] < 0


def test_take_profit_when_only_target_hit():
    bt = make_backtester()
    candles = _flat_until_candle(5)
    candles += [candle(5, 1.1000, open_px=1.0995)]
    # target hit, stop untouched
    candles += [candle(6, 1.1030, open_px=1.1000, high=1.1040, low=1.0995)]
    candles += [candle(7, 1.1030, open_px=1.1030)]
    res = bt.run(candles, "EURUSD", "M5")
    trade = res["trades"][0]
    assert trade["exit_reason"] == "take_profit"
    assert trade["exit_price"] == pytest.approx(trade["target"], abs=1e-5)
    assert trade["net_pnl"] > 0


# -- costs ---------------------------------------------------------------

def test_spread_and_slippage_costs_are_applied():
    bt = make_backtester(spread_pips=1.0, slippage_pips=0.5)
    candles = _flat_until_candle(5)
    candles += [candle(5, 1.1000, open_px=1.0995)]
    candles += [candle(6, 1.1010, open_px=1.1000, high=1.1040, low=1.0995)]
    candles += [candle(7, 1.1030, open_px=1.1030)]
    res = bt.run(candles, "EURUSD", "M5")
    trade = res["trades"][0]
    assert trade["spread_cost"] > 0
    assert trade["slippage_cost"] > 0
    assert trade["commission"] > 0
    assert trade["net_pnl"] < trade["gross_pnl"]


def test_zero_cost_model():
    bt = make_backtester(spread_pips=0.0, slippage_pips=0.0)
    bt.cost.commission_per_lot = 0.0
    candles = _flat_until_candle(5)
    candles += [candle(5, 1.1000, open_px=1.0995)]
    candles += [candle(6, 1.1010, open_px=1.1000, high=1.1040, low=1.0995)]
    candles += [candle(7, 1.1030, open_px=1.1030)]
    res = bt.run(candles, "EURUSD", "M5")
    trade = res["trades"][0]
    assert trade["slippage_cost"] == 0
    assert trade["commission"] == 0


# -- reproducibility -----------------------------------------------------

def test_backtest_is_deterministic():
    candles = []
    for i in range(200):
        close = 1.1000 + 0.00025 * (i % 40) - 0.00015 * ((i * 7) % 60)
        candles.append(candle(i, round(close, 5)))
    bt1 = make_backtester(slippage_pips=0.2)
    bt2 = make_backtester(slippage_pips=0.2)
    r1 = bt1.run(candles, "EURUSD", "M5")
    r2 = bt2.run(candles, "EURUSD", "M5")
    assert r1["trades"] == r2["trades"]
    assert r1["equity_curve"] == r2["equity_curve"]
    assert r1["ending_balance"] == r2["ending_balance"]


# -- data quality --------------------------------------------------------

def test_na_price_rejected():
    bt = make_backtester()
    candles = [candle(i, 1.1000) for i in range(10)]
    candles[4]["close"] = float("nan")
    with pytest.raises(BacktestDataError):
        bt.run(candles, "EURUSD", "M5")


def test_high_below_low_rejected():
    bt = make_backtester()
    candles = [candle(i, 1.1000) for i in range(10)]
    candles[3]["high"] = 1.0990
    with pytest.raises(BacktestDataError):
        bt.run(candles, "EURUSD", "M5")


def test_high_below_open_close_rejected():
    bt = make_backtester()
    candles = [candle(i, 1.1000) for i in range(10)]
    candles[5]["high"] = 1.0995
    candles[5]["open"] = 1.1005
    candles[5]["close"] = 1.1004
    with pytest.raises(BacktestDataError):
        bt.run(candles, "EURUSD", "M5")


def test_missing_candle_gap_rejected_when_strict():
    bt = make_backtester()
    candles = [candle(i, 1.1000) for i in range(10)]
    candles.append({"ts": _ts(30), "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105})
    with pytest.raises(BacktestDataError):
        bt.run(candles, "EURUSD", "M5", require_contiguous=True)


def test_missing_candle_gap_allowed_by_default():
    bt = make_backtester()
    candles = [candle(i, 1.1000) for i in range(10)]
    candles.append({"ts": _ts(30), "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105})
    res = bt.run(candles, "EURUSD", "M5")  # should not raise
    assert "equity_curve" in res
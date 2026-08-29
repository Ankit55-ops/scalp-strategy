from datetime import datetime, timezone

from app.backtest.backtester import Backtester
from app.backtest.cost import CostParams
from app.backtest.validation import walk_forward_test


def _mk_candles(n):
    out = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    for i in range(n):
        close = 1.10 + (0.0005 * (i % 90)) - (0.0003 * (i % 130))
        out.append(
            {
                "ts": base + i * 300,
                "open": round(close - 0.0001, 5),
                "high": round(close + 0.0004, 5),
                "low": round(close - 0.0004, 5),
                "close": round(close, 5),
            }
        )
    return out


def _spec():
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
        name="wf test",
        version="1.0.0",
        strategy_family=StrategyFamily.trend_pullback,
        supported_pairs=["EURUSD"],
        supported_timeframes=["M5"],
        sessions_utc=[SessionWindow(name="24h", start="00:00", end="23:59")],
        market_regime=MarketRegime(preferred=["trending"]),
        entry_rules=[Rule(id="long_c1", description="", expression="close > open")],
        exit_rules=[Rule(id="exit_bear", description="", expression="close < open")],
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


def test_walk_forward_insufficient_data():
    candles = _mk_candles(100)
    spec = _spec()
    cost = CostParams(spread_pips=0.8, contract_size=100000.0, pip_size=0.0001)

    def factory(cands):
        return Backtester(spec, cost)

    res = walk_forward_test(factory, candles, window_bars=4000, step_bars=2000)
    assert res["completed"] is False


def test_walk_forward_completes_windows():
    candles = _mk_candles(9000)
    spec = _spec()
    cost = CostParams(spread_pips=0.8, contract_size=100000.0, pip_size=0.0001)

    def factory(cands):
        return Backtester(spec, cost)

    res = walk_forward_test(factory, candles, window_bars=4000, step_bars=2000)
    assert res["completed"] is True
    assert res["oos_window_count"] == 2
    assert len(res["windows"]) == 2
    # each window must report IS and OOS data
    for w in res["windows"]:
        assert w["is_net"] is not None
        assert w["oos_net"] is not None
        assert w["oos_start"] > w["is_start"]
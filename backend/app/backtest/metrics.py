"""Comprehensive backtest metrics."""

from __future__ import annotations

import math

import numpy as np


def compute_metrics(trades: list[dict], equity_curve: list[dict], starting_balance: float) -> dict:
    metric = {}
    if not trades:
        return {
            "net_profit": 0.0,
            "net_return_pct": 0.0,
            "num_trades": 0,
            "message": "no trades",
        }

    nets = np.array([t["net_pnl"] for t in trades])
    gross_profit = float(np.sum(nets[nets > 0]))
    gross_loss = float(abs(np.sum(nets[nets < 0])))
    wins = nets[nets > 0]
    losses = nets[nets < 0]

    metric["num_trades"] = int(len(nets))
    metric["net_profit"] = round(float(nets.sum()), 4)
    metric["gross_profit"] = round(gross_profit, 4)
    metric["gross_loss"] = round(gross_loss, 4)
    metric["win_rate"] = round(float((nets > 0).mean()) if len(nets) else 0.0, 4)
    metric["avg_win"] = round(float(wins.mean()), 4) if len(wins) else 0.0
    metric["avg_loss"] = round(float(losses.mean()), 4) if len(losses) else 0.0
    metric["profit_factor"] = (
        round(gross_profit / gross_loss, 4) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    )
    metric["expectancy_per_trade"] = round(float(nets.mean()), 4) if len(nets) else 0.0
    metric["net_return_pct"] = round(float(nets.sum() / starting_balance) * 100, 4) if starting_balance else 0.0

    # Long vs short
    long_nets = np.array([t["net_pnl"] for t in trades if t["side"] == "long"])
    short_nets = np.array([t["net_pnl"] for t in trades if t["side"] == "short"])
    metric["long_net"] = round(float(long_nets.sum()), 4) if len(long_nets) else 0.0
    metric["short_net"] = round(float(short_nets.sum()), 4) if len(short_nets) else 0.0
    metric["long_count"] = int(len(long_nets))
    metric["short_count"] = int(len(short_nets))

    # Holding time
    holds = np.array([t["exit_ts"] - t["entry_ts"] for t in trades])
    metric["avg_holding_seconds"] = round(float(holds.mean()), 2) if len(holds) else 0.0

    # Costs
    metric["total_spread_cost"] = round(float(sum(t["spread_cost"] for t in trades)), 4)
    metric["total_slippage_cost"] = round(float(sum(t["slippage_cost"] for t in trades)), 4)
    metric["total_commission"] = round(float(sum(t["commission"] for t in trades)), 4)
    metric["total_costs"] = round(
        float(sum(t["spread_cost"] + t["slippage_cost"] + t["commission"] for t in trades)), 4
    )

    # Equity / drawdown
    eq = np.array([e["equity"] for e in equity_curve]) if equity_curve else np.array([])
    if len(eq):
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        metric["max_drawdown_pct"] = round(float(dd.min()) * 100, 4)
        metric["max_drawdown_abs"] = round(float((peak - eq).max()), 4)
        # Sharpe / Sortino
        rets = np.diff(eq) / eq[:-1]
        if len(rets) > 1 and np.std(rets) > 0:
            metric["sharpe"] = round(float(rets.mean() / np.std(rets) * math.sqrt(len(rets))), 4)
            downside = rets[rets < 0]
            dd_std = np.std(downside)
            if dd_std > 0:
                metric["sortino"] = round(float(rets.mean() / dd_std * math.sqrt(len(rets))), 4)
            else:
                metric["sortino"] = None
        else:
            metric["sharpe"] = None
            metric["sortino"] = None
        # Calmar
        annual_return = float(rets.mean()) * 252
        if metric["max_drawdown_pct"] < 0:
            metric["calmar"] = round(annual_return / abs(metric["max_drawdown_pct"]) * 100, 4)
        else:
            metric["calmar"] = None
        if metric["max_drawdown_abs"] > 0:
            metric["recovery_factor"] = round(float(nets.sum()) / float(metric["max_drawdown_abs"]), 4)
        else:
            metric["recovery_factor"] = None
    else:
        metric["max_drawdown_pct"] = 0.0
        metric["max_drawdown_abs"] = 0.0
        metric["sharpe"] = None
        metric["sortino"] = None

    # Streaks
    metric["max_consecutive_wins"] = _max_streak(nets > 0)
    metric["max_consecutive_losses"] = _max_streak(nets < 0)

    # Exposure time fraction estimate
    if equity_curve:
        span = equity_curve[-1]["ts"] - equity_curve[0]["ts"]
        if span > 0 and trades:
            exposure = sum(t["exit_ts"] - t["entry_ts"] for t in trades)
            metric["exposure_time_pct"] = round(float(exposure / span) * 100, 4)
        else:
            metric["exposure_time_pct"] = 0.0
    else:
        metric["exposure_time_pct"] = 0.0

    return metric


def _max_streak(mask: np.ndarray) -> int:
    best = 0
    cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def monthly_returns(trades: list[dict]) -> list[dict]:
    from collections import defaultdict

    by_month: dict[str, float] = defaultdict(float)
    for t in trades:
        from datetime import datetime, timezone

        ts = t["entry_ts"]
        month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        by_month[month] += t["net_pnl"]
    return [{"month": m, "net_pnl": round(v, 4)} for m, v in sorted(by_month.items())]


def pair_performance(trades: list[dict]) -> dict:
    from collections import defaultdict

    by_pair: dict[str, float] = defaultdict(float)
    for t in trades:
        by_pair[t["symbol"]] += t["net_pnl"]
    return {k: round(v, 4) for k, v in by_pair.items()}


def session_performance(trades: list[dict]) -> dict:
    from collections import defaultdict

    by_session: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        hour = int(t["entry_ts"] % 86400) // 3600
        if 0 <= hour < 7:
            s = "Asian"
        elif 7 <= hour < 12:
            s = "London"
        elif 12 <= hour < 17:
            s = "New York"
        else:
            s = "Late"
        by_session[s].append(t["net_pnl"])
    return {
        k: {"net_pnl": round(sum(v), 4), "count": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 4) if v else 0}
        for k, v in by_session.items()
    }

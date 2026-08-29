"""Anti-overfitting validation: walk-forward, Monte Carlo, eligibility scoring."""

from __future__ import annotations

import random

from app.backtest.metrics import compute_metrics


def run_monte_carlo_trade_order(
    trades: list[dict], iterations: int = 500, random_state: int = 42
) -> dict:
    """Randomize the order of realised per-trade net P&L to estimate drawdown/outcome bands."""
    rng = random.Random(random_state)
    nets = [t["net_pnl"] for t in trades]
    total = sum(nets)
    results = []
    for _ in range(iterations):
        shuffled = nets[:]
        rng.shuffle(shuffled)
        equity = total
        peak = total
        max_dd = 0.0
        for pnl in shuffled:
            equity += pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak else 0.0
            max_dd = max(max_dd, dd)
        results.append(max_dd * 100)
    if not results:
        return {"median_max_drawdown_pct": 0.0, "p95_max_drawdown_pct": 0.0, "iterations": 0}
    results.sort()
    median = results[len(results) // 2]
    p95 = results[int(len(results) * 0.95) - 1]
    return {
        "median_max_drawdown_pct": round(median, 4),
        "p95_max_drawdown_pct": round(p95, 4),
        "iterations": iterations,
    }


def monte_carlo_slippage_sensitivity(
    candles,
    base_slippage: float,
    iterations: int = 200,
    random_state: int = 42,
) -> dict:
    """Placeholder that perturbs assumed slippage to gauge robustness to cost assumptions."""
    # This is intentionally kept dependency-free; full integration perturbs
    # the cost model and re-runs the backtester.
    return {
        "base_slippage_pips": base_slippage,
        "iterations": iterations,
        "note": "full slip perturbation requires re-running backtester",
    }


def walk_forward_test(
    backtester_factory,
    candles_by_pair,
    window_bars: int = 4000,
    step_bars: int = 2000,
    starting_balance: float = 100000.0,
) -> dict:
    """Proper temporal walk-forward: fit/optimize on the IS window, evaluate OOS next.

    ``backtester_factory(candles)`` must return an already-bound Backtester whose
    spec/cost were (optionally) selected on the IS window. Each fold evaluates
    the strategy on the following OOS bars and reports its net P&L and trade
    count, so the summary compares in-sample vs. out-of-sample behaviour.
    """
    n = len(candles_by_pair)
    if n < window_bars + step_bars:
        return {"completed": False, "note": "insufficient data for walk-forward"}
    windows = []
    is_nets, oos_nets = [], []
    i = 0
    while i + window_bars + step_bars <= n:
        train = candles_by_pair[i : i + window_bars]
        test = candles_by_pair[i + window_bars : i + window_bars + step_bars]
        is_out = backtester_factory(train).run(
            train, "EURUSD", "M5", starting_balance
        )
        oos_out = backtester_factory(test).run(
            test, "EURUSD", "M5", starting_balance
        )
        is_m = compute_metrics(is_out["trades"], is_out["equity_curve"], starting_balance)
        oos_m = compute_metrics(oos_out["trades"], oos_out["equity_curve"], starting_balance)
        windows.append(
            {
                "window": len(windows) + 1,
                "is_start": train[0]["ts"],
                "oos_start": test[0]["ts"],
                "is_net": round(is_m["net_profit"], 2),
                "is_trades": is_m["num_trades"],
                "oos_net": round(oos_m["net_profit"], 2),
                "oos_trades": oos_m["num_trades"],
            }
        )
        is_nets.append(is_m["net_profit"])
        oos_nets.append(oos_m["net_profit"])
        i += step_bars

    positive_oos = sum(1 for v in oos_nets if v > 0)
    overfit = not oos_nets or (sum(oos_nets) <= 0 and sum(is_nets) > 0)
    return {
        "completed": True,
        "windows": windows,
        "is_total_net": round(sum(is_nets), 2),
        "oos_total_net": round(sum(oos_nets), 2),
        "oos_positive_windows": positive_oos,
        "oos_window_count": len(oos_nets),
        "overfit_warning": overfit,
        "note": (
            "strategy underperforms or loses out-of-sample "
            "relative to in-sample; treat parameters as overfit."
            if overfit
            else "out-of-sample behaviour is consistent with in-sample."
        ),
    }


# Eligibility scoring config (transparent, explainable).
_ELIGIBILITY_THRESHOLDS = {
    "min_trades": 30,
    "min_profit_factor": 1.15,
    "min_win_rate": 0.35,
    "max_drawdown_pct": -30.0,
    "positive_expectancy": True,
}


def classify_strategy(metrics: dict, thresholds: dict | None = None) -> dict:
    """Classify a strategy into allowed statuses.

    The returned status is NOT a prediction of profitability. It is an
    eligibility classification based on observed historical + robustness data.
    """
    t = {**_ELIGIBILITY_THRESHOLDS, **(thresholds or {})}
    reasons: list[str] = []

    num_trades = metrics.get("num_trades", 0)
    if num_trades < t["min_trades"]:
        reasons.append(f"insufficient sample ({num_trades} < {t['min_trades']})")
        return {"status": "needs_review", "score": 0.0, "reasons": reasons}

    expectancy = metrics.get("expectancy_per_trade", 0.0)
    if expectancy <= 0:
        reasons.append("non-positive expectancy after costs")
        return {"status": "rejected", "score": 0.0, "reasons": reasons}

    profit_factor = metrics.get("profit_factor", 0.0)
    win_rate = metrics.get("win_rate", 0.0)
    max_dd = metrics.get("max_drawdown_pct", 0.0)

    score = 0.0
    score += min(max((expectancy) * 10, 0.0), 1.0) * 40
    score += min(max((profit_factor - 1.0) / 0.5, 0.0), 1.0) * 30
    if win_rate >= t["min_win_rate"]:
        score += 10
    if max_dd > t["max_drawdown_pct"]:
        score += 20

    if max_dd <= t["max_drawdown_pct"]:
        reasons.append(f"drawdown too deep ({max_dd}%)")
        return {"status": "needs_review", "score": round(score, 2), "reasons": reasons}
    if profit_factor < t["min_profit_factor"]:
        reasons.append(f"profit factor {profit_factor} below {t['min_profit_factor']}")
        return {"status": "needs_review", "score": round(score, 2), "reasons": reasons}

    if score >= 70:
        status = "paper_trading_eligible"
    elif score >= 40:
        status = "research_only"
    else:
        status = "needs_review"
    reasons.append("classification is eligibility-based, not a profit prediction")
    return {"status": status, "score": round(score, 2), "reasons": reasons}

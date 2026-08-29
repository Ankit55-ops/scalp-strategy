"""Backtest orchestration: fetch data, run, metrics, validation, persist."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.backtest.backtester import Backtester
from app.backtest.cost import CostParams
from app.backtest.metrics import (
    compute_metrics,
    monthly_returns,
    pair_performance,
    session_performance,
)
from app.backtest.validation import (
    classify_strategy,
    run_monte_carlo_trade_order,
    walk_forward_test,
)
from app.models import BacktestJob, BacktestMetric, BacktestRun, SimulatedOrder, Strategy
from app.providers.factory import get_market_data_provider
from app.schemas.backtest import BacktestRequest
from app.schemas.strategy import StrategySpec
from app.services.market_math import pip_size


def _iso_to_ts(s: str) -> float:
    if len(s) == 10:
        s = s + "T00:00:00"
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def build_cost_params(req: BacktestRequest, symbol: str) -> CostParams:
    ps = pip_size("JPY" if symbol.upper().endswith("JPY") else "USD")
    return CostParams(
        spread_pips=req.spread_pips or 0.8,
        commission_per_lot=req.commission_per_lot or 3.0,
        slippage_pips=req.slippage_pips or 0.3,
        contract_size=100000.0,
        pip_size=ps,
    )


def run_backtest(db: Session, job: BacktestJob, strategy: Strategy) -> BacktestRun:
    req = BacktestRequest.model_validate(job.params)
    provider = get_market_data_provider("mock")
    start = datetime.fromtimestamp(_iso_to_ts(req.date_from), tz=timezone.utc)
    end = datetime.fromtimestamp(_iso_to_ts(req.date_to), tz=timezone.utc)

    spec = strategy.spec

    spec_model = StrategySpec.model_validate(spec)

    run = BacktestRun(
        job_id=job.id,
        status="running",
        start_ts=_iso_to_ts(req.date_from),
        end_ts=_iso_to_ts(req.date_to),
    )
    db.add(run)
    db.commit()

    all_trades = []
    all_equity = []
    for pair in req.pairs:
        candles = provider.get_historical_candles(pair, req.timeframe, start, end)
        if not candles:
            job.status = "failed"
            job.error = f"no candle data for {pair}"
            db.commit()
            return run
        cost = build_cost_params(req, pair)
        bt = Backtester(spec_model, cost, risk_engine=None)
        out = bt.run(candles, pair, req.timeframe, req.balance)
        all_trades.extend(out["trades"])
        all_equity = all_equity or out["equity_curve"]

    metrics = compute_metrics(all_trades, all_equity, req.balance)
    classification = classify_strategy(metrics)
    metrics["status"] = classification["status"]
    metrics["eligibility_score"] = classification["score"]
    metrics["classification_reasons"] = classification["reasons"]

    robustness: dict = {"status": classification["status"]}
    if req.run_monte_carlo and all_trades:
        robustness["monte_carlo_trade_order"] = run_monte_carlo_trade_order(
            all_trades, iterations=req.mc_iterations
        )
    if req.run_walk_forward:
        if len(req.pairs) == 1:
            try:
                cost = build_cost_params(req, req.pairs[0])

                def factory(cands):
                    return Backtester(spec_model, cost, risk_engine=None)

                robustness["walk_forward"] = walk_forward_test(
                    factory,
                    provider.get_historical_candles(
                        req.pairs[0], req.timeframe, start, end
                    ),
                    window_bars=getattr(req, "wf_window_bars", None) or 4000,
                    step_bars=getattr(req, "wf_step_bars", None) or 2000,
                    starting_balance=req.balance,
                )
            except Exception as exc:
                robustness["walk_forward"] = {
                    "completed": False,
                    "note": f"walk-forward failed: {exc}",
                }
        else:
            robustness["walk_forward"] = {
                "completed": False,
                "note": "walk-forward requires a single pair; select one pair.",
            }

    run.status = "completed"
    run.equity_curve = all_equity
    run.robustness = robustness
    run.validation = {
        "classification": classification,
        "monthly_returns": monthly_returns(all_trades),
        "pair_performance": pair_performance(all_trades),
        "session_performance": session_performance(all_trades),
    }
    db.add(run)
    db.flush()

    # Persist metrics
    metric_rows = []
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and value is not None:
            metric_rows.append(BacktestMetric(run_id=run.id, name=key, value=float(value)))
        else:
            metric_rows.append(BacktestMetric(run_id=run.id, name=key, value=None))
    db.add_all(metric_rows)

    # Persist trades
    for t in all_trades:
        db.add(
            SimulatedOrder(
                run_id=run.id,
                symbol=t["symbol"],
                timeframe=t["timeframe"],
                side=t["side"],
                order_type="market",
                entry_ts=t["entry_ts"],
                exit_ts=t["exit_ts"],
                entry_price=t["entry_price"],
                exit_price=t["exit_price"],
                stop_loss=t["stop"],
                take_profit=t["target"],
                size_units=t["size_units"],
                risk_amount=0.0,
                status="closed",
                reasons_entry=t["reasons_entry"],
                reasons_exit=t["reasons_exit"][-1:] if t["reasons_exit"] else [],
                spread_cost=t["spread_cost"],
                slippage_cost=t["slippage_cost"],
                commission=t["commission"],
                gross_pnl=t["gross_pnl"],
                net_pnl=t["net_pnl"],
                pips=t["pips"],
            )
        )

    job.status = "completed"
    job.progress = 1.0
    db.commit()
    run = db.query(BacktestRun).filter(BacktestRun.id == run.id).one()
    return run

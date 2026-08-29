from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import (
    BacktestJob,
    BacktestMetric,
    BacktestRun,
    SimulatedOrder,
    Strategy,
    User,
    Workspace,
)
from app.schemas.backtest import BacktestJobOut, BacktestRequest
from app.services.backtest_service import run_backtest

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestJobOut, status_code=201)
def create_backtest(
    payload: BacktestRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> BacktestJobOut:
    strategy = db.get(Strategy, payload.strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    job = BacktestJob(
        workspace_id=ws.id,
        strategy_id=strategy.id,
        status="queued",
        progress=0.0,
        params=payload.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        run_backtest(db, job, strategy)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:1000]
        db.commit()
        db.refresh(job)
    return BacktestJobOut(
        id=job.id,
        status=job.status,
        progress=job.progress,
        error=job.error,
    )


def _get_run(db: Session, job_id: str, ws: Workspace) -> BacktestRun:
    job = db.get(BacktestJob, job_id)
    if not job or job.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="backtest job not found")
    run = db.query(BacktestRun).filter(BacktestRun.job_id == job.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="backtest not run")
    return run


@router.get("/{job_id}", response_model=dict)
def get_backtest(
    job_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    run = _get_run(db, job_id, ws)
    metrics = {
        m.name: m.value
        for m in db.query(BacktestMetric).filter(BacktestMetric.run_id == run.id).all()
    }
    return {
        "job_id": job_id,
        "status": run.status,
        "metrics": metrics,
        "validation": run.validation,
        "robustness": run.robustness,
        "starting_balance": run.equity_curve[0]["balance"] if run.equity_curve else 0,
        "ending_balance": run.equity_curve[-1]["balance"] if run.equity_curve else 0,
    }


@router.get("/{job_id}/trades", response_model=list[dict])
def get_backtest_trades(
    job_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    run = _get_run(db, job_id, ws)
    trades = (
        db.query(SimulatedOrder)
        .filter(SimulatedOrder.run_id == run.id)
        .order_by(SimulatedOrder.entry_ts.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "entry_ts": t.entry_ts,
            "exit_ts": t.exit_ts,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_loss": t.stop_loss,
            "take_profit": t.take_profit,
            "pips": t.pips,
            "gross_pnl": t.gross_pnl,
            "net_pnl": t.net_pnl,
            "spread_cost": t.spread_cost,
            "slippage_cost": t.slippage_cost,
            "commission": t.commission,
            "exit_reason": t.reasons_exit[0]["rule_id"] if t.reasons_exit else "n/a",
            "reasons_entry": t.reasons_entry,
            "reasons_exit": t.reasons_exit,
        }
        for t in trades
    ]


@router.get("/{job_id}/equity-curve", response_model=list[dict])
def get_equity_curve(
    job_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    run = _get_run(db, job_id, ws)
    return run.equity_curve or []


@router.get("/{job_id}/chart-data", response_model=dict)
def get_chart_data(
    job_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(BacktestJob, job_id)
    if not job or job.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="backtest job not found")
    run = _get_run(db, job_id, ws)
    trades = (
        db.query(SimulatedOrder)
        .filter(SimulatedOrder.run_id == run.id)
        .all()
    )
    return {
        "equity_curve": run.equity_curve or [],
        "trades": [
            {
                "symbol": t.symbol,
                "side": t.side,
                "entry_ts": t.entry_ts,
                "exit_ts": t.exit_ts,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
            }
            for t in trades
        ],
    }

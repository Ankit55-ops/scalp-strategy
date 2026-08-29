import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.core.config import get_settings
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
from app.tasks.backtest_tasks import run_backtest_job

logger = logging.getLogger("fxscalper.backtests")

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _enqueue_or_run(job: BacktestJob, strategy: Strategy, db: Session) -> None:
    if not get_settings().BACKTEST_ASYNC:
        # Synchronous execution: run inline and mark progress.
        job.status = "running"
        db.commit()
        try:
            run_backtest(db, job, strategy)
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)[:1000]
            db.commit()
        return
    from rq import Queue
    import redis

    queue = Queue(
        "fxscalper",
        connection=redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1),
    )
    queue.enqueue("app.tasks.backtest_tasks:run_backtest_job", job.id, job_timeout=600)


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

    # Idempotent re-submission: reuse an existing identical queued/completed job.
    if payload.idempotency_key:
        existing = (
            db.query(BacktestJob)
            .filter(
                BacktestJob.workspace_id == ws.id,
                BacktestJob.idempotency_key == payload.idempotency_key,
            )
            .order_by(BacktestJob.created_at.desc())
            .first()
        )
        if existing is not None:
            return BacktestJobOut(
                id=existing.id,
                status=existing.status,
                progress=existing.progress,
                error=existing.error,
            )

    job = BacktestJob(
        workspace_id=ws.id,
        strategy_id=strategy.id,
        idempotency_key=payload.idempotency_key,
        status="queued",
        progress=0.0,
        params=payload.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _enqueue_or_run(job, strategy, db)
    db.refresh(job)
    return BacktestJobOut(
        id=job.id,
        status=job.status,
        progress=job.progress,
        error=job.error,
    )


@router.get("", response_model=dict)
def list_backtests(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    q = db.query(BacktestJob).filter(BacktestJob.workspace_id == ws.id)
    if status:
        q = q.filter(BacktestJob.status == status)
    total = q.count()
    rows = q.order_by(BacktestJob.created_at.desc()).offset(offset).limit(limit).all()
    strategy_names = {s.id: s.name for s in db.query(Strategy).filter(Strategy.workspace_id == ws.id).all()}
    run_map: dict[str, BacktestRun] = {}
    run_ids = [j.id for j in rows]
    if run_ids:
        for r in db.query(BacktestRun).filter(BacktestRun.job_id.in_(run_ids)).all():
            run_map[r.job_id] = r
    items = []
    for j in rows:
        run = run_map.get(j.id)
        metrics = {}
        if run is not None:
            metrics = {
                m.name: m.value
                for m in db.query(BacktestMetric).filter(BacktestMetric.run_id == run.id).all()
            }
        items.append(
            {
                "id": j.id,
                "strategy_id": j.strategy_id,
                "strategy_name": strategy_names.get(j.strategy_id, ""),
                "status": j.status,
                "progress": j.progress,
                "error": j.error,
                "created_at": j.created_at.isoformat(),
                "metrics": {k: v for k, v in metrics.items() if k in ("net_profit", "profit_factor", "total_trades", "max_drawdown_pct", "win_rate", "sharpe_ratio")},
            }
        )
    return {"total": total, "items": items}


def _get_job(db: Session, job_id: str, ws: Workspace) -> BacktestJob:
    job = db.get(BacktestJob, job_id)
    if not job or job.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="backtest job not found")
    return job


def _get_run(db: Session, job: BacktestJob) -> BacktestRun:
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
    job = _get_job(db, job_id, ws)
    run = db.query(BacktestRun).filter(BacktestRun.job_id == job.id).first()
    if run is None:
        # Job queued/running/failed — nothing to report beyond job state.
        return {
            "job_id": job_id,
            "status": job.status,
            "progress": job.progress,
            "error": job.error,
        }
    metrics = {
        m.name: m.value
        for m in db.query(BacktestMetric).filter(BacktestMetric.run_id == run.id).all()
    }
    return {
        "job_id": job_id,
        "status": run.status if run.status != "running" else job.status,
        "progress": job.progress,
        "error": job.error,
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
    job = _get_job(db, job_id, ws)
    run = _get_run(db, job)
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
    job = _get_job(db, job_id, ws)
    run = _get_run(db, job)
    return run.equity_curve or []


@router.get("/{job_id}/chart-data", response_model=dict)
def get_chart_data(
    job_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    job = _get_job(db, job_id, ws)
    run = _get_run(db, job)
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
"""Real Historical Data validation endpoints.

The user picks a saved strategy + exact version, a connected provider, a
provider symbol, a timeframe and a date range, configures realistic cost
assumptions, and the system fetches real historical candles, runs the Data
Quality Gate, executes the immutable strategy bar-by-bar, and persists a fully
reproducible run (metrics, trades, signals, cost events, quality report).

Runs are synchronous by default (consistent with backtests) and can be handed
to an RQ worker via VALIDATION_ASYNC=1. Every endpoint is authenticated,
workspace-scoped, and returns only safe metadata.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.core.config import get_settings
from app.db.session import get_db
from app.models import User, Workspace
from app.schemas.real_historical import (
    RealHistoricalValidationPreviewRequest,
    RealHistoricalValidationRequest,
)
from app.services.real_historical_validator import (
    cancel_run,
    create_validation_run,
    export_run,
    get_run,
    get_run_candles,
    list_runs,
    preview_validation,
    run_validation,
)

router = APIRouter(prefix="/real-historical-validations", tags=["real-historical"])


def _enqueue_or_run(db: Session, ws_id: str, run) -> None:
    if not get_settings().VALIDATION_ASYNC:
        run_validation(db, ws_id, run)
        return
    import redis
    from rq import Queue

    queue = Queue(
        "fxscalper",
        connection=redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1),
    )
    queue.enqueue("app.tasks.validation_tasks:run_validation_job", run.id, job_timeout=900)


@router.post("/preview")
def preview(
    payload: RealHistoricalValidationPreviewRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return preview_validation(
            db, ws.id, payload.strategy_id, payload.strategy_version_id,
            payload.connection_id, payload.provider, payload.provider_symbol,
            payload.timeout, payload.start_time_utc, payload.end_time_utc,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", status_code=201)
def create_validation(
    payload: RealHistoricalValidationRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.models import RealHistoricalValidationRun

    # Idempotent re-submission: reuse an identical queued/completed run.
    if payload.idempotency_key:
        existing = (
            db.query(RealHistoricalValidationRun)
            .filter(RealHistoricalValidationRun.workspace_id == ws.id,
                    RealHistoricalValidationRun.idempotency_key == payload.idempotency_key)
            .order_by(RealHistoricalValidationRun.created_at.desc())
            .first()
        )
        if existing is not None:
            return _run_dict(existing)

    try:
        run = create_validation_run(db, ws.id, str(user.id), payload)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _enqueue_or_run(db, ws.id, run)
    return _run_dict(run)


@router.get("")
def list_validations(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
) -> list[dict]:
    rows = list_runs(db, ws.id, limit=limit, offset=offset)
    if status:
        rows = [r for r in rows if r["run_status"] == status]
    return rows


@router.get("/{run_id}")
def get_validation(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return _run_dict(get_run(db, ws.id, run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/candles")
def candles_for_run(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return get_run_candles(db, ws.id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{run_id}/trades")
def trades_for_run(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    from app.models import RealHistoricalValidationTrade

    run = _require_run(db, ws.id, run_id)
    rows = (db.query(RealHistoricalValidationTrade)
            .filter(RealHistoricalValidationTrade.run_id == run.id)
            .order_by(RealHistoricalValidationTrade.entry_ts.asc()).all())
    return [_trade_dict(t) for t in rows]


@router.get("/{run_id}/signals")
def signals_for_run(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    from app.models import RealHistoricalValidationSignal

    run = _require_run(db, ws.id, run_id)
    rows = (db.query(RealHistoricalValidationSignal)
            .filter(RealHistoricalValidationSignal.run_id == run.id)
            .order_by(RealHistoricalValidationSignal.ts.asc()).all())
    return [{"ts": s.ts, "signal": s.signal, "state": s.state,
             "blocked_reason": s.blocked_reason, "price": s.price, "detail": s.detail} for s in rows]


@router.get("/{run_id}/metrics")
def metrics_for_run(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.models import RealHistoricalValidationMetric

    run = _require_run(db, ws.id, run_id)
    rows = (db.query(RealHistoricalValidationMetric)
            .filter(RealHistoricalValidationMetric.run_id == run.id).all())
    values = {m.name: m.value for m in rows if isinstance(m.value, (int, float))}
    details = {m.name: m.extra for m in rows if m.extra}
    return {"metrics": values, "details": details}


@router.get("/{run_id}/data-quality")
def data_quality_for_run(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.models import HistoricalDataQualityReport

    run = _require_run(db, ws.id, run_id)
    report = (db.query(HistoricalDataQualityReport)
              .filter(HistoricalDataQualityReport.validation_run_id == run.id)
              .order_by(HistoricalDataQualityReport.created_at.desc()).first())
    if report is None:
        return {"run_id": run.id, "quality_status": "NOT_AVAILABLE"}
    return {
        "run_id": run.id, "provider_name": report.provider_name,
        "provider_symbol": report.provider_symbol, "canonical_symbol": report.canonical_symbol,
        "timeout": report.timeout, "data_type": report.data_type,
        "requested_start": report.requested_start, "requested_end": report.requested_end,
        "actual_start": report.actual_start, "actual_end": report.actual_end,
        "expected_candles": report.expected_candles, "received_candles": report.received_candles,
        "missing_candles": report.missing_candles,
        "duplicate_candles_removed": report.duplicate_candles_removed,
        "warmup_candles_used": report.warmup_candles_used, "gap_count": report.gap_count,
        "gaps": report.gaps, "feed_delay_warning": report.feed_delay_warning,
        "spread_availability": report.spread_availability,
        "bid_ask_availability": report.bid_ask_availability,
        "cost_model_confidence": report.cost_model_confidence,
        "quality_status": report.quality_status, "details": report.details,
    }


@router.get("/{run_id}/equity-curve")
def equity_curve_for_run(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    run = _require_run(db, ws.id, run_id)
    return {"run_id": run.id, "equity_curve": run.equity_curve or [],
            "drawdown_curve": run.drawdown_curve or []}


@router.post("/{run_id}/cancel")
def cancel_validation(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return cancel_run(db, ws.id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/export")
def export_validation(
    run_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return export_run(db, ws.id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_run(db, ws_id, run_id):
    try:
        return get_run(db, ws_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run_dict(run) -> dict:
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "strategy_id": run.strategy_id,
        "strategy_version_id": run.strategy_version_id,
        "strategy_version": run.strategy_version,
        "provider_name": run.provider_name,
        "provider_symbol": run.provider_symbol,
        "canonical_symbol": run.canonical_symbol,
        "timeout": run.timeout,
        "start_time_utc": run.start_time_utc,
        "end_time_utc": run.end_time_utc,
        "account_currency": run.account_currency,
        "starting_balance": run.starting_balance,
        "execution_model": run.execution_model,
        "source_data_type": run.source_data_type,
        "source_data_hash": run.source_data_hash,
        "candle_count": run.candle_count,
        "missing_candle_count": run.missing_candle_count,
        "data_quality_score": run.data_quality_score,
        "run_status": run.run_status,
        "error_safe": run.error_safe,
        "warnings": run.warnings or [],
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at_utc": run.completed_at_utc,
        "result": run.result,
    }


def _trade_dict(t) -> dict:
    return {
        "id": t.id, "side": t.side, "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
        "entry_price": t.entry_price, "exit_price": t.exit_price,
        "entry_price_basis": t.entry_price_basis, "exit_price_basis": t.exit_price_basis,
        "size_units": t.size_units, "stop": t.stop, "target": t.target,
        "gross_pnl": t.gross_pnl, "net_pnl": t.net_pnl, "spread_cost": t.spread_cost,
        "slippage_cost": t.slippage_cost, "commission": t.commission, "swap": t.swap,
        "pips": t.pips, "risk_amount": t.risk_amount, "risk_reward_ratio": t.risk_reward_ratio,
        "exit_reason": t.exit_reason, "execution_model": t.execution_model,
        "reasons_entry": t.reasons_entry, "reasons_exit": t.reasons_exit,
        "risk_engine_decision": t.risk_engine_decision, "strategy_version": t.strategy_version,
    }
"""Real Backtest endpoints (AI Strategy Tester data source).

Thin, authenticated, workspace-scoped alias over the same real-data engine
used by /real-historical-validations (immutable strategy version, Data Quality
Gate, deterministic bar-by-bar execution, cost model, metrics/trades/signals
persistence). Reuses the validator service and route helpers wholesale; adds a
chart endpoint that assembles candles + trade markers + signals + data gaps +
indicator overlay series for the interactive results chart.

Data sources are labelled explicitly and there is never a silent fallback:
  * a connected real provider supplies REAL data;
  * otherwise USER CSV or the clearly-labelled MOCK adapter;
  * with no provider connected, historical runs refuse to start.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.backtest.indicators import add_indicators
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
    get_run,
    get_run_candles,
    list_runs,
    preview_validation,
    run_validation,
)

logger = logging.getLogger("fxscalper.real_backtests")

router = APIRouter(prefix="/real-backtests", tags=["real-backtests"])


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
def preview_backtest(
    payload: RealHistoricalValidationPreviewRequest,
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
def create_backtest(
    payload: RealHistoricalValidationRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.api.routes.real_historical import _run_dict

    # Idempotent re-submission: reuse an identical queued/completed run.
    if payload.idempotency_key:
        from app.models import RealHistoricalValidationRun

        existing = (
            db.query(RealHistoricalValidationRun)
            .filter(
                RealHistoricalValidationRun.workspace_id == ws.id,
                RealHistoricalValidationRun.idempotency_key == payload.idempotency_key,
            )
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
def list_backtests(
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    return list_runs(db, ws.id, limit=limit, offset=offset)


@router.get("/{run_id}")
def get_backtest(
    run_id: str,
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.api.routes.real_historical import _run_dict

    try:
        return _run_dict(get_run(db, ws.id, run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/trades")
def backtest_trades(
    run_id: str,
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    from app.api.routes.real_historical import _require_run, _trade_dict
    from app.models import RealHistoricalValidationTrade

    run = _require_run(db, ws.id, run_id)
    rows = (
        db.query(RealHistoricalValidationTrade)
        .filter(RealHistoricalValidationTrade.run_id == run.id)
        .order_by(RealHistoricalValidationTrade.entry_ts.asc())
        .all()
    )
    return [_trade_dict(t) for t in rows]


@router.get("/{run_id}/metrics")
def backtest_metrics(
    run_id: str,
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.api.routes.real_historical import _require_run
    from app.models import RealHistoricalValidationMetric

    run = _require_run(db, ws.id, run_id)
    rows = (
        db.query(RealHistoricalValidationMetric)
        .filter(RealHistoricalValidationMetric.run_id == run.id)
        .all()
    )
    values = {m.name: m.value for m in rows if isinstance(m.value, (int, float))}
    details = {m.name: m.extra for m in rows if m.extra}
    return {"metrics": values, "details": details}


@router.get("/{run_id}/data-quality")
def backtest_data_quality(
    run_id: str,
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.api.routes.real_historical import _require_run
    from app.models import HistoricalDataQualityReport

    run = _require_run(db, ws.id, run_id)
    report = (
        db.query(HistoricalDataQualityReport)
        .filter(HistoricalDataQualityReport.validation_run_id == run.id)
        .order_by(HistoricalDataQualityReport.created_at.desc())
        .first()
    )
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


@router.post("/{run_id}/cancel")
def cancel_backtest(
    run_id: str,
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return cancel_run(db, ws.id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/chart")
def backtest_chart(
    run_id: str,
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    """Assembles the interactive chart payload: candles, overlays, markers, gaps."""
    import pandas as pd

    from app.api.routes.real_historical import _trade_dict
    from app.models import (
        HistoricalDataQualityReport,
        RealHistoricalValidationSignal,
        RealHistoricalValidationTrade,
    )

    try:
        run = get_run(db, ws.id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run_out = get_run_candles(db, ws.id, run_id)
    candles = run_out.get("candles", [])

    # -- indicator overlay series (strategy spec only, allow-listed) --------
    overlays: dict[str, dict] = {}
    spec = run.strategy_spec or {}
    indicators = spec.get("indicators", [])
    if candles and indicators:
        try:
            df = pd.DataFrame(candles)
            df["ts"] = df["ts"].astype(float)
            df = df.sort_values("ts").reset_index(drop=True)
            env_df = add_indicators(df, indicators)
            for ind in indicators:
                name = (ind.get("name") if isinstance(ind, dict) else ind).upper()
                period = int(
                    (ind.get("parameters") if isinstance(ind, dict) else ind).get("period", 0)
                )
                col = f"{name}{period}"
                if col in env_df.columns:
                    series = env_df[["ts", col]].dropna()
                    overlays[col] = {
                        "type": "line",
                        "name": col,
                        "values": [
                            {"ts": float(r["ts"]), "value": float(r[col])}
                            for _, r in series.iterrows()
                        ],
                    }
        except Exception as exc:  # defensive; overlays must not break the chart
            logger.warning("chart overlays failed for run %s: %s", run_id, exc)

    # -- markers ------------------------------------------------------------
    trades = (
        db.query(RealHistoricalValidationTrade)
        .filter(RealHistoricalValidationTrade.run_id == run.id)
        .order_by(RealHistoricalValidationTrade.entry_ts.asc())
        .all()
    )
    signals = (
        db.query(RealHistoricalValidationSignal)
        .filter(RealHistoricalValidationSignal.run_id == run.id)
        .order_by(RealHistoricalValidationSignal.ts.asc())
        .all()
    )
    gaps: list[dict] = []
    quality = (
        db.query(HistoricalDataQualityReport)
        .filter(HistoricalDataQualityReport.validation_run_id == run.id)
        .order_by(HistoricalDataQualityReport.created_at.desc())
        .first()
    )
    if quality is not None and quality.gaps:
        for g in quality.gaps:
            if isinstance(g, dict):
                gaps.append({k: g.get(k) for k in ("start_ts", "end_ts", "missing_seconds")})
            else:
                gaps.append({"start_ts": getattr(g, "start_ts", None), "end_ts": getattr(g, "end_ts", None)})

    return {
        "run": _run_out_for_chart(run_out, run),
        "candles": candles,
        "overlays": overlays,
        "trades": [_trade_dict(t) for t in trades],
        "signals": [
            {"ts": s.ts, "signal": s.signal, "state": s.state,
             "blocked_reason": s.blocked_reason, "price": s.price}
            for s in signals
        ],
        "gaps": gaps,
    }


def _run_out_for_chart(run_out: dict, run) -> dict:
    return {
        "run_id": run.id,
        "run_status": run.run_status,
        "provider": run.provider_name,
        "provider_symbol": run.provider_symbol,
        "canonical_symbol": run.canonical_symbol,
        "timeout": run.timeout,
        "execution_model": run.execution_model,
        "source_data_type": run.source_data_type,
        "source_data_hash": run.source_data_hash,
        "candle_count": run_out.get("candle_count", 0),
        "start_time_utc": run.start_time_utc,
        "end_time_utc": run.end_time_utc,
        "data_quality_score": run.data_quality_score,
        "warnings": run.warnings or [],
        "error_safe": run.error_safe,
    }
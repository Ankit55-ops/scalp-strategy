from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from datetime import datetime, timedelta, timezone

from app.ai.architect import generate
from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import BacktestJob, Strategy, StrategyVersion, User, Workspace
from app.schemas.api_strategy import (
    StrategyCreate,
    StrategyGenerateRequest,
    StrategyGenerateResponse,
    StrategyGenerateResponseItem,
    StrategyUpdate,
    StrategyVersionCreate,
)
from app.schemas.strategy import StrategySpec
from app.schemas.strategy_check import StrategyCheckReport
from app.services.audit import AuditService
from app.services import feed_health
from app.services.provider_service import get_active_provider
from app.services.strategy_check import run_strategy_check
from app.services.strategy_service import add_version, create_strategy

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("/generate", response_model=StrategyGenerateResponse)
def generate_strategies(
    payload: StrategyGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyGenerateResponse:
    candidates = generate(payload)
    items = [
        StrategyGenerateResponseItem(candidate_id=cid, spec=spec)
        for cid, spec in candidates
    ]
    return StrategyGenerateResponse(candidates=items)


@router.get("", response_model=list[dict])
def list_strategies(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(Strategy)
        .filter(Strategy.workspace_id == ws.id)
        .order_by(Strategy.created_at.desc())
        .all()
    )
    return [
        {
            "id": s.id,
            "name": s.name,
            "strategy_family": s.strategy_family,
            "current_version": s.current_version,
            "status": s.status,
            "created_at": s.created_at.isoformat(),
        }
        for s in rows
    ]


@router.get("/{strategy_id}", response_model=dict)
def get_strategy(
    strategy_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    return {
        "id": strategy.id,
        "name": strategy.name,
        "strategy_family": strategy.strategy_family,
        "current_version": strategy.current_version,
        "status": strategy.status,
        "spec": strategy.spec,
        "created_at": strategy.created_at.isoformat(),
        "updated_at": strategy.updated_at.isoformat(),
    }


@router.post("", status_code=201, response_model=dict)
def save_strategy(
    payload: StrategyCreate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = create_strategy(db, ws.id, payload.spec, payload.notes)
    return {
        "id": strategy.id,
        "name": strategy.name,
        "current_version": strategy.current_version,
        "status": strategy.status,
    }


@router.post("/{strategy_id}/versions", response_model=dict)
def create_version(
    strategy_id: str,
    payload: StrategyVersionCreate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    version = add_version(db, strategy, payload.spec, payload.notes)
    return {
        "version": version.version,
        "notes": version.notes,
        "created_at": version.created_at.isoformat(),
    }


@router.get("/{strategy_id}/versions", response_model=list[dict])
def list_versions(
    strategy_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    versions = (
        db.query(StrategyVersion)
        .filter(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.created_at.desc())
        .all()
    )
    return [
        {
            "id": v.id,
            "version": v.version,
            "notes": v.notes,
            "created_at": v.created_at.isoformat(),
        }
        for v in versions
    ]


@router.get("/{strategy_id}/versions/{version}", response_model=dict)
def get_version(
    strategy_id: str,
    version: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    v = (
        db.query(StrategyVersion)
        .filter(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.version == version,
        )
        .order_by(StrategyVersion.created_at.desc())
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="version not found")
    return {"id": v.id, "version": v.version, "spec": v.spec, "notes": v.notes}


@router.put("/{strategy_id}", response_model=dict)
def update_strategy(
    strategy_id: str,
    payload: StrategyUpdate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    if payload.name is not None:
        strategy.name = payload.name
    if payload.status is not None:
        strategy.status = payload.status
    if payload.spec is not None:
        spec = payload.spec
        if payload.name is not None:
            spec.name = payload.name
        add_version(db, strategy, spec, payload.notes)
    db.commit()
    db.refresh(strategy)
    return {
        "id": strategy.id,
        "name": strategy.name,
        "status": strategy.status,
        "current_version": strategy.current_version,
    }


@router.post("/{strategy_id}/check", response_model=StrategyCheckReport)
def check_strategy(
    strategy_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    spec = StrategySpec.model_validate(strategy.spec)

    try:
        symbols = get_active_provider(db, ws.id).list_symbols()
    except Exception:  # noqa: BLE001
        symbols = None

    live_context = None
    try:
        pair = spec.supported_pairs[0]
        q = feed_health.get_quote(db, ws.id, pair, mark_stale=True)
        live_context = {
            "symbol": pair,
            "provider": q.get("provider", q.get("source")),
            "feed_state": q.get("feed_state"),
            "market_status": q.get("market_status"),
            "spread_pips": q.get("spread_pips"),
            "is_stale": q.get("is_stale"),
            "max_spread_pips": spec.execution_filters.max_spread_pips,
        }
    except Exception:  # noqa: BLE001
        live_context = None

    latest_metrics = None
    latest_job = (
        db.query(BacktestJob)
        .filter(
            BacktestJob.strategy_id == strategy.id,
            BacktestJob.status == "completed",
        )
        .order_by(BacktestJob.created_at.desc())
        .first()
    )
    if latest_job is not None and latest_job.run is not None:
        latest_metrics = {m.name: m.value for m in latest_job.run.metrics}

    report = run_strategy_check(
        spec, available_symbols=symbols, latest_metrics=latest_metrics, live_context=live_context
    )

    intrabar = None
    try:
        from app.services import signal_engine

        tf = spec.supported_timeframes[0]
        pair = spec.supported_pairs[0]
        provider = get_active_provider(db, ws.id)
        end_dt = datetime.now(timezone.utc)
        candles = provider.get_historical_candles(pair, tf, end_dt - timedelta(days=7), end_dt)
        quote = feed_health.get_quote(db, ws.id, pair, mark_stale=True)
        intrabar = signal_engine.intrabar_preview(
            db, ws.id, strategy, spec, pair.upper(), tf, candles, quote
        )
    except Exception:  # noqa: BLE001
        intrabar = None

    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="strategy_check",
        resource_type="strategy",
        resource_id=strategy.id,
        payload={"version": strategy.current_version or spec.version, "overall": report["overall"]},
    )
    _record_signal_event(db, ws.id, strategy, spec, report, live_context)
    return {
        "strategy_id": strategy.id,
        "version": strategy.current_version or spec.version,
        **report,
        "intrabar": intrabar,
    }


def _record_signal_event(db: Session, workspace_id: str, strategy: Strategy, spec, report: dict, live_context: dict | None) -> None:
    from app.models import StrategySignalEvent

    symbol = spec.supported_pairs[0]
    pair_display = live_context and live_context.get("symbol") or symbol
    blocked_reason = None
    state = "monitoring"
    signal_label = "NO_SIGNAL"
    if live_context:
        feed = live_context.get("feed_state")
        if report["overall"] == "fail":
            state = "blocked"
            blocked_reason = "; ".join(c["detail"] for c in report["checks"] if c["severity"] == "fail")
        elif feed in ("STALE", "DISCONNECTED", "CONNECTING"):
            state = "blocked"
            blocked_reason = f"feed {feed}"
        else:
            state = "ready"
    db.add(
        StrategySignalEvent(
            workspace_id=workspace_id,
            strategy_id=strategy.id,
            strategy_version=strategy.current_version or spec.version,
            symbol=pair_display,
            timeframe=spec.supported_timeframes[0],
            signal="none",
            signal_label=signal_label,
            state=state,
            blocked_reason=blocked_reason,
            detail={
                "overall": report["overall"],
                "feed": live_context and live_context.get("feed_state"),
                "provider": live_context and live_context.get("provider"),
                "spread_pips": live_context and live_context.get("spread_pips"),
            },
            price=0.0,
            spread_pips=live_context and live_context.get("spread_pips") or 0.0,
        )
    )
    db.commit()


@router.get("/{strategy_id}/signals", response_model=dict)
def strategy_signals(
    strategy_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(25, ge=1, le=200),
) -> dict:
    from app.models import StrategySignalEvent

    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    rows = (
        db.query(StrategySignalEvent)
        .filter(StrategySignalEvent.strategy_id == strategy.id)
        .order_by(StrategySignalEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "strategy_id": strategy.id,
        "signals": [
            {
                "id": e.id,
                "created_at": e.created_at.isoformat(),
                "symbol": e.symbol,
                "timeframe": e.timeframe,
                "signal": e.signal,
                "signal_label": e.signal_label,
                "state": e.state,
                "blocked_reason": e.blocked_reason,
                "price": e.price,
                "spread_pips": e.spread_pips,
                "detail": e.detail,
            }
            for e in rows
        ],
    }


@router.delete("/{strategy_id}", response_model=dict)
def delete_strategy(
    strategy_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    db.delete(strategy)
    db.commit()
    return {"id": strategy_id, "deleted": True}

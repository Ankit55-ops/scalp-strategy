from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_current_workspace,
    require_superuser,
)
from app.db.session import get_db
from app.models import (
    BrokerConnection,
    LiveDeploymentRequest,
    PaperAccount,
    SimulatedOrder,
    Strategy,
    User,
    Workspace,
)
from app.schemas.broker import (
    LiveDeploymentApprove,
    LiveDeploymentReject,
    LiveDeploymentRequestCreate,
)
from app.services.audit import AuditService

router = APIRouter(prefix="/live-deployments", tags=["live-deployments"])

MIN_PAPER_TRADES = 30


@router.get("/config", response_model=dict)
def live_trading_config(
    user: User = Depends(get_current_user),
) -> dict:
    from app.core.config import get_settings

    s = get_settings()
    return {
        "live_trading_enabled": bool(s.LIVE_TRADING_ENABLED),
        "practice_broker_dry_run": bool(s.BROKER_PRACTICE_DRY_RUN),
        "broker_provider": s.BROKER_PROVIDER,
        "market_data_provider": getattr(s, "MARKET_DATA_PROVIDER", "mock"),
        "note": "live execution is globally disabled by default; enable with LIVE_TRADING_ENABLED",
    }


def _paper_trade_count(db: Session, ws: Workspace) -> int:
    account = (
        db.query(PaperAccount)
        .filter(PaperAccount.workspace_id == ws.id)
        .first()
    )
    if account is None:
        return 0
    return (
        db.query(SimulatedOrder)
        .filter(
            SimulatedOrder.paper_account_id == account.id,
            SimulatedOrder.status == "closed",
        )
        .count()
    )


def _risk_profile_ok(db: Session, ws: Workspace) -> tuple[bool, str]:
    from app.models import RiskProfile

    profile = (
        db.query(RiskProfile)
        .filter(RiskProfile.workspace_id == ws.id, RiskProfile.is_active.is_(True))
        .first()
    )
    if profile is None:
        return False, "no active risk profile configured"
    return True, "active risk profile detected"


def _gate(db: Session, deployment: LiveDeploymentRequest, ws: Workspace) -> list[str]:
    """Return a list of unmet conditions. Empty list means deployable."""
    unmet: list[str] = []
    if not deployment.risk_acknowledged:
        unmet.append("risk acknowledgment required")
    trade_count = _paper_trade_count(db, ws)
    if trade_count < MIN_PAPER_TRADES:
        unmet.append(f"paper trading track record not satisfied ({trade_count}/{MIN_PAPER_TRADES} trades)")
    ok, note = _risk_profile_ok(db, ws)
    if not ok:
        unmet.append(note)
    return unmet


def _refresh_checks(
    deployment: LiveDeploymentRequest, db: Session, ws: Workspace
) -> LiveDeploymentRequest:
    trade_count = _paper_trade_count(db, ws)
    tracked_ok, tracked_note = trade_count >= MIN_PAPER_TRADES, (
        f"{trade_count}/{MIN_PAPER_TRADES} closed paper trades"
    )
    rp_ok, rp_note = _risk_profile_ok(db, ws)
    deployment.checks = {
        "paper_track_record": {
            "passed": tracked_ok,
            "closed_trades": trade_count,
            "required_min_trades": MIN_PAPER_TRADES,
            "note": tracked_note,
        },
        "risk_profile": {
            "passed": rp_ok,
            "note": rp_note,
        },
    }
    return deployment


@router.post("/request", status_code=201, response_model=dict)
def request_deployment(
    payload: LiveDeploymentRequestCreate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    strategy = db.get(Strategy, payload.strategy_id)
    broker = db.get(BrokerConnection, payload.broker_connection_id)
    if not strategy or strategy.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="strategy not found")
    if not broker or broker.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="broker connection not found")

    deployment = LiveDeploymentRequest(
        workspace_id=ws.id,
        strategy_id=strategy.id,
        broker_connection_id=broker.id,
        risk_acknowledged=payload.risk_acknowledged,
        status="pending_review",
        checks={},
    )
    db.add(deployment)
    db.flush()
    _refresh_checks(deployment, db, ws)
    db.commit()
    db.refresh(deployment)
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="live_deployment_request",
        resource_type="live_deployment_request",
        resource_id=deployment.id,
        payload={"strategy_id": strategy.id, "broker_id": broker.id},
    )
    return {"id": deployment.id, "status": deployment.status, "checks": deployment.checks}


@router.get("", response_model=list[dict])
def list_deployments(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    status: str | None = Query(None),
) -> list[dict]:
    q = db.query(LiveDeploymentRequest).filter(LiveDeploymentRequest.workspace_id == ws.id)
    if status:
        q = q.filter(LiveDeploymentRequest.status == status)
    rows = q.order_by(LiveDeploymentRequest.created_at.desc()).all()
    strategy_names = {s.id: s.name for s in db.query(Strategy).filter(Strategy.workspace_id == ws.id).all()}
    broker_labels = {c.id: c.label for c in db.query(BrokerConnection).filter(BrokerConnection.workspace_id == ws.id).all()}
    return [
        {
            "id": d.id,
            "strategy_id": d.strategy_id,
            "strategy_name": strategy_names.get(d.strategy_id, ""),
            "broker_connection_id": d.broker_connection_id,
            "broker_label": broker_labels.get(d.broker_connection_id, ""),
            "status": d.status,
            "checks": d.checks,
            "risk_acknowledged": d.risk_acknowledged,
            "created_at": d.created_at.isoformat(),
        }
        for d in rows
    ]


@router.get("/{deployment_id}", response_model=dict)
def get_deployment(
    deployment_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    deployment = db.get(LiveDeploymentRequest, deployment_id)
    if not deployment or deployment.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="deployment not found")
    _refresh_checks(deployment, db, ws)
    db.commit()
    strategy = db.get(Strategy, deployment.strategy_id)
    broker = (
        db.get(BrokerConnection, deployment.broker_connection_id)
        if deployment.broker_connection_id
        else None
    )
    return {
        "id": deployment.id,
        "strategy_id": deployment.strategy_id,
        "strategy_name": strategy.name if strategy else "",
        "broker_connection_id": deployment.broker_connection_id,
        "broker_label": broker.label if broker else "",
        "status": deployment.status,
        "checks": deployment.checks,
        "risk_acknowledged": deployment.risk_acknowledged,
        "deployment_config": deployment.deployment_config,
        "created_at": deployment.created_at.isoformat(),
    }


@router.post("/{deployment_id}/approve", response_model=dict)
def approve_deployment(
    deployment_id: str,
    payload: LiveDeploymentApprove,
    user: User = Depends(require_superuser),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    deployment = db.get(LiveDeploymentRequest, deployment_id)
    if not deployment or deployment.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="deployment not found")
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="approval not confirmed")
    if deployment.status not in ("pending_review", "blocked"):
        raise HTTPException(status_code=409, detail=f"cannot approve from status {deployment.status}")
    _refresh_checks(deployment, db, ws)
    unmet = _gate(db, deployment, ws)
    if unmet:
        deployment.status = "blocked"
        db.commit()
        AuditService(db).record(
            workspace_id=ws.id,
            actor_id=user.id,
            action="live_deployment_block",
            resource_type="live_deployment_request",
            resource_id=deployment.id,
            payload={"reasons": unmet},
        )
        return {
            "id": deployment.id,
            "status": deployment.status,
            "approved": False,
            "reasons": unmet,
        }
    # Sandbox-only live enablement. Real broker execution is intentionally
    # gated: practice (sandbox) accounts approve to `approved_sandbox_only`;
    # non-sandbox execution only when the master LIVE_TRADING_ENABLED flag is on.
    from app.core.config import get_settings

    settings = get_settings()
    broker = db.get(BrokerConnection, deployment.broker_connection_id)
    if broker and broker.is_sandbox:
        deployment.status = "approved_sandbox_only"
    elif not settings.LIVE_TRADING_ENABLED:
        deployment.status = "blocked"
        db.commit()
        AuditService(db).record(
            workspace_id=ws.id,
            actor_id=user.id,
            action="live_deployment_block",
            resource_type="live_deployment_request",
            resource_id=deployment.id,
            payload={"reasons": ["live trading is disabled (LIVE_TRADING_ENABLED=false)"]},
        )
        return {
            "id": deployment.id,
            "status": deployment.status,
            "approved": False,
            "reasons": ["live trading is disabled (LIVE_TRADING_ENABLED=false)"],
        }
    else:
        deployment.status = "approved"
    db.commit()
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="live_deployment_approve",
        resource_type="live_deployment_request",
        resource_id=deployment.id,
        payload={"status": deployment.status, "approver": user.id},
    )
    return {"id": deployment.id, "status": deployment.status, "approved": True}


@router.post("/{deployment_id}/reject", response_model=dict)
def reject_deployment(
    deployment_id: str,
    payload: LiveDeploymentReject,
    user: User = Depends(require_superuser),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    deployment = db.get(LiveDeploymentRequest, deployment_id)
    if not deployment or deployment.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="deployment not found")
    if deployment.status not in ("pending_review",):
        raise HTTPException(status_code=409, detail=f"cannot reject from status {deployment.status}")
    deployment.status = "rejected"
    deployment.deployment_config = {**(deployment.deployment_config or {}), "rejection_reason": payload.reason}
    db.commit()
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="live_deployment_reject",
        resource_type="live_deployment_request",
        resource_id=deployment.id,
        payload={"reason": payload.reason, "reviewer": user.id},
    )
    return {"id": deployment.id, "status": deployment.status}


@router.post("/{deployment_id}/disable", response_model=dict)
def disable_deployment(
    deployment_id: str,
    user: User = Depends(require_superuser),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    deployment = db.get(LiveDeploymentRequest, deployment_id)
    if not deployment or deployment.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="deployment not found")
    deployment.status = "disabled"
    db.commit()
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="live_deployment_disable",
        resource_type="live_deployment_request",
        resource_id=deployment.id,
        payload={},
    )
    return {"id": deployment.id, "status": deployment.status}
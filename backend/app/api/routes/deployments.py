from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import BrokerConnection, LiveDeploymentRequest, Strategy, User, Workspace
from app.schemas.broker import LiveDeploymentApprove, LiveDeploymentRequestCreate
from app.services.audit import AuditService

router = APIRouter(prefix="/live-deployments", tags=["live-deployments"])

MIN_PAPER_TRADES = 30


def _gate(deployment: LiveDeploymentRequest) -> list[str]:
    """Return a list of unmet conditions. Empty list means deployable."""
    unmet: list[str] = []
    if not deployment.risk_acknowledged:
        unmet.append("risk acknowledgment required")
    if not deployment.checks or not deployment.checks.get("paper_track_record", {}).get("passed"):
        unmet.append("paper trading track record not satisfied")
    if not deployment.checks or not deployment.checks.get("risk_profile", {}).get("passed"):
        unmet.append("risk configuration incomplete")
    return unmet


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
        checks={
            "paper_track_record": {
                "passed": False,
                "required_min_trades": MIN_PAPER_TRADES,
                "note": "must complete required paper-trading trades before live eligibility",
            },
            "risk_profile": {
                "passed": bool(payload.risk_acknowledged),
                "note": "user must acknowledge risk and configure risk profile",
            },
        },
    )
    db.add(deployment)
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


@router.post("/{deployment_id}/approve", response_model=dict)
def approve_deployment(
    deployment_id: str,
    payload: LiveDeploymentApprove,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    deployment = db.get(LiveDeploymentRequest, deployment_id)
    if not deployment or deployment.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="deployment not found")
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="approval not confirmed")
    unmet = _gate(deployment)
    if unmet:
        deployment.status = "blocked"
        db.commit()
        return {
            "id": deployment.id,
            "status": deployment.status,
            "approved": False,
            "reasons": unmet,
        }
    # Sandbox-only live enablement. Real broker execution is intentionally
    # unsupported until a non-sandbox adapter is configured.
    broker = db.get(BrokerConnection, deployment.broker_connection_id)
    if broker and broker.is_sandbox:
        deployment.status = "approved_sandbox_only"
    else:
        deployment.status = "approved"
    db.commit()
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="live_deployment_approve",
        resource_type="live_deployment_request",
        resource_id=deployment.id,
        payload={"status": deployment.status},
    )
    return {"id": deployment.id, "status": deployment.status, "approved": True}


@router.post("/{deployment_id}/disable", response_model=dict)
def disable_deployment(
    deployment_id: str,
    user: User = Depends(get_current_user),
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

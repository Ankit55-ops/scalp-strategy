from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import RiskEvent, RiskProfile, User, Workspace
from app.risk.killswitch import KillSwitchRegistry
from app.schemas.risk import KillSwitchRequest, RiskProfileCreate
from app.services.audit import AuditService

router = APIRouter(prefix="/risk", tags=["risk"])


@router.post("/kill-switch", response_model=dict)
def set_kill_switch(
    payload: KillSwitchRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    ks = KillSwitchRegistry()
    if payload.scope == "global":
        ks.set_global(payload.enabled)
    elif payload.scope == "strategy":
        ks.set_strategy(payload.resource_id, payload.enabled)
    elif payload.scope == "pair":
        ks.set_pair(payload.resource_id, payload.enabled)
    else:
        raise HTTPException(status_code=400, detail="invalid scope")
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="kill_switch",
        resource_type=payload.scope,
        resource_id=payload.resource_id,
        payload={"enabled": payload.enabled, "reason": payload.reason},
    )
    return {"scope": payload.scope, "resource_id": payload.resource_id, "enabled": payload.enabled}


@router.get("/kill-switch", response_model=dict)
def kill_switch_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return KillSwitchRegistry().status()


@router.get("/events", response_model=list[dict])
def list_risk_events(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = 100,
) -> list[dict]:
    events = (
        db.query(RiskEvent)
        .filter(RiskEvent.workspace_id == ws.id)
        .order_by(RiskEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "severity": e.severity,
            "symbol": e.symbol,
            "details": e.details,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


@router.post("/profiles", status_code=201, response_model=dict)
def create_risk_profile(
    payload: RiskProfileCreate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    profile = RiskProfile(
        workspace_id=ws.id,
        **payload.model_dump(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return {"id": profile.id, "name": profile.name}


@router.get("/profiles", response_model=list[dict])
def list_risk_profiles(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.query(RiskProfile).filter(RiskProfile.workspace_id == ws.id).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "risk_per_trade_pct": p.risk_per_trade_pct,
            "max_daily_loss_pct": p.max_daily_loss_pct,
            "max_open_positions": p.max_open_positions,
            "is_active": p.is_active,
        }
        for p in rows
    ]

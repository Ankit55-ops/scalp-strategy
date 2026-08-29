from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import KillSwitch, RiskEvent, RiskProfile, User, Workspace
from app.risk.killswitch import KillSwitchRegistry
from app.schemas.risk import (
    KillSwitchRequest,
    RiskProfileCreate,
    RiskProfileOut,
    RiskProfileUpdate,
)
from app.services.audit import AuditService

router = APIRouter(prefix="/risk", tags=["risk"])


@router.post("/kill-switch", response_model=dict)
def set_kill_switch(
    payload: KillSwitchRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    ks = KillSwitchRegistry(db=db, workspace_id=ws.id)
    if payload.scope == "global":
        ks.set_global(payload.enabled, reason=payload.reason)
    elif payload.scope == "strategy":
        ks.set_strategy(payload.resource_id, payload.enabled, reason=payload.reason)
    elif payload.scope == "pair":
        ks.set_pair(payload.resource_id, payload.enabled, reason=payload.reason)
    else:
        raise HTTPException(status_code=400, detail="invalid scope")
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="kill_switch",
        resource_type=payload.scope,
        resource_id=payload.resource_id or "global",
        payload={"enabled": payload.enabled, "reason": payload.reason},
    )
    return {"scope": payload.scope, "resource_id": payload.resource_id, "enabled": payload.enabled}


@router.get("/kill-switch", response_model=dict)
def kill_switch_status(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    return KillSwitchRegistry(db=db, workspace_id=ws.id).status()


@router.get("/kill-switch/engagements", response_model=list[dict])
def list_kill_switch_engagements(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(KillSwitch)
        .filter(KillSwitch.workspace_id == ws.id, KillSwitch.enabled.is_(True))
        .order_by(KillSwitch.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "scope": r.scope,
            "resource_id": r.resource_id,
            "reason": r.reason,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


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


def _profile_out(p: RiskProfile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "risk_per_trade_pct": p.risk_per_trade_pct,
        "max_daily_loss_pct": p.max_daily_loss_pct,
        "max_weekly_loss_pct": p.max_weekly_loss_pct,
        "max_drawdown_pct": p.max_drawdown_pct,
        "max_consecutive_losses": p.max_consecutive_losses,
        "max_open_positions": p.max_open_positions,
        "max_trades_per_day": p.max_trades_per_day,
        "max_correlated_exposure_pct": p.max_correlated_exposure_pct,
        "max_spread_pips": p.max_spread_pips,
        "max_slippage_pips": p.max_slippage_pips,
        "news_blackout_minutes_before": p.news_blackout_minutes_before,
        "news_blackout_minutes_after": p.news_blackout_minutes_after,
        "correlated_currency_groups": p.correlated_currency_groups,
        "hard_stop_distance_pips": p.hard_stop_distance_pips,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat(),
    }


def _get_owned_profile(db: Session, ws: Workspace, profile_id: str) -> RiskProfile:
    profile = db.get(RiskProfile, profile_id)
    if not profile or profile.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="risk profile not found")
    return profile


@router.post("/profiles", status_code=201, response_model=RiskProfileOut)
def create_risk_profile(
    payload: RiskProfileCreate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    profile = RiskProfile(
        workspace_id=ws.id,
        **payload.model_dump(exclude_none=True),
    )
    db.add(profile)
    if payload.is_active:
        db.query(RiskProfile).filter(
            RiskProfile.workspace_id == ws.id, RiskProfile.is_active.is_(True)
        ).update({"is_active": False})
    db.commit()
    db.refresh(profile)
    return _profile_out(profile)


@router.get("/profiles", response_model=list[dict])
def list_risk_profiles(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(RiskProfile)
        .filter(RiskProfile.workspace_id == ws.id)
        .order_by(RiskProfile.is_active.desc(), RiskProfile.created_at.desc())
        .all()
    )
    return [_profile_out(p) for p in rows]


@router.patch("/profiles/{profile_id}", response_model=RiskProfileOut)
def update_risk_profile(
    profile_id: str,
    payload: RiskProfileUpdate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    profile = _get_owned_profile(db, ws, profile_id)
    for key, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(profile, key, value)
    if payload.is_active is True:
        db.query(RiskProfile).filter(
            RiskProfile.workspace_id == ws.id,
            RiskProfile.is_active.is_(True),
            RiskProfile.id != profile.id,
        ).update({"is_active": False})
    db.commit()
    db.refresh(profile)
    return _profile_out(profile)


@router.post("/profiles/{profile_id}/activate", response_model=RiskProfileOut)
def activate_risk_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    profile = _get_owned_profile(db, ws, profile_id)
    db.query(RiskProfile).filter(
        RiskProfile.workspace_id == ws.id,
        RiskProfile.is_active.is_(True),
        RiskProfile.id != profile.id,
    ).update({"is_active": False})
    profile.is_active = True
    db.commit()
    db.refresh(profile)
    return _profile_out(profile)


@router.get("/overview", response_model=dict)
def risk_overview(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    """Live risk dashboard: paper-exposure + kill-switch + profile limits."""
    from app.services.paper_service import PaperTradingService

    paper = PaperTradingService(db).status(ws.id)
    profile = (
        db.query(RiskProfile)
        .filter(RiskProfile.workspace_id == ws.id, RiskProfile.is_active.is_(True))
        .first()
    )
    ks = KillSwitchRegistry(db=db, workspace_id=ws.id).status()
    recent_events = (
        db.query(RiskEvent)
        .filter(RiskEvent.workspace_id == ws.id)
        .order_by(RiskEvent.created_at.desc())
        .count()
    )
    open_count = int(paper.get("open_positions", 0)) or 0
    equity = float(paper.get("equity", 0.0)) or 0.0
    per_trade_budget = 0.0
    if profile and open_count:
        per_trade_budget = equity * (profile.max_daily_loss_pct / 100.0) / open_count
    return {
        "account": {
            "is_active": paper.get("is_active"),
            "balance": paper.get("balance"),
            "equity": equity,
            "open_positions": open_count,
            "closed_trades": paper.get("closed_trades"),
            "trading_state": paper.get("trading_state"),
            "state_reason": paper.get("state_reason"),
            "pending_orders": paper.get("pending_orders"),
        },
        "kill_switch": ks.get("global") or ks,
        "profile": {
            "configured": profile is not None,
            "max_daily_loss_pct": profile.max_daily_loss_pct if profile else None,
            "max_weekly_loss_pct": profile.max_weekly_loss_pct if profile else None,
            "max_drawdown_pct": profile.max_drawdown_pct if profile else None,
            "max_consecutive_losses": int(profile.max_consecutive_losses) if profile else None,
        },
        "budget": {
            "per_trade_budget": round(per_trade_budget, 2),
            "notes": (
                []
                if profile
                else ["no active risk profile — define one to enable risk-based gating"]
            ),
        },
        "events": {"total": recent_events},
    }


@router.delete("/profiles/{profile_id}", response_model=dict)
def delete_risk_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    profile = _get_owned_profile(db, ws, profile_id)
    db.delete(profile)
    db.commit()
    return {"id": profile_id, "deleted": True}
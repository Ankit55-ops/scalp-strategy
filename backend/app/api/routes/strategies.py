from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.architect import generate
from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import Strategy, StrategyVersion, User, Workspace
from app.schemas.api_strategy import (
    StrategyCreate,
    StrategyGenerateRequest,
    StrategyGenerateResponse,
    StrategyGenerateResponseItem,
    StrategyUpdate,
    StrategyVersionCreate,
)
from app.schemas.strategy import StrategySpec
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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import Alert, User, Workspace

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[dict])
def list_alerts(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    q = db.query(Alert).filter(Alert.workspace_id == ws.id)
    if unread_only:
        q = q.filter(Alert.is_read.is_(False))
    rows = q.order_by(Alert.created_at.desc()).limit(limit).all()
    return [
        {
            "id": a.id,
            "level": a.level,
            "title": a.title,
            "message": a.message,
            "is_read": a.is_read,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]


@router.get("/unread-count", response_model=dict)
def unread_count(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    count = (
        db.query(Alert)
        .filter(Alert.workspace_id == ws.id, Alert.is_read.is_(False))
        .count()
    )
    return {"count": count}


@router.post("/{alert_id}/read", response_model=dict)
def mark_read(
    alert_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    alert = db.get(Alert, alert_id)
    if not alert or alert.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.is_read = True
    db.commit()
    return {"id": alert.id, "is_read": True}


@router.post("/mark-all-read", response_model=dict)
def mark_all_read(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    updated = (
        db.query(Alert)
        .filter(Alert.workspace_id == ws.id, Alert.is_read.is_(False))
        .update({Alert.is_read: True})
    )
    db.commit()
    return {"marked": updated}
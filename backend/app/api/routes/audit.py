from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import AuditLog, User, Workspace

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=dict)
def audit_logs(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    q = db.query(AuditLog).filter(AuditLog.workspace_id == ws.id)
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "actor_id": r.actor_id,
                "payload": r.payload,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import AuditLog, User, Workspace

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=list[dict])
def audit_logs(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = 200,
) -> list[dict]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.workspace_id == ws.id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
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
    ]

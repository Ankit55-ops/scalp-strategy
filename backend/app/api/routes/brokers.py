from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.core.security import Cipher
from app.db.session import get_db
from app.models import BrokerConnection, User, Workspace
from app.schemas.broker import BrokerConnect, BrokerOut
from app.services.audit import AuditService

router = APIRouter(prefix="/brokers", tags=["brokers"])


@router.post("/connect", response_model=BrokerOut)
def connect_broker(
    payload: BrokerConnect,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> BrokerOut:
    cipher = Cipher()
    conn = BrokerConnection(
        workspace_id=ws.id,
        provider=payload.provider,
        label=payload.label,
        is_sandbox=payload.sandbox,
        status="connected",
    )
    # Encrypt secrets at rest; never store plaintext.
    if payload.api_key:
        conn.encrypted_api_key = cipher.encrypt(payload.api_key)
    if payload.api_secret:
        conn.encrypted_api_secret = cipher.encrypt(payload.api_secret)
    db.add(conn)
    db.commit()
    db.refresh(conn)
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="broker_connect",
        resource_type="broker_connection",
        resource_id=conn.id,
        payload={"provider": conn.provider, "is_sandbox": conn.is_sandbox},
    )
    return BrokerOut(
        id=conn.id,
        provider=conn.provider,
        label=conn.label,
        status=conn.status,
        is_sandbox=conn.is_sandbox,
    )


@router.get("", response_model=list[BrokerOut])
def list_brokers(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[BrokerOut]:
    rows = db.query(BrokerConnection).filter(BrokerConnection.workspace_id == ws.id).all()
    return [
        BrokerOut(
            id=c.id,
            provider=c.provider,
            label=c.label,
            status=c.status,
            is_sandbox=c.is_sandbox,
        )
        for c in rows
    ]

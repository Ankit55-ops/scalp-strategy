from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.core.security import Cipher
from app.db.session import get_db
from app.models import BrokerConnection, User, Workspace
from app.providers.factory import get_broker_provider
from app.schemas.broker import (
    BrokerConnect,
    BrokerConnectTest,
    BrokerOut,
    BrokerUpdate,
)
from app.services.audit import AuditService

router = APIRouter(prefix="/brokers", tags=["brokers"])


@router.post("/connect", response_model=BrokerOut)
def connect_broker(
    payload: BrokerConnect,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> BrokerOut:
    from app.core.config import get_settings

    settings = get_settings()
    if payload.provider == "oanda_practice" and not payload.sandbox:
        if not settings.LIVE_TRADING_ENABLED:
            raise HTTPException(
                status_code=400,
                detail="live OANDA connections are disabled (LIVE_TRADING_ENABLED=false); use a practice (sandbox) connection",
            )
        if not user.is_superuser:
            raise HTTPException(status_code=403, detail="live OANDA connections require superuser approval")
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


def _get_owned_broker(db: Session, broker_id: str, ws: Workspace) -> BrokerConnection:
    conn = db.get(BrokerConnection, broker_id)
    if not conn or conn.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="broker connection not found")
    return conn


@router.get("/{broker_id}", response_model=dict)
def get_broker(
    broker_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    conn = _get_owned_broker(db, broker_id, ws)
    symbols: list[str] = []
    try:
        provider = get_broker_provider(conn.provider)
        symbols = provider.list_symbols()
    except ValueError:
        symbols = []
    return {
        "id": conn.id,
        "provider": conn.provider,
        "label": conn.label,
        "status": conn.status,
        "is_sandbox": conn.is_sandbox,
        "symbols": symbols,
        "created_at": conn.created_at.isoformat(),
    }


@router.patch("/{broker_id}", response_model=BrokerOut)
def update_broker(
    broker_id: str,
    payload: BrokerUpdate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> BrokerOut:
    conn = _get_owned_broker(db, broker_id, ws)
    if payload.label is not None:
        conn.label = payload.label
    if payload.status is not None:
        conn.status = payload.status
    db.commit()
    db.refresh(conn)
    return BrokerOut(
        id=conn.id,
        provider=conn.provider,
        label=conn.label,
        status=conn.status,
        is_sandbox=conn.is_sandbox,
    )


@router.delete("/{broker_id}", response_model=dict)
def delete_broker(
    broker_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    conn = _get_owned_broker(db, broker_id, ws)
    db.delete(conn)
    db.commit()
    AuditService(db).record(
        workspace_id=ws.id,
        actor_id=user.id,
        action="broker_disconnect",
        resource_type="broker_connection",
        resource_id=broker_id,
        payload={"provider": conn.provider},
    )
    return {"id": broker_id, "deleted": True}


@router.post("/{broker_id}/test", response_model=dict)
def test_broker(
    broker_id: str,
    payload: BrokerConnectTest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    conn = _get_owned_broker(db, broker_id, ws)
    try:
        provider = get_broker_provider(conn.provider)
        ok = provider.authenticate({"api_key": payload.api_key or "", "sandbox": conn.is_sandbox})
        symbols = provider.list_symbols()
        return {
            "ok": ok,
            "provider": conn.provider,
            "symbols": symbols,
            "message": "connection successful" if ok else "authentication failed",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": conn.provider, "symbols": [], "message": str(exc)[:300]}
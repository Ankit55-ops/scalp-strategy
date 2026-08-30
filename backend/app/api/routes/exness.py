"""Exness via MetaTrader 5 — secure, read-only provider connection endpoints.

Every endpoint here is authenticated and workspace-scoped. No endpoint ever
returns credentials, tokens, encrypted blobs, or passwords. Credential-mutating
actions require a recent authentication (``require_recent_auth``) and are
audit-logged with redacted detail. Live order placement is not part of this
surface; ``LIVE_TRADING_ENABLED`` stays ``false`` and the report always says
``disabled``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace, require_recent_auth
from app.core.config import get_settings
from app.db.session import get_db
from app.models import User, Workspace
from app.schemas.exness import (
    CapabilityReportOut,
    ExnessConnectRequest,
    ExnessPairGatewayRequest,
    ExnessTestConnectionRequest,
    ProviderConnectionStatusCardOut,
)
from app.services.exness_provider_service import (
    ExnessConnectionError,
    connect_exness,
    connection_status_card,
    get_capabilities,
    health_check,
    issue_pairing_token,
    list_connection_instruments,
    recent_connect_attempt_count,
    test_gateway_connection,
    test_server_side_connection,
    validate_pairing_token,
)

router = APIRouter(prefix="/providers/exness-mt5", tags=["providers-exness"])


def _enforce_attempt_budget(db: Session, workspace_id: str) -> None:
    settings = get_settings()
    count = recent_connect_attempt_count(db, workspace_id)
    if count >= settings.PROVIDER_CONNECT_MAX_ATTEMPTS_PER_WINDOW:
        raise HTTPException(
            status_code=429,
            detail=f"Too many provider connection attempts. Retry in {settings.PROVIDER_CONNECT_WINDOW_SECONDS}s.",
        )


def _utc_iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


@router.get("/status", response_model=ProviderConnectionStatusCardOut)
def status_card(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    return connection_status_card(db, ws.id)


@router.post("/test-connection", response_model=CapabilityReportOut)
def test_connection(
    payload: ExnessTestConnectionRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> CapabilityReportOut:
    _enforce_attempt_budget(db, ws.id)
    if payload.mode == "gateway":
        if not (payload.gateway_url and payload.pairing_code):
            raise HTTPException(status_code=422, detail="gateway test requires gateway_url + pairing_code")
        return test_gateway_connection(db, ws.id, str(user.id), payload=ExnessTestConnectionRequest(
            mode="gateway", gateway_url=payload.gateway_url, pairing_code=payload.pairing_code,
            device_name=payload.device_name or "test-device", environment=payload.environment,
            idempotency_key=payload.idempotency_key))
    if payload.mode == "server_side":
        if not (payload.login and payload.password and payload.server):
            raise HTTPException(status_code=422, detail="server-side test requires login + password + server")
        if not (payload.login or "").strip().isdigit():
            raise HTTPException(status_code=422, detail="MT5 login must be numeric")
        return test_server_side_connection(db, ws.id, str(user.id), payload=ExnessTestConnectionRequest(
            mode="server_side", login=payload.login, password=payload.password,
            server=payload.server, environment=payload.environment,
            idempotency_key=payload.idempotency_key))
    raise HTTPException(status_code=422, detail="unknown mode")


@router.post("/connect", response_model=dict)
def connect(
    payload: ExnessConnectRequest,
    user: User = Depends(require_recent_auth),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    _enforce_attempt_budget(db, ws.id)
    try:
        conn = connect_exness(db, ws.id, str(user.id), payload,
                              correlation_id=payload.idempotency_key)
    except ExnessConnectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    caps = get_capabilities(db, ws.id, conn.id)
    return {
        "connection": {
            "id": conn.id,
            "provider": conn.provider,
            "display_name": conn.display_name,
            "connection_mode": conn.connection_mode,
            "environment": conn.environment,
            "status": conn.status,
            "health_status": conn.health_status,
            "last_connected_at": _utc_iso(conn.last_connected_at),
            "last_successful_data_at": _utc_iso(conn.last_successful_data_at),
            "last_error_message_safe": conn.last_error_message_safe,
        },
        "capabilities": caps["capabilities"],
        "live_trading_status": "disabled",
    }


@router.post("/pair-gateway")
def pair_gateway(
    payload: ExnessPairGatewayRequest,
    user: User = Depends(require_recent_auth),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    _enforce_attempt_budget(db, ws.id)
    return issue_pairing_token(db, ws.id, str(user.id), payload)


@router.post("/gateway/verify")
def gateway_verify(
    gateway_id: str = Query(min_length=1, max_length=36),
    pairing_token: str = Query(min_length=8, max_length=256),
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return validate_pairing_token(db, ws.id, gateway_id, pairing_token)
    except ExnessConnectionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/capabilities")
def capabilities(
    connection_id: str = Query(min_length=1, max_length=36),
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return get_capabilities(db, ws.id, connection_id)
    except ExnessConnectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/health")
def health(
    connection_id: str = Query(min_length=1, max_length=36),
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return health_check(db, ws.id, connection_id)
    except ExnessConnectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/instruments")
def instruments(
    connection_id: str = Query(min_length=1, max_length=36),
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        return list_connection_instruments(db, ws.id, connection_id)
    except ExnessConnectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/disconnect")
def disconnect(
    connection_id: str = Query(min_length=1, max_length=36),
    user: User = Depends(require_recent_auth),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.exness_provider_service import (
        ExnessConnectionError,
        disconnect_exness,
    )

    try:
        return disconnect_exness(db, ws.id, connection_id, str(user.id))
    except ExnessConnectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
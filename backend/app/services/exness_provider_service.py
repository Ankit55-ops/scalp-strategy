"""Server-side Exness / MT5 provider connection lifecycle.

Encapsulates the secure, read-only connection workflow:

- Credentials are encrypted at rest (Fernet) and never returned to clients.
- Capability discovery is dynamic and honest: an Exness connection is only shown
  as ``CONNECTED`` after a server-side verification returns healthy.
- MT5 Gateway Agent pairing issues short-lived, encrypted pairing tokens.
- Every create / test / update / disconnect / pair is audit-logged with secrets
  redacted.

In development/tests (``EXNESS_MOCK_ADAPTER=true``) a clearly-labelled mock
adapter is used and the report notes it. Any real deployment must set that flag
false and provide an approved server-side connector / gateway agent; the
resolver then refuses to claim connectivity it cannot verify.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from app.core.redact import redact_text, sanitize_error
from app.core.secrets import decrypt_secret, encrypt_secret
from app.models import (
    MT5GatewayAgent,
    MT5GatewayPairingEvent,
    ProviderConnection,
    ProviderConnectionAuditLog,
    ProviderConnectionCapability,
    ProviderConnectionHealthEvent,
    ProviderInstrumentMapping,
)
from app.providers.exness_mt5 import (
    CONNECTION_MODE_MT5_GATEWAY,
    CONNECTION_MODE_SERVER_SIDE,
    PROVIDER_TYPE,
    build_capability_report,
    build_mock_adapter,
)
from app.schemas.exness import (
    CapabilityReportOut,
    ExnessConnectRequest,
    ExnessPairGatewayRequest,
    ExnessTestGatewayRequest,
    ExnessTestServerSideRequest,
    ProviderConnectionOut,
)

PAIRING_TOKEN_TTL_SECONDS = 600


class ExnessConnectionError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _connection_out(conn: ProviderConnection) -> ProviderConnectionOut:
    return ProviderConnectionOut(
        id=conn.id,
        provider=conn.provider,
        display_name=conn.display_name,
        connection_mode=conn.connection_mode,
        environment=conn.environment,
        status=conn.status,
        health_status=conn.health_status,
        last_connected_at=conn.last_connected_at,
        last_successful_data_at=conn.last_successful_data_at,
        last_error_code=conn.last_error_code,
        last_error_message_safe=conn.last_error_message_safe,
    )


def _write_audit(db, ws_id, conn_id, action, status, actor, detail="", correlation_id=None):
    db.add(ProviderConnectionAuditLog(
        workspace_id=ws_id,
        connection_id=conn_id,
        actor_user_id=actor,
        action=action,
        status=status,
        detail_safe=redact_text(detail)[:1024] if detail else None,
        correlation_id=correlation_id or uuid.uuid4().hex,
    ))


# Friendly, user-facing explanations per provider connection status. The UI also
# composes generic guidance; these strings never include raw errors or secrets.
STATUS_MESSAGES = {
    "NOT_CONFIGURED": "No market-data provider is connected. Connect Exness MT5 or another supported provider to load real historical data.",
    "CONFIGURED": "Provider connection is saved but has not passed a server-side health check yet.",
    "CONNECTING": "Connecting to the provider server side...",
    "CONNECTED": "Provider is connected and healthy. Historical data and quotes are available.",
    "DEGRADED": "Provider is connected but degraded. Some data or permissions may be limited.",
    "STALE": "Real-time feed is stale. Strategy checking and paper-order creation are paused.",
    "DISCONNECTED": "Exness connection is configured but the MT5 gateway is offline.",
    "AUTHENTICATION_FAILED": "Provider rejected the credentials. Re-test the connection before use.",
    "PERMISSION_DENIED": "The account does not grant permission for this capability.",
    "UNSUPPORTED_ACCOUNT": "This account type or region is not supported by the configured connection method. Consult Exness for official API availability.",
    "RATE_LIMITED": "Provider rate limit reached. Retry later.",
    "MAINTENANCE": "Provider is in maintenance. Data may be delayed or unavailable.",
    "ERROR": "Provider connection could not be established safely. Review the connection settings.",
}


def _live_trading_note() -> str:
    return ("Live trading is intentionally disabled. Your connected account may be used only "
            "for data access, practice-mode validation, or account metadata depending on "
            "configured permissions.")


def connection_status_card(db, workspace_id: str) -> dict:
    """Build the safe, frontend-ready Provider Connection Status Card payload."""

    conn = (db.query(ProviderConnection)
            .filter(ProviderConnection.workspace_id == workspace_id,
                    ProviderConnection.provider == PROVIDER_TYPE).first())

    card = {
        "connection_status": "NOT_CONFIGURED",
        "selected_provider": "Exness via MetaTrader 5",
        "display_name": None,
        "connection_mode": None,
        "environment": None,
        "account_type": "not_connected",
        "provider_server": None,
        "capabilities": [],
        "available_capabilities": [],
        "unavailable_capabilities": [],
        "last_successful_data_utc": None,
        "latency_ms": None,
        "feed_health": None,
        "active_symbol_count": 0,
        "active_symbols": [],
        "instrument_count": 0,
        "message": "No market-data provider is connected. Connect Exness MT5 or another supported provider to load real historical data.",
        "show_connect_button": False,
        "live_trading_status": "disabled",
    }

    env_map = {"demo": "demo", "real": "real"}
    if conn is None:
        card["account_type"] = "not_connected"
        card["selected_provider"] = "No provider selected"
        card["show_connect_button"] = True
        return card

    card["connection_status"] = conn.status or "NOT_CONFIGURED"
    card["display_name"] = conn.display_name
    card["connection_mode"] = conn.connection_mode
    card["environment"] = conn.environment
    card["account_type"] = env_map.get(conn.environment or "", "unknown")
    card["provider_server"] = None
    card["latency_ms"] = conn.latency_ms
    card["feed_health"] = conn.health_status
    card["last_successful_data_utc"] = (
        datetime.fromtimestamp(conn.last_successful_data_at, tz=timezone.utc).isoformat()
        if conn.last_successful_data_at else None
    )
    card["show_connect_button"] = conn.status in (
        "NOT_CONFIGURED", "DISCONNECTED", "AUTHENTICATION_FAILED", "ERROR",
    )

    caps = (db.query(ProviderConnectionCapability)
            .filter(ProviderConnectionCapability.connection_id == conn.id))
    available = [c.capability for c in caps if c.availability == "available"]
    unavailable = [c.capability for c in caps if c.availability != "available"]
    card["capabilities"] = available + unavailable
    card["available_capabilities"] = available
    card["unavailable_capabilities"] = unavailable

    symbols = (db.query(ProviderInstrumentMapping)
               .filter(ProviderInstrumentMapping.connection_id == conn.id,
                       ProviderInstrumentMapping.is_supported.is_(True)).all())
    card["active_symbols"] = [s.canonical_symbol for s in symbols][:200]
    card["active_symbol_count"] = len(card["active_symbols"])

    # Compose the specific friendly message for this state.
    base = STATUS_MESSAGES.get(card["connection_status"], STATUS_MESSAGES["ERROR"])
    if card["connection_status"] == "STALE":
        base = "Real-time feed is stale. Strategy checking and paper-order creation are paused."
    elif card["connection_status"] == "CONNECTED":
        hist = "Historical data is available; " if "historical_candles" in available else ""
        quotes = "live quotes are available." if ("realtime_quotes" in available or "bid_ask_quotes" in available) else "live quotes are unavailable."
        base = f"{hist}{quotes}"
    elif card["connection_status"] == "DISCONNECTED":
        base = "Exness connection is configured but the MT5 gateway is offline."
    card["message"] = base if conn.status != "NOT_CONFIGURED" else (
        "No market-data provider is connected. Connect Exness MT5 or another supported provider to load real historical data."
    )
    return card


def recent_connect_attempt_count(db, workspace_id: str) -> int:
    """Count recent test/connect/pair audit events for the workspace (rate limit)."""
    from app.core.config import get_settings

    window = get_settings().PROVIDER_CONNECT_WINDOW_SECONDS
    since = _now() - window
    return int(db.query(ProviderConnectionAuditLog)
               .filter(ProviderConnectionAuditLog.workspace_id == workspace_id,
                       ProviderConnectionAuditLog.action.in_(("test", "create", "pair")),
                       ProviderConnectionAuditLog.created_at >= datetime.fromtimestamp(since, tz=timezone.utc))
               .count())


def _resolve_adapter(conn):
    from app.core.config import get_settings
    if get_settings().EXNESS_MOCK_ADAPTER:
        return build_mock_adapter(account_environment=conn.environment or "demo", bid_ask=True, seed=1)
    raise ExnessConnectionError(
        "UNSUPPORTED_ACCOUNT: no approved server-side MT5 connector or reachable "
        "MT5 Gateway Agent is configured for this connection."
    )

def _is_gateway(mode):
    return mode == CONNECTION_MODE_MT5_GATEWAY

def test_gateway_connection(db, workspace_id, user_id, payload: ExnessTestGatewayRequest) -> CapabilityReportOut:
    correlation_id = uuid.uuid4().hex
    _write_audit(db, workspace_id, None, "test", "started", user_id, "gateway test", correlation_id)
    db.commit()
    try:
        adapter = build_mock_adapter(seed=2)
        report = build_capability_report(adapter)
        _write_audit(db, workspace_id, None, "test", "completed", user_id,
                     f"gateway test: {report.connection_status}", correlation_id)
        db.commit()
        return CapabilityReportOut(**report.__dict__)
    except Exception as exc:  # noqa: BLE001 - graceful degrade to safe status
        db.rollback()
        _write_audit(db, workspace_id, None, "test", "failed", user_id, sanitize_error(exc), correlation_id)
        db.commit()
        return CapabilityReportOut(connection_status="ERROR", live_trading_status="disabled",
                                   detail=sanitize_error(exc))


def test_server_side_connection(db, workspace_id, user_id, payload: ExnessTestServerSideRequest) -> CapabilityReportOut:
    correlation_id = uuid.uuid4().hex
    _write_audit(db, workspace_id, None, "test", "started", user_id, "server-side connector test", correlation_id)
    db.commit()
    try:
        adapter = build_mock_adapter(account_environment=payload.environment, seed=2)
        report = build_capability_report(adapter)
        _write_audit(db, workspace_id, None, "test", "completed", user_id,
                     f"server-side test: {report.connection_status}", correlation_id)
        db.commit()
        return CapabilityReportOut(**report.__dict__)
    except Exception as exc:  # noqa: BLE001 - graceful degrade to safe status
        db.rollback()
        _write_audit(db, workspace_id, None, "test", "failed", user_id, sanitize_error(exc), correlation_id)
        db.commit()
        return CapabilityReportOut(connection_status="ERROR", live_trading_status="disabled",
                                   detail=sanitize_error(exc))


def connect_exness(db, workspace_id, user_id, payload: ExnessConnectRequest, correlation_id=None) -> ProviderConnectionOut:
    if payload.connection_mode.value == CONNECTION_MODE_SERVER_SIDE:
        if not (payload.login and payload.password and payload.server):
            raise ExnessConnectionError("server-side connector requires login + password + server")
        if not payload.confirm_read_only:
            raise ExnessConnectionError("confirm read-only usage before storing credentials")
    if payload.connection_mode.value == CONNECTION_MODE_MT5_GATEWAY and not (
        payload.gateway_url and payload.pairing_code
    ):
        raise ExnessConnectionError("gateway mode requires gateway URL + pairing code")

    correlation_id = correlation_id or uuid.uuid4().hex
    conn = (db.query(ProviderConnection)
            .filter(ProviderConnection.workspace_id == workspace_id,
                    ProviderConnection.provider == PROVIDER_TYPE).first())
    if conn is None:
        conn = ProviderConnection(workspace_id=workspace_id, provider=PROVIDER_TYPE)
        db.add(conn)

    import json as _json
    cred = {"mode": payload.connection_mode.value, "environment": payload.environment}
    meta = {"account_label": payload.account_label or payload.display_name,
            "read_only_capabilities": payload.read_only_capabilities}
    if payload.connection_mode.value == CONNECTION_MODE_SERVER_SIDE:
        cred.update({"login": payload.login, "server": payload.server,
                     "password": payload.password, "use_read_only": payload.use_read_only})
    else:
        cred.update({"gateway_url": payload.gateway_url, "pairing_code": payload.pairing_code,
                     "device_name": payload.device_name})
    conn.encrypted_credentials = encrypt_secret(_json.dumps(cred))
    conn.encrypted_connection_metadata = encrypt_secret(_json.dumps(meta))
    conn.display_name = payload.display_name
    conn.connection_mode = payload.connection_mode.value
    conn.environment = payload.environment
    conn.user_id = user_id
    conn.status = "CONNECTING"
    conn.health_status = "connecting"
    conn.last_error_code = None
    conn.last_error_message_safe = None
    db.flush()
    _write_audit(db, workspace_id, conn.id, "create", "started", user_id, "begin connect", correlation_id)
    db.commit()

    try:
        adapter = _resolve_adapter(conn)
        report = build_capability_report(adapter, account_label=payload.account_label)
        conn.status = report.connection_status
        conn.health_status = "healthy" if report.connection_status == "CONNECTED" else "unhealthy"
        conn.last_connected_at = _now()
        if report.connection_status == "CONNECTED":
            conn.last_successful_data_at = _now()
        for capability, availability in report.capabilities.items():
            db.add(ProviderConnectionCapability(connection_id=conn.id, capability=capability,
                                                availability=availability, verified_at=_now()))
        db.flush()
        _sync_instrument_mappings(db, workspace_id, conn, adapter)
        _write_audit(db, workspace_id, conn.id, "test", "completed", user_id,
                     f"connect: {report.connection_status}", correlation_id)
        db.commit()
        return _connection_out(conn)
    except Exception as exc:  # noqa: BLE001 - graceful degrade to safe status
        db.rollback()
        msg = sanitize_error(exc)
        conn.status = "ERROR"
        conn.last_error_code = "ERROR"
        conn.last_error_message_safe = msg
        _write_audit(db, workspace_id, conn.id, "create", "failed", user_id, msg, correlation_id)
        db.commit()
        return _connection_out(conn)

def get_connection(db, workspace_id, connection_id):
    conn = db.get(ProviderConnection, connection_id)
    if not conn or conn.workspace_id != workspace_id or conn.provider != PROVIDER_TYPE:
        raise ExnessConnectionError("connection not found")
    return conn


def health_check(db, workspace_id, connection_id):
    conn = get_connection(db, workspace_id, connection_id)
    try:
        adapter = _resolve_adapter(conn)
        report = build_capability_report(adapter)
        conn.health_status = "healthy" if report.connection_status == "CONNECTED" else "unhealthy"
        conn.status = report.connection_status
        db.add(ProviderConnectionHealthEvent(connection_id=conn.id, status=report.connection_status,
                                             latency_ms=report.latency_ms, checked_at=_now()))
        db.commit()
        return {"connection_id": conn.id, "status": report.connection_status,
                "health_status": conn.health_status, "latency_ms": report.latency_ms,
                "checked_at_utc": datetime.fromtimestamp(_now(), tz=timezone.utc).isoformat()}
    except Exception as exc:  # noqa: BLE001 - graceful degrade to safe status
        db.rollback()
        return {"connection_id": conn.id, "status": "ERROR", "health_status": "unhealthy",
                "error_safe": sanitize_error(exc)}


def list_connection_instruments(db, workspace_id, connection_id):
    conn = get_connection(db, workspace_id, connection_id)
    adapter = _resolve_adapter(conn)
    return _sync_instrument_mappings(db, workspace_id, conn, adapter)


def _sync_instrument_mappings(db, workspace_id, conn, adapter):
    """Discover provider instruments and upsert canonical symbol mappings.

    Called on connect (immediate, dynamic discovery) and when the instruments
    endpoint is queried (refresh). Mappings are the single source of truth for
    the status card's active symbols and the validator's symbol-mapping check.
    """
    symbols = adapter.list_instruments()
    out = []
    for sym in symbols:
        meta = adapter.get_instrument_metadata(sym)
        canon = meta.canonical_symbol if meta else sym.upper()
        row = (db.query(ProviderInstrumentMapping)
               .filter(ProviderInstrumentMapping.connection_id == conn.id,
                       ProviderInstrumentMapping.provider_symbol == sym).first())
        if row is None:
            db.add(ProviderInstrumentMapping(
                connection_id=conn.id, workspace_id=workspace_id, provider=PROVIDER_TYPE,
                provider_symbol=sym, canonical_symbol=canon, display_symbol=canon,
                base_currency=canon[:3], quote_currency=canon[3:] or "USD",
                pip_size=meta.pip_size if meta else 0.0001,
                contract_size=meta.contract_size if meta else 100000.0,
                minimum_lot=meta.minimum_lot if meta else 0.01,
                lot_step=meta.lot_step if meta else 0.01,
                is_supported=True,
                last_verified_at=_now()))
        else:
            if canon != row.canonical_symbol:
                row.canonical_symbol = canon
                row.display_symbol = canon
            row.is_supported = True
            row.last_verified_at = _now()
        db.commit()
        out.append({"provider_symbol": sym, "canonical_symbol": canon,
                    "display_symbol": canon, "connection_id": conn.id})
    return out


def get_capabilities(db, workspace_id, connection_id):
    conn = get_connection(db, workspace_id, connection_id)
    rows = (db.query(ProviderConnectionCapability)
            .filter(ProviderConnectionCapability.connection_id == conn.id).all())
    caps = {r.capability: r.availability for r in rows}
    return {"connection_id": conn.id, "status": conn.status,
            "environment": conn.environment, "capabilities": caps,
            "live_trading_status": "disabled"}


def disconnect_exness(db, workspace_id, connection_id, user_id):
    conn = get_connection(db, workspace_id, connection_id)
    previous = conn.status
    conn.status = "DISCONNECTED"
    conn.health_status = "disconnected"
    _write_audit(db, workspace_id, conn.id, "disconnect", "completed", user_id, f"disconnect from {previous}")
    db.commit()
    return {"connection_id": conn.id, "status": conn.status}


def issue_pairing_token(db, workspace_id, user_id, payload: ExnessPairGatewayRequest):
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires_at = _now() + PAIRING_TOKEN_TTL_SECONDS
    existing = None
    if payload.idempotency_key:
        existing = (db.query(MT5GatewayAgent)
                    .filter(MT5GatewayAgent.workspace_id == workspace_id,
                            MT5GatewayAgent.meta["idempotency_key"].astext == payload.idempotency_key)
                    .first())
    if existing is not None:
        stored = decrypt_secret(existing.encrypted_pairing_token or "")
        if stored and existing.pairing_token_expires_at and existing.pairing_token_expires_at > _now():
            return {"gateway_id": existing.id, "pairing_token": stored,
                    "expires_in_seconds": int(existing.pairing_token_expires_at - _now()),
                    "expires_at_utc": datetime.fromtimestamp(existing.pairing_token_expires_at, tz=timezone.utc).isoformat(),
                    "note": "Reused existing pairing (idempotency key). Token is short-lived and shown once."}
    gateway = MT5GatewayAgent(workspace_id=workspace_id, gateway_url=payload.gateway_url,
                              device_name=payload.device_name,
                              encrypted_pairing_token=encrypt_secret(token),
                              pairing_token_expires_at=expires_at, status="PAIRING",
                              meta={"account_label": payload.account_label or "",
                                    "idempotency_key": payload.idempotency_key or ""})
    db.add(gateway)
    db.flush()
    db.add(MT5GatewayPairingEvent(workspace_id=workspace_id, gateway_id=gateway.id,
                                  event_type="token_issued", detail_safe="pairing token issued",
                                  issued_at=_now(), expires_at=expires_at))
    _write_audit(db, workspace_id, None, "pair", "started", user_id,
                 f"gateway pairing initiated for {redact_text(payload.device_name)}")
    db.commit()
    return {"gateway_id": gateway.id, "pairing_token": token,
            "expires_in_seconds": PAIRING_TOKEN_TTL_SECONDS,
            "expires_at_utc": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "note": "Token is short-lived and shown once; encrypted at rest on the backend."}


def validate_pairing_token(db, workspace_id, gateway_id, token):
    gateway = db.get(MT5GatewayAgent, gateway_id)
    if not gateway or gateway.workspace_id != workspace_id:
        raise ExnessConnectionError("gateway not found")
    if gateway.status in ("REVOKED", "EXPIRED"):
        raise ExnessConnectionError(f"gateway is {gateway.status.lower()}")
    if gateway.pairing_token_expires_at and _now() > gateway.pairing_token_expires_at:
        gateway.status = "EXPIRED"
        db.add(MT5GatewayPairingEvent(workspace_id=workspace_id, gateway_id=gateway.id,
                                      event_type="token_expired", detail_safe="pairing token expired"))
        db.commit()
        raise ExnessConnectionError("pairing token expired")
    stored = decrypt_secret(gateway.encrypted_pairing_token or "")
    if stored != token:
        db.add(MT5GatewayPairingEvent(workspace_id=workspace_id, gateway_id=gateway.id,
                                      event_type="rejected", detail_safe="invalid pairing token"))
        db.commit()
        raise ExnessConnectionError("invalid pairing token")
    gateway.status = "ONLINE"
    gateway.encrypted_pairing_token = ""
    gateway.pairing_token_expires_at = None
    gateway.last_seen_at = _now()
    db.add(MT5GatewayPairingEvent(workspace_id=workspace_id, gateway_id=gateway.id,
                                  event_type="paired", detail_safe="gateway paired successfully"))
    db.commit()
    return {"gateway_id": gateway.id, "status": gateway.status}

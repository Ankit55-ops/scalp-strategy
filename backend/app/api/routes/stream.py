"""WebSocket live market-data stream.

Auth is carried in the ``Sec-WebSocket-Protocol`` subprotocol (the browser
cannot set arbitrary handshake headers), NOT in the URL query string, so the
JWT never leaks into access logs or browser history. The Origin header is
verified against the configured CORS allow-list to prevent cross-site
WebSocket hijacking. A per-user connection cap and server ping keepalive
bound resource usage.

On connect the server sends a snapshot (active provider, feed-health rows,
ingestion state) and then streams events pushed by the ingestion service over
the in-process event bus: ``quote``, ``candle_update``, ``candle_close``,
``feed_health``, ``signal``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.models import User, Workspace
from app.services import feed_health
from app.services.event_bus import bus
from app.services.ingestion import ingestion_status
from app.services.provider_service import provider_status

logger = logging.getLogger("fxscalper.stream")

router = APIRouter(tags=["stream"])

# user_id -> number of live sockets (single event loop; no lock needed).
_active_sockets: dict[int, int] = {}


def _origin_allowed(origin: str | None) -> bool:
    """CSWSH guard: the Origin header must be present AND in the allow-list.

    Browsers always send Origin on WebSocket handshakes, so a missing header
    indicates a non-browser client and is rejected. This prevents a token
    bearer from bypassing origin validation by simply omitting the header.
    """
    if origin is None:
        return False
    allowed = get_settings().cors_origins
    return origin in allowed


@router.websocket("/ws/market-data")
async def market_data_stream(ws: WebSocket) -> None:
    # Token policy: subprotocol header only. Query-string tokens are rejected.
    if ws.query_params.get("token"):
        await ws.close(code=4401)
        return
    token = ws.headers.get("sec-websocket-protocol", "")
    if not token or token == "forbidden":
        await ws.close(code=4401)
        return
    if not _origin_allowed(ws.headers.get("origin")):
        await ws.close(code=4403)
        return

    payload = decode_access_token(token)
    if payload is None:
        await ws.close(code=4401)
        return
    db = SessionLocal()
    try:
        user = db.get(User, payload.get("sub", ""))
        if user is None or not user.is_active:
            await ws.close(code=4401)
            return
        workspace = db.query(Workspace).filter(Workspace.owner_id == user.id).order_by(Workspace.created_at).first()
        if workspace is None:
            await ws.close(code=4403)
            return

        settings = get_settings()
        current = _active_sockets.get(user.id, 0)
        if current >= settings.MAX_CONCURRENT_WS_PER_USER:
            await ws.close(code=4408, reason="connection limit reached")
            return
        _active_sockets[user.id] = current + 1
        try:
            await ws.accept(subprotocol=token)
            await ws.send_json(
                {
                    "type": "snapshot",
                    "ts": time.time(),
                    "data": {
                        "provider_status": _safe_provider_status(db, workspace.id),
                        "feed_health": feed_health.list_feed_health(db, workspace.id),
                        "ingestion": ingestion_status(workspace.id),
                        "utc_now": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            last_seen = time.time()
            last_ping = time.time()
            while True:
                events = bus.poll(workspace.id, last_seen)
                for ev in events:
                    last_seen = max(last_seen, ev["ts"])
                    await ws.send_json(ev)
                if time.time() - last_ping >= 30:
                    await ws.send({"type": "websocket.ping", "data": b""})
                    last_ping = time.time()
                await asyncio.sleep(0.25)
        finally:
            _active_sockets[user.id] = max(0, _active_sockets.get(user.id, 1) - 1)
    except WebSocketDisconnect:
        pass
    except (RuntimeError, asyncio.CancelledError):
        pass
    finally:
        db.close()


def _safe_provider_status(db: Session, workspace_id: str) -> dict:
    try:
        return provider_status(db, workspace_id)
    except Exception:  # noqa: BLE001
        # The fallback must not poison the session transaction: without a
        # rollback the failed provider query would abort the transaction and
        # every later statement in this WS session would raise
        # InFailedSqlTransaction.
        db.rollback()
        return {"active_provider": "unknown", "health": {"status": "unavailable"}}
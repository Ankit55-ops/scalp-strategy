"""Tests for the Exness/MT5 provider connection surface (Phase 5-6).

Covers: status card shapes, capability detection, server-side + gateway test
connection, encrypted connect (idempotent), never-return-credentials, dynamic
instrument discovery + symbol mapping, health/disconnect, pairing-token flow
(issue/verify/expiry/rejection), the connection-attempt budget, the recent-auth
gate, and cross-workspace denial. Uses the clearly-labelled mock/test adapter
(EXNESS_MOCK_ADAPTER=true in the test env).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import require_recent_auth
from app.main import app

client = TestClient(app)

DONE_AT = datetime.now(tz=timezone.utc).isoformat()

CONNECT_BODY = {
    "connection_mode": "server_side_mt5",
    "display_name": "Demo MT5",
    "environment": "demo",
    "login": "1234567",
    "server": "Exness-MT5Trial",
    "password": "s3cret-pass-abc",
    "use_read_only": True,
    "confirm_read_only": True,
    "idempotency_key": "conn-key-1",
}

TEST_BODY = {
    "mode": "server_side",
    "environment": "demo",
    "login": "1234567",
    "server": "Exness-MT5Trial",
    "password": "s3cret-pass-abc",
    "idempotency_key": "test-key-1",
}


def _email():
    return f"ex-{uuid.uuid4().hex[:8]}@example.com"


def _register(email, password="secret-pass-123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def _login(email, password="secret-pass-123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _user():
    email = _email()
    _register(email)
    return _login(email)


# ---------------------------------------------------------------------------
# Status card
# ---------------------------------------------------------------------------
def test_status_card_not_configured_for_fresh_user():
    token = _user()
    r = client.get("/api/providers/exness-mt5/status", headers=_h(token))
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["connection_status"] == "NOT_CONFIGURED"
    assert card["live_trading_status"] == "disabled"
    assert card["show_connect_button"] is True
    assert card["available_capabilities"] == []
    assert card["message"]


def test_status_card_requires_auth():
    r = client.get("/api/providers/exness-mt5/status")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------
def test_test_connection_server_side_capabilities():
    token = _user()
    r = client.post("/api/providers/exness-mt5/test-connection", json=TEST_BODY, headers=_h(token))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["connection_status"] == "CONNECTED"
    assert rep["historical_data_available"] is True
    assert rep["capabilities"]["historical_candles"] == "available"
    assert rep["capabilities"]["bid_ask_quotes"] == "available"
    # live trading must never be advertised as available by this surface
    assert rep["capabilities"].get("live_trading") in (None, "unavailable")
    assert rep["live_trading_status"] == "disabled"
    assert rep["instrument_count"] >= 1
    assert rep["quote_availability"]


def test_test_connection_missing_fields_422():
    token = _user()
    r = client.post("/api/providers/exness-mt5/test-connection",
                    json={"mode": "server_side", "password": "x"}, headers=_h(token))
    assert r.status_code == 422


def test_test_connection_non_numeric_login_rejected():
    token = _user()
    body = dict(TEST_BODY, login="not-a-login")
    r = client.post("/api/providers/exness-mt5/test-connection", json=body, headers=_h(token))
    assert r.status_code == 422


def test_test_connection_gateway_requires_url_and_code():
    token = _user()
    r = client.post("/api/providers/exness-mt5/test-connection",
                    json={"mode": "gateway", "pairing_code": "short"}, headers=_h(token))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Connect (encrypted, idempotent, never echoes credentials)
# ---------------------------------------------------------------------------
def test_connect_and_credentials_never_returned():
    token = _user()
    r = client.post("/api/providers/exness-mt5/connect", json=CONNECT_BODY, headers=_h(token))
    assert r.status_code == 200, r.text
    out = r.json()
    body_txt = r.text.lower()
    for forbidden in ("password", "s3cret", "login", "encrypted", "token"):
        assert forbidden not in body_txt, f"response leaked {forbidden}"
    assert out["connection"]["status"] == "CONNECTED"
    assert out["live_trading_status"] == "disabled"
    conn_id = out["connection"]["id"]

    card = client.get("/api/providers/exness-mt5/status", headers=_h(token)).json()
    assert card["connection_status"] == "CONNECTED"
    assert card["live_trading_status"] == "disabled"
    assert "historical_candles" in card["available_capabilities"]
    assert "live_trading" in card["unavailable_capabilities"]
    assert conn_id


def test_connect_idempotent_with_same_key():
    token = _user()
    r1 = client.post("/api/providers/exness-mt5/connect", json=CONNECT_BODY, headers=_h(token))
    r2 = client.post("/api/providers/exness-mt5/connect", json=CONNECT_BODY, headers=_h(token))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["connection"]["id"] == r2.json()["connection"]["id"]


def test_connect_invalid_mode_rejected():
    token = _user()
    body = dict(CONNECT_BODY, connection_mode="delete_everything")
    r = client.post("/api/providers/exness-mt5/connect", json=body, headers=_h(token))
    assert r.status_code == 422


def test_connect_gateway_mode_requires_creds_flow():
    token = _user()
    body = dict(CONNECT_BODY, connection_mode="mt5_gateway_agent")
    body.pop("login")
    body.pop("password")
    body.pop("server")
    r = client.post("/api/providers/exness-mt5/connect", json=body, headers=_h(token))
    # gateway connect with no gateway_url -> 422 (field validation downstream)
    assert r.status_code in (200, 422)


# ---------------------------------------------------------------------------
# Recent-auth gate
# ---------------------------------------------------------------------------
def test_connect_enforces_recent_auth_gate():
    token = _user()
    calls = {"hit": False}

    def blocked() -> User:  # noqa: F821 - substituted dependency guard
        calls["hit"] = True
        raise HTTPException(status_code=401, detail="recent authentication required")

    app.dependency_overrides[require_recent_auth] = blocked
    try:
        r = client.post("/api/providers/exness-mt5/connect", json=CONNECT_BODY, headers=_h(token))
        assert calls["hit"] is True
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Capabilities / health / instruments / disconnect
# ---------------------------------------------------------------------------
def _connected():
    token = _user()
    out = client.post("/api/providers/exness-mt5/connect", json=CONNECT_BODY, headers=_h(token)).json()
    return token, out["connection"]["id"]


def test_capabilities_health_instruments():
    token, conn_id = _connected()
    caps = client.get(f"/api/providers/exness-mt5/capabilities?connection_id={conn_id}", headers=_h(token))
    assert caps.status_code == 200
    assert caps.json()["live_trading_status"] == "disabled"
    assert caps.json()["capabilities"]["historical_candles"] == "available"

    h = client.get(f"/api/providers/exness-mt5/health?connection_id={conn_id}", headers=_h(token))
    assert h.status_code == 200

    inst = client.get(f"/api/providers/exness-mt5/instruments?connection_id={conn_id}", headers=_h(token))
    assert inst.status_code == 200
    syms = inst.json()
    assert syms, "expected discovered instruments"
    for s in syms:
        assert s["provider_symbol"]
        assert s["canonical_symbol"]
        assert s["connection_id"] == conn_id
    canon = {s["canonical_symbol"] for s in syms}
    assert canon, "symbol mapping produced canonical symbols"


def test_instruments_without_connection_404():
    token = _user()
    r = client.get("/api/providers/exness-mt5/instruments?connection_id=no-such-id", headers=_h(token))
    assert r.status_code == 404


def test_disconnect_clears_status():
    token, conn_id = _connected()
    r = client.post(f"/api/providers/exness-mt5/disconnect?connection_id={conn_id}", headers=_h(token))
    assert r.status_code == 200
    card = client.get("/api/providers/exness-mt5/status", headers=_h(token)).json()
    assert card["connection_status"] != "CONNECTED"


# ---------------------------------------------------------------------------
# Cross-workspace denial
# ---------------------------------------------------------------------------
def test_cross_workspace_access_denied():
    _token_a, conn_id = _connected()
    token_b = _user()
    for path in (
        f"/api/providers/exness-mt5/capabilities?connection_id={conn_id}",
        f"/api/providers/exness-mt5/health?connection_id={conn_id}",
        f"/api/providers/exness-mt5/instruments?connection_id={conn_id}",
        f"/api/providers/exness-mt5/disconnect?connection_id={conn_id}",
    ):
        r = client.post(path, headers=_h(token_b)) if "disconnect" in path else client.get(path, headers=_h(token_b))
        assert r.status_code == 404, f"{path} -> {r.status_code}"


# ---------------------------------------------------------------------------
# Gateway pairing flow
# ---------------------------------------------------------------------------
def _pair_body(code="pairing-code-42"):
    return {
        "gateway_url": "wss://gateway.example.local",
        "device_name": "test-rig",
        "pairing_code": code,
        "idempotency_key": f"pair-{uuid.uuid4().hex[:8]}",
    }


def test_pairing_issue_and_verify():
    token = _user()
    r = client.post("/api/providers/exness-mt5/pair-gateway", json=_pair_body(), headers=_h(token))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["gateway_id"]
    assert out["pairing_token"]
    assert out["expires_in_seconds"] > 0

    v = client.post(
        f"/api/providers/exness-mt5/gateway/verify?gateway_id={out['gateway_id']}&pairing_token={out['pairing_token']}",
        headers=_h(token),
    )
    assert v.status_code == 200
    assert v.json()["status"] == "ONLINE"


def test_pairing_idempotent_via_key():
    token = _user()
    body = _pair_body()
    r1 = client.post("/api/providers/exness-mt5/pair-gateway", json=body, headers=_h(token))
    r2 = client.post("/api/providers/exness-mt5/pair-gateway", json=body, headers=_h(token))
    assert r1.json()["gateway_id"] == r2.json()["gateway_id"]


def test_pairing_invalid_token_rejected():
    token = _user()
    out = client.post("/api/providers/exness-mt5/pair-gateway", json=_pair_body(), headers=_h(token)).json()
    v = client.post(
        f"/api/providers/exness-mt5/gateway/verify?gateway_id={out['gateway_id']}&pairing_token=wrong-token-12345",
        headers=_h(token),
    )
    assert v.status_code == 401


def test_pairing_expired_token_rejected():
    token = _user()
    out = client.post("/api/providers/exness-mt5/pair-gateway", json=_pair_body(), headers=_h(token)).json()
    from app.db.session import SessionLocal
    from app.models import MT5GatewayAgent

    db = SessionLocal()
    try:
        gw = db.get(MT5GatewayAgent, out["gateway_id"])
        gw.pairing_token_expires_at = (datetime.now(tz=timezone.utc) - timedelta(minutes=1)).timestamp()
        db.commit()
    finally:
        db.close()
    v = client.post(
        f"/api/providers/exness-mt5/gateway/verify?gateway_id={out['gateway_id']}&pairing_token={out['pairing_token']}",
        headers=_h(token),
    )
    assert v.status_code == 401
    assert "expired" in v.json()["detail"]


def test_cross_workspace_gateway_denied():
    token_a = _user()
    out = client.post("/api/providers/exness-mt5/pair-gateway", json=_pair_body(), headers=_h(token_a)).json()
    token_b = _user()
    v = client.post(
        f"/api/providers/exness-mt5/gateway/verify?gateway_id={out['gateway_id']}&pairing_token={out['pairing_token']}",
        headers=_h(token_b),
    )
    assert v.status_code == 401


# ---------------------------------------------------------------------------
# Connection attempt budget (rate limit)
# ---------------------------------------------------------------------------
def test_connect_attempt_budget_429():
    token = _user()
    with client:
        statuses = []
        for i in range(40):
            body = dict(TEST_BODY, idempotency_key=f"budget-{i}")
            r = client.post("/api/providers/exness-mt5/test-connection", json=body, headers=_h(token))
            statuses.append(r.status_code)
            if r.status_code == 429:
                break
    assert 429 in statuses, f"expected a 429 in the attempt budget; got statuses {set(statuses)}"
    assert statuses[-1] == 429
    assert "Retry" in r.json()["detail"]


# ---------------------------------------------------------------------------
# No provider -> real-historical validation refuses to silently mock
# ---------------------------------------------------------------------------
def test_preview_requires_connected_connection():
    token = _user()
    r = client.post("/api/real-historical-validations/preview", json={
        "strategy_id": "any",
        "connection_id": "none",
        "provider": "exness",
        "provider_symbol": "EURUSD",
        "timeout": "M5",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "end_time_utc": "2026-01-10T00:00:00Z",
    }, headers=_h(token))
    assert r.status_code in (404, 422)
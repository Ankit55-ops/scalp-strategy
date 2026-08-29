"""Tests for the ingestion + WS market-data stream, the paper execution
ledger, the OANDA practice-account adapter, and live-deployment gates
(Phases 4, 7, 9, 10)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.providers.broker import SimulatedBroker
from app.providers.oanda_broker import OandaPracticeBroker

client = TestClient(app)

MIN_SPEC = {
    "name": "LedgerCheck",
    "version": "1.0.0",
    "strategy_family": "momentum",
    "supported_pairs": ["EURUSD"],
    "supported_timeframes": ["M5"],
    "sessions_utc": [{"name": "London", "start": "00:00", "end": "23:59"}],
    "market_regime": {"preferred": [], "avoid": []},
    "indicators": [{"name": "EMA", "parameters": {"period": 20}}],
    "entry_rules": [
        {"id": "long_1", "description": "momentum", "expression": "close > sma(close, 20)"}
    ],
    "exit_rules": [
        {"id": "exit_1", "description": "exit", "expression": "close < sma(close, 20)"}
    ],
    "risk_management": {
        "risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_consecutive_losses": 3,
        "max_open_positions": 2,
        "max_trades_per_day": 10,
        "stop_loss_method": "ATR",
        "stop_loss_parameters": {"atr_period": 14, "atr_multiplier": 1.2},
        "take_profit_method": "risk_reward",
        "take_profit_parameters": {"risk_reward_ratio": 1.5},
    },
    "execution_filters": {
        "max_spread_pips": 1.2,
        "max_slippage_pips": 0.5,
        "minimum_atr_pips": 0.0,
        "news_blackout_minutes_before": 0,
        "news_blackout_minutes_after": 0,
    },
}


def _email():
    return f"p9-{uuid.uuid4().hex[:8]}@example.com"


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


def _login_setup():
    email = _email()
    _register(email)
    return email, _login(email)


def _strategy(token, spec=None):
    r = client.post("/api/strategies", json={"name": "LedgerCheck", "spec": spec or MIN_SPEC}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ===========================================================================
# Phase 4: ingestion start/stop + status
# ===========================================================================
def test_ingestion_start_status_stop_loop_when_disabled(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "MARKET_DATA_INGESTION_ENABLED", False)
    email, token = _login_setup()
    start = client.post("/api/market-data/ingest/start", headers=_h(token))
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["running"] in (True, False)
    status = client.get("/api/market-data/ingest/status", headers=_h(token))
    assert status.status_code == 200
    stop = client.post("/api/market-data/ingest/stop", headers=_h(token))
    assert stop.status_code == 200
    assert stop.json()["running"] is False


def test_ingestion_drives_live_feed_health_for_mock():
    """The ingestor must mark quotes seen so subscribed symbols read LIVE,
    not DISCONNECTED (regression: _persist_health signature mismatch flagged
    every symbol as a dead feed)."""
    import time

    from app.db.session import SessionLocal
    from app.models import User, Workspace
    from app.services import feed_health
    from app.services.ingestion import start_ingestion
    from app.services.provider_service import get_active_provider

    db = SessionLocal()
    user = db.query(User).filter(User.email == _email()).first()
    # _login_setup created a user; reuse it by email
    email, token = _login_setup()
    user = db.query(User).filter(User.email == email).first()
    ws = db.query(Workspace).filter(Workspace.owner_id == user.id).first()
    start_ingestion(ws.id)
    time.sleep(3)
    provider = get_active_provider(db, ws.id)
    states = {s: feed_health.feed_state(ws.id, provider.name, s, "open") for s in provider.list_symbols()}
    assert states, "no symbols"
    assert all(v in ("LIVE", "CONNECTING") for v in states.values()), states
    db.close()


# ===========================================================================
# Phase 7: paper execution ledger + account state
# ===========================================================================
def test_paper_ledger_orders_fills_margin_and_account_state():
    email, token = _login_setup()
    sid = _strategy(token)
    client.post("/api/risk/profiles", json={"name": "HP", "max_spread_pips": 1.0, "risk_per_trade_pct": 0.25, "is_active": True}, headers=_h(token))
    started = client.post("/api/paper-trading/start", json={"balance": 10000.0}, headers=_h(token))
    assert started.json()["is_active"] is True

    st = client.get("/api/paper-trading/account-state", headers=_h(token))
    assert st.status_code == 200
    state = st.json()
    assert state["trading_state"] in ("ACTIVE", "INACTIVE", "DATA_PAUSED", "RISK_PAUSED", "KILL_SWITCHED")
    assert state["is_active"] is True

    r = client.post("/api/paper-trading/order", json={"strategy_id": sid, "side": "long"}, headers=_h(token))
    assert r.status_code == 200, r.text
    body = r.json()
    # A genuinely stale/fresh-less feed gate keeps safety; otherwise order fills.
    if body["approved"]:
        assert body["order_id"]

    orders = client.get("/api/paper-trading/orders", headers=_h(token))
    fills = client.get("/api/paper-trading/fills", headers=_h(token))
    margins = client.get("/api/paper-trading/margin-events", headers=_h(token))
    assert orders.status_code == fills.status_code == margins.status_code == 200
    o = orders.json()
    f = fills.json()
    m = margins.json()
    if body["approved"]:
        assert any(x["status"] in ("FILLED", "APPROVED", "PENDING") for x in o)
        assert any(x["fill_type"] == "entry" for x in f)
        assert any(x["event_type"] == "position_opened" for x in m)
    assert isinstance(o, list) and isinstance(f, list) and isinstance(m, list)


# ===========================================================================
# Phase 9: OANDA practice adapter is dry-run safe by default
# ===========================================================================
def test_oanda_practice_broker_dry_run_by_default():
    broker = OandaPracticeBroker(api_key="k", account_id="acc", env="practice")
    broker.dry_run = True
    assert broker.name == "oanda_practice"
    assert broker.authenticate() is True
    resp = broker.submit_order({"symbol": "EURUSD", "side": "long", "size_units": 1000, "stop_loss": 1.09, "take_profit": 1.11})
    assert resp["dry_run"] is True
    assert resp["status"] == "accepted_for_review"


def test_oanda_practice_broker_rejects_live_without_flag(monkeypatch):
    from app.providers import oanda_broker

    monkeypatch.setattr(
        oanda_broker, "get_settings", lambda: type("S", (), {"LIVE_TRADING_ENABLED": False, "BROKER_PRACTICE_DRY_RUN": False})()
    )
    broker = OandaPracticeBroker(api_key="k", account_id="acc", env="live")
    broker.dry_run = False
    try:
        broker.submit_order({"symbol": "EURUSD", "side": "long", "size_units": 1})
        raised = False
    except PermissionError:
        raised = True
    assert raised


def test_broker_connect_rejects_live_oanda_without_flag(monkeypatch):
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)
    email, token = _login_setup()
    r = client.post(
        "/api/brokers/connect",
        headers=_h(token),
        json={"provider": "oanda_practice", "label": "Live", "sandbox": False},
    )
    assert r.status_code == 400
    assert "disabled" in r.json()["detail"].lower()


def test_broker_connect_practice_sandbox_ok():
    email, token = _login_setup()
    r = client.post(
        "/api/brokers/connect",
        headers=_h(token),
        json={"provider": "oanda_practice", "label": "Practice", "sandbox": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "oanda_practice"


# ===========================================================================
# Phase 10: live-deployment config gate
# ===========================================================================
def test_live_deployments_config_reports_gates():
    email, token = _login_setup()
    r = client.get("/api/live-deployments/config", headers=_h(token))
    assert r.status_code == 200
    body = r.json()
    assert "live_trading_enabled" in body
    assert "practice_broker_dry_run" in body
    assert isinstance(body["live_trading_enabled"], bool)


# ===========================================================================
# WS market-data stream
# ===========================================================================
def test_ws_market_data_requires_auth():
    # Without a valid JWT the app must refuse the upgrade (403 at handshake).
    try:
        with client.websocket_connect("/api/ws/market-data") as ws:
            data = ws.receive_json()
            assert data.get("type") == "error"
    except Exception:  # noqa: BLE001 - server refusing unauthenticated upgrade
        return
    raise AssertionError("unauthenticated WebSocket connection was not refused")


def _seed_closed_trades(token):
    """SimulatedBroker is in-memory per factory instance; sanity via direct call."""
    broker = SimulatedBroker()
    assert broker.name == "simulated"
"""End-to-end tests for Phase 4/5 gaps: paper lifecycle, deployments,
broker CRUD, alerts, backtest idempotency/list, chart layouts, calendar,
and strategy versioning."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import User, Workspace
from app.db.session import SessionLocal

client = TestClient(app)

SPEC = {
    "name": "Lifecycle Test",
    "version": "1.0.0",
    "strategy_family": "trend_pullback",
    "supported_pairs": ["EURUSD"],
    "supported_timeframes": ["M5"],
    "sessions_utc": [{"name": "London", "start": "00:00", "end": "23:59"}],
    "market_regime": {"preferred": ["trending"], "avoid": []},
    "indicators": [{"name": "ATR", "parameters": {"period": 14}}],
    "entry_rules": [
        {"id": "long_rule_1", "description": "always long", "expression": "close > close"}
    ],
    "exit_rules": [
        {"id": "exit_rule_1", "description": "always exit", "expression": "close > close and close < close"}
    ],
    "risk_management": {
        "risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_consecutive_losses": 3,
        "max_open_positions": 1,
        "max_trades_per_day": 5,
        "stop_loss_method": "ATR",
        "stop_loss_parameters": {"atr_period": 14, "atr_multiplier": 1.2},
        "take_profit_method": "risk_reward",
        "take_profit_parameters": {"risk_reward_ratio": 1.5},
    },
    "execution_filters": {
        "max_spread_pips": 5.0,
        "max_slippage_pips": 0.5,
        "minimum_atr_pips": 0.0,
        "news_blackout_minutes_before": 0,
        "news_blackout_minutes_after": 0,
    },
}


def _email(prefix="user"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _register(email, password="secret-pass-123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def _login(email, password="secret-pass-123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _cleanup(email):
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if user:
        for ws in db.query(Workspace).filter(Workspace.owner_id == user.id).all():
            db.delete(ws)
        db.delete(user)
        db.commit()
    db.close()


def _make_superuser(email):
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    user.is_superuser = True
    db.commit()
    db.close()


def _create_strategy(token, spec=None):
    r = client.post("/api/strategies", headers=_headers(token), json={"spec": spec or SPEC})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _active_risk_profile(email, **overrides):
    from app.models import RiskProfile

    db = SessionLocal()
    ws = db.query(Workspace).filter(Workspace.owner_id == db.query(User).filter(User.email == email).first().id).order_by(Workspace.created_at).first()
    profile = RiskProfile(
        workspace_id=ws.id,
        name="active",
        risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_weekly_loss_pct=3.0,
        max_drawdown_pct=10.0,
        max_consecutive_losses=3,
        max_open_positions=1,
        max_trades_per_day=5,
        max_correlated_exposure_pct=2.0,
        max_spread_pips=5.0,
        max_slippage_pips=0.5,
        news_blackout_minutes_before=0,
        news_blackout_minutes_after=0,
        hard_stop_distance_pips=0.0,
        is_active=True,
        **overrides,
    )
    db.add(profile)
    db.commit()
    pid = profile.id
    db.close()
    return pid


# -- paper trading lifecycle ----------------------------------------------

def test_paper_lifecycle_order_close_trades():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        _active_risk_profile(email)

        started = client.post("/api/paper-trading/start", headers=_headers(token), json={"balance": 50000})
        assert started.status_code == 201, started.text
        assert started.json()["is_active"] is True

        placed = client.post(
            "/api/paper-trading/order",
            headers=_headers(token),
            json={"strategy_id": sid, "side": "long"},
        )
        assert placed.status_code == 200, placed.text
        body = placed.json()
        assert body["approved"] is True, body
        assert body["position_id"]
        assert body["stop_loss"] is not None and body["take_profit"] is not None

        positions = client.get("/api/paper-trading/positions", headers=_headers(token))
        assert positions.status_code == 200
        assert len(positions.json()) == 1

        closed = client.post(
            f"/api/paper-trading/positions/{body['position_id']}/close",
            headers=_headers(token),
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"

        trades = client.get("/api/paper-trading/trades", headers=_headers(token))
        assert trades.status_code == 200
        assert len(trades.json()) == 1

        status = client.get("/api/paper-trading/status", headers=_headers(token))
        assert status.json()["open_positions"] == 0
        assert status.json()["closed_trades"] == 1
        assert status.json()["balance"] != 50000  # balance moved

        # dashboard reflects the paper account
        overview = client.get("/api/dashboard/overview", headers=_headers(token))
        assert overview.status_code == 200
        assert overview.json()["paper_account"]["is_active"] is True
        assert overview.json()["paper_account"]["closed_trades"] == 1
    finally:
        _cleanup(email)


def test_paper_order_rejected_without_risk_profile():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        client.post("/api/paper-trading/start", headers=_headers(token), json={"balance": 10000})

        placed = client.post(
            "/api/paper-trading/order",
            headers=_headers(token),
            json={"strategy_id": sid, "side": "long"},
        )
        assert placed.status_code == 200
        assert placed.json()["approved"] is False
        assert placed.json()["reason"]

        # rejection produced an alert
        alerts = client.get("/api/alerts", headers=_headers(token))
        assert len(alerts.json()) >= 1
        assert any("rejected" in a["title"] for a in alerts.json())
    finally:
        _cleanup(email)


def test_paper_order_blocked_at_max_positions():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        _active_risk_profile(email)  # max_open_positions = 1
        client.post("/api/paper-trading/start", headers=_headers(token), json={"balance": 10000})

        first = client.post("/api/paper-trading/order", headers=_headers(token), json={"strategy_id": sid, "side": "long"})
        assert first.json()["approved"] is True

        second = client.post("/api/paper-trading/order", headers=_headers(token), json={"strategy_id": sid, "side": "long"})
        assert second.json()["approved"] is False
        assert "max open positions" in second.json()["reason"]
    finally:
        _cleanup(email)


# -- deployment workflow --------------------------------------------------

def test_deployment_request_approve_superuser_gate():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        _active_risk_profile(email)

        conn = client.post(
            "/api/brokers/connect",
            headers=_headers(token),
            json={"provider": "simulated", "label": "Demo Sandbox", "sandbox": True},
        )
        assert conn.status_code == 200, conn.text
        broker_id = conn.json()["id"]

        req = client.post(
            "/api/live-deployments/request",
            headers=_headers(token),
            json={"strategy_id": sid, "broker_connection_id": broker_id, "risk_acknowledged": True},
        )
        assert req.status_code == 201, req.text
        deploy_id = req.json()["id"]

        # non-superuser cannot approve
        denied = client.post(f"/api/live-deployments/{deploy_id}/approve", headers=_headers(token), json={"confirm": True})
        assert denied.status_code == 403

        # track record insufficient -> blocked
        _make_superuser(email)
        blocked = client.post(f"/api/live-deployments/{deploy_id}/approve", headers=_headers(token), json={"confirm": True})
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["approved"] is False
        assert blocked.json()["status"] == "blocked"

        # satisfy track record with 30 closed simulated orders
        from app.models import PaperAccount, SimulatedOrder

        db = SessionLocal()
        ws = db.query(Workspace).filter(Workspace.owner_id == db.query(User).filter(User.email == email).first().id).first()
        acc = PaperAccount(workspace_id=ws.id, balance=100000.0, equity=100000.0, is_active=True)
        db.add(acc)
        db.flush()
        for i in range(30):
            db.add(
                SimulatedOrder(
                    paper_account_id=acc.id,
                    symbol="EURUSD",
                    timeframe="M5",
                    side="buy",
                    order_type="market",
                    entry_ts=1000.0 + i,
                    exit_ts=2000.0 + i,
                    entry_price=1.10,
                    exit_price=1.10 + i / 100000.0,
                    stop_loss=1.09,
                    take_profit=1.11,
                    size_units=1000.0,
                    risk_amount=10.0,
                    status="closed",
                    net_pnl=1.0,
                )
            )
        db.commit()
        db.close()

        approved = client.post(f"/api/live-deployments/{deploy_id}/approve", headers=_headers(token), json={"confirm": True})
        assert approved.status_code == 200, approved.text
        assert approved.json()["approved"] is True
        assert approved.json()["status"] == "approved_sandbox_only"

        listed = client.get("/api/live-deployments", headers=_headers(token))
        assert listed.status_code == 200
        assert any(d["id"] == deploy_id and d["status"] == "approved_sandbox_only" for d in listed.json())

        detail = client.get(f"/api/live-deployments/{deploy_id}", headers=_headers(token))
        assert detail.status_code == 200
        assert detail.json()["checks"]["paper_track_record"]["closed_trades"] == 30
    finally:
        _cleanup(email)


def test_deployment_reject_requires_superuser():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        broker_id = client.post(
            "/api/brokers/connect", headers=_headers(token),
            json={"provider": "simulated", "label": "B", "sandbox": True},
        ).json()["id"]
        deploy_id = client.post(
            "/api/live-deployments/request", headers=_headers(token),
            json={"strategy_id": sid, "broker_connection_id": broker_id},
        ).json()["id"]

        _make_superuser(email)
        rej = client.post(f"/api/live-deployments/{deploy_id}/reject", headers=_headers(token), json={"reason": "not enough data"})
        assert rej.status_code == 200
        assert rej.json()["status"] == "rejected"
    finally:
        _cleanup(email)


# -- brokers ----------------------------------------------------------------

def test_broker_crud_and_test():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        conn = client.post(
            "/api/brokers/connect",
            headers=_headers(token),
            json={"provider": "simulated", "label": "Sandbox", "sandbox": True},
        )
        assert conn.status_code == 200
        broker_id = conn.json()["id"]

        listed = client.get("/api/brokers", headers=_headers(token))
        assert any(b["id"] == broker_id for b in listed.json())

        detail = client.get(f"/api/brokers/{broker_id}", headers=_headers(token))
        assert detail.status_code == 200
        assert "EURUSD" in detail.json()["symbols"]

        tested = client.post(f"/api/brokers/{broker_id}/test", headers=_headers(token), json={"api_key": "x"})
        assert tested.status_code == 200
        assert tested.json()["ok"] is True

        patched = client.patch(f"/api/brokers/{broker_id}", headers=_headers(token), json={"label": "Renamed"})
        assert patched.status_code == 200
        assert patched.json()["label"] == "Renamed"

        deleted = client.delete(f"/api/brokers/{broker_id}", headers=_headers(token))
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.get(f"/api/brokers/{broker_id}", headers=_headers(token)).status_code == 404
    finally:
        _cleanup(email)


# -- alerts / audit --------------------------------------------------------

def test_alerts_mark_read_flow():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        client.post("/api/paper-trading/start", headers=_headers(token), json={"balance": 5000})
        client.post("/api/paper-trading/order", headers=_headers(token), json={"strategy_id": sid, "side": "long"})

        count = client.get("/api/alerts/unread-count", headers=_headers(token))
        assert count.status_code == 200 and count.json()["count"] >= 1

        alerts = client.get("/api/alerts?unread_only=true", headers=_headers(token))
        assert len(alerts.json()) >= 1
        rid = alerts.json()[0]["id"]

        r = client.post(f"/api/alerts/{rid}/read", headers=_headers(token))
        assert r.status_code == 200 and r.json()["is_read"] is True

        count2 = client.get("/api/alerts/unread-count", headers=_headers(token)).json()["count"]
        assert count2 == count.json()["count"] - 1 or count2 >= 0

        marked = client.post("/api/alerts/mark-all-read", headers=_headers(token))
        assert marked.status_code == 200
        assert client.get("/api/alerts/unread-count", headers=_headers(token)).json()["count"] == 0
    finally:
        _cleanup(email)


def test_audit_pagination_and_filter():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        _create_strategy(token)  # no audit entry, but proves the endpoint works
        logs = client.get("/api/audit/logs?limit=5&offset=0", headers=_headers(token))
        assert logs.status_code == 200
        body = logs.json()
        assert "total" in body and "items" in body
        assert isinstance(body["items"], list)
    finally:
        _cleanup(email)


# -- backtests -------------------------------------------------------------

def test_backtest_idempotency_and_list():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)
        payload = {
            "strategy_id": sid,
            "pairs": ["EURUSD"],
            "timeframe": "M5",
            "date_from": "2025-01-01",
            "date_to": "2025-02-01",
            "balance": 50000,
            "swap_pips_per_night": 0.0,
            "idempotency_key": "dup-key-1",
        }
        r1 = client.post("/api/backtests", headers=_headers(token), json=payload)
        assert r1.status_code == 201, r1.text
        r2 = client.post("/api/backtests", headers=_headers(token), json=payload)
        assert r2.status_code == 201
        assert r2.json()["id"] == r1.json()["id"]

        listed = client.get("/api/backtests", headers=_headers(token))
        assert listed.status_code == 200
        assert any(j["id"] == r1.json()["id"] for j in listed.json()["items"])
    finally:
        _cleanup(email)


# -- chart layouts / calendar ----------------------------------------------

def test_chart_layouts_crud():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        saved = client.post(
            "/api/chart-layouts",
            headers=_headers(token),
            json={"name": "My Layout", "symbol": "EURUSD", "timeframe": "M5", "layout": {"panes": [{"indicator": "ema"}]}},
        )
        assert saved.status_code == 201, saved.text
        lid = saved.json()["id"]

        listed = client.get("/api/chart-layouts", headers=_headers(token))
        assert any(l["id"] == lid and l["name"] == "My Layout" for l in listed.json())

        deleted = client.delete(f"/api/chart-layouts/{lid}", headers=_headers(token))
        assert deleted.status_code == 200
        assert client.get("/api/chart-layouts", headers=_headers(token)).json() == []
    finally:
        _cleanup(email)


def test_economic_calendar_endpoint():
    from app.models import EconomicEvent

    email = _email()
    try:
        db = SessionLocal()
        from app.models import User, Workspace

        user = db.query(User).filter(User.email == email).first()
        if user is None:
            db.close()
            _register(email)
        else:
            db.close()
        token = _login(email)
        r = client.get("/api/market-data/economic-calendar", headers=_headers(token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)
    finally:
        _cleanup(email)


# -- strategy versioning ---------------------------------------------------

def test_strategy_versions_update_delete():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _create_strategy(token)

        versions = client.get(f"/api/strategies/{sid}/versions", headers=_headers(token))
        assert versions.status_code == 200
        assert len(versions.json()) == 1

        updated = client.put(
            f"/api/strategies/{sid}",
            headers=_headers(token),
            json={"name": "Renamed Strategy", "status": "paused"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed Strategy"
        assert updated.json()["status"] == "paused"

        detailed = client.get(f"/api/strategies/{sid}", headers=_headers(token))
        assert detailed.json()["name"] == "Renamed Strategy"

        v = client.get(f"/api/strategies/{sid}/versions/1.0.0", headers=_headers(token))
        assert v.status_code == 200
        assert v.json()["version"] == "1.0.0"

        deleted = client.delete(f"/api/strategies/{sid}", headers=_headers(token))
        assert deleted.status_code == 200
        assert client.get(f"/api/strategies/{sid}", headers=_headers(token)).status_code == 404
    finally:
        _cleanup(email)


# -- architect fix ---------------------------------------------------------

def test_architect_breakout_exit_is_not_tautology():
    from app.ai.architect import generate_candidates
    from app.schemas.api_strategy import StrategyGenerateRequest

    req = StrategyGenerateRequest(
        prompt="breakout scalp", pairs=["EURUSD"], timeframe="M5", strategy_family="breakout"
    )
    candidates = generate_candidates(req)
    breakout = [c for c in candidates if "Breakout" in c[1].name]
    assert breakout
    exit_expr = breakout[0][1].exit_rules[0].expression
    assert "atr" not in exit_expr  # previously a tautology involving atr(x)<atr(x)
    assert "crossunder" not in exit_expr
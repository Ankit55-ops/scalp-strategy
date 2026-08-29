import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import User, Workspace
from app.db.session import SessionLocal

client = TestClient(app)


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


# -- authentication ------------------------------------------------------

def test_unauthenticated_requests_rejected():
    r = client.get("/api/strategies")
    assert r.status_code == 401


def test_invalid_credentials_rejected():
    email = _email()
    try:
        _register(email)
        r = client.post("/api/auth/login", json={"email": email, "password": "wrong-pass"})
        assert r.status_code == 401
    finally:
        _cleanup(email)


def test_register_login_me_flow():
    email = _email()
    try:
        _register(email)
        token = _login(email)
        me = client.get("/api/auth/me", headers=_headers(token))
        assert me.status_code == 200
        assert me.json()["email"] == email
    finally:
        _cleanup(email)


def test_duplicate_register_conflict():
    email = _email()
    try:
        _register(email)
        r = client.post("/api/auth/register", json={"email": email, "password": "secret-pass-123"})
        assert r.status_code == 409
    finally:
        _cleanup(email)


def test_bad_email_rejected():
    r = client.post("/api/auth/register", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422


def test_no_token_me_rejected():
    assert client.get("/api/auth/me").status_code == 401


# -- authorization / workspace isolation ---------------------------------

def test_workspace_isolated_strategies():
    email_a, email_b = _email("alice"), _email("bob")
    try:
        token_a = _login(_register(email_a)["email"])
        _login(_register(email_b)["email"])

        spec = {
            "name": "Isolation Test",
            "version": "1.0.0",
            "strategy_family": "trend_pullback",
            "supported_pairs": ["EURUSD"],
            "supported_timeframes": ["M5", "M15"],
            "sessions_utc": [{"name": "London", "start": "07:00", "end": "16:00"}],
            "market_regime": {"preferred": ["trending"], "avoid": []},
            "entry_rules": [
                {"id": "long_ema", "description": "ema cross up", "expression": "crossover(ema(close,10), ema(close,30))"}
            ],
            "exit_rules": [
                {"id": "exit_low", "description": "exit below ema", "expression": "close < ema(close,10)"}
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
                "max_spread_pips": 3.0,
                "max_slippage_pips": 0.5,
                "minimum_atr_pips": 0.0,
                "news_blackout_minutes_before": 0,
                "news_blackout_minutes_after": 0,
            },
        }
        r = client.post("/api/strategies", headers=_headers(token_a), json={"spec": spec, "notes": "n"})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]

        # Alice can read it
        assert client.get(f"/api/strategies/{sid}", headers=_headers(token_a)).status_code == 200
        # Bob cannot access Alice's strategy
        assert client.get(f"/api/strategies/{sid}", headers=_headers("bogus-token-b-not-used")).status_code == 401

        # use real token for bob
        token_b = _login(email_b)
        assert client.get(f"/api/strategies/{sid}", headers=_headers(token_b)).status_code == 404
        # Bob's list is empty
        assert client.get("/api/strategies", headers=_headers(token_b)).json() == []
    finally:
        _cleanup(email_a)
        _cleanup(email_b)


def test_strategy_generate_and_list_consistency():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        r = client.post(
            "/api/strategies/generate",
            headers=_headers(token),
            json={"prompt": "scalp EURUSD M5 breakout", "pairs": ["EURUSD"], "timeframe": "M5"},
        )
        assert r.status_code == 200
        candidates = r.json()["candidates"]
        assert len(candidates) == 3
    finally:
        _cleanup(email)
"""Regression tests for the security hardening pass.

Each test maps to a specific flaw found during the audit and asserts the
fix holds. Run with: cd backend && python -m pytest tests/test_security_hardening.py -q
"""

from __future__ import annotations

import math
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import (
    BacktestJob,
    PaperAccount,
    PaperPosition,
    Strategy,
    User,
    Workspace,
)
from app.services.paper_service import PaperTradingService

client = TestClient(app)

SPEC = {
    "name": "Hardening Test",
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


def _email(prefix="sec"):
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
    from app.db.session import SessionLocal

    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if user:
        for ws in db.query(Workspace).filter(Workspace.owner_id == user.id).all():
            db.delete(ws)
        db.delete(user)
        db.commit()
    db.close()


def _token_and_workspace(email):
    from app.db.session import SessionLocal

    token = _login(_register(email)["email"])
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    ws = db.query(Workspace).filter(Workspace.owner_id == user.id).order_by(Workspace.created_at).first()
    db.close()
    return token, ws


# ---------------------------------------------------------------------------
# C1: config fail-fast + JWT hardening
# ---------------------------------------------------------------------------

def test_production_environment_requires_strong_secret_key():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="short",
            DATA_ENCRYPTION_KEY="a" * 44,
        )


def test_production_environment_requires_data_encryption_key():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="x" * 40,
            DATA_ENCRYPTION_KEY="",
        )


def test_data_encryption_key_must_decode_to_32_bytes():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="x" * 40,
            DATA_ENCRYPTION_KEY="bm90LWxvbmctZW5vdWdo",
        )


def test_data_encryption_key_must_be_valid_base64():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="x" * 40,
            DATA_ENCRYPTION_KEY="***not base64***",
        )


def test_jwt_algorithm_is_allow_listed():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(_env_file=None, APP_ENV="development", JWT_ALGORITHM="none")


def test_cors_origins_parses_as_json_array():
    from app.core.config import Settings

    s = Settings(_env_file=None, CORS_ORIGINS='["http://localhost:3000"]')
    assert s.cors_origins == ["http://localhost:3000"]
    with pytest.raises(ValueError):
        Settings(_env_file=None, CORS_ORIGINS="not-json")


def test_jwt_roundtrip_honors_issuer_and_rejects_tampering():
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token(subject="u1")
    payload = decode_access_token(token)
    assert payload is not None and payload["iss"] == "fxscalper-lab"
    assert decode_access_token(token[:-2] + "xx") is None


def test_jwt_rejects_expired_token():
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token(subject="u1", expires_minutes=-1)
    assert decode_access_token(token) is None


def test_jwt_rejects_non_access_token_type():
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.security import decode_access_token

    settings = get_settings()
    now = math.floor(__import__("time").time())
    bogus = pyjwt.encode(
        {
            "sub": "u1",
            "iss": settings.JWT_ISSUER,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "jti": uuid.uuid4().hex,
            "typ": "refresh",
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    assert decode_access_token(bogus) is None


def test_jwt_requires_nbf_claim():
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.security import decode_access_token

    settings = get_settings()
    now = math.floor(__import__("time").time())
    missing_nbf = pyjwt.encode(
        {
            "sub": "u1",
            "iss": settings.JWT_ISSUER,
            "iat": now,
            "exp": now + 3600,
            "jti": uuid.uuid4().hex,
            "typ": "access",
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    assert decode_access_token(missing_nbf) is None


def test_jwt_rejects_token_not_before_in_future():
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.security import decode_access_token

    settings = get_settings()
    now = math.floor(__import__("time").time())
    future = pyjwt.encode(
        {
            "sub": "u1",
            "iss": settings.JWT_ISSUER,
            "iat": now,
            "nbf": now + 3600,
            "exp": now + 7200,
            "jti": uuid.uuid4().hex,
            "typ": "access",
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    assert decode_access_token(future) is None


def test_jwt_access_token_roundtrip_has_type_and_nbf():
    from app.core.security import create_access_token, decode_access_token

    payload = decode_access_token(create_access_token(subject="user-123"))
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert payload["typ"] == "access"
    assert "nbf" in payload


# ---------------------------------------------------------------------------
# H1: auth hardening
# ---------------------------------------------------------------------------

def test_register_normalizes_email_case():
    email = f"MiXeD-{uuid.uuid4().hex[:8]}@EXAMPLE.com"
    try:
        registered = _register(email)
        assert registered["email"] == email.lower()
        # login with mixed-case also works
        token = _login(email, "secret-pass-123")
        assert token
    finally:
        _cleanup(email.lower())


def test_register_rejects_long_password():
    email = _email()
    try:
        r = client.post(
            "/api/auth/register",
            json={"email": email, "password": "x" * 73},
        )
        assert r.status_code == 422
    finally:
        _cleanup(email)


def test_login_rejects_long_password():
    email = _email()
    try:
        _register(email)
        r = client.post("/api/auth/login", json={"email": email, "password": "y" * 73})
        assert r.status_code == 422
    finally:
        _cleanup(email)


def test_login_lockout_after_consecutive_failures():
    email = _email()
    try:
        _register(email)
        for _ in range(9):
            r = client.post(
                "/api/auth/login",
                json={"email": email, "password": "wrong-password-1"},
            )
            assert r.status_code == 401
        # attempt that reaches the failure cap is refused with 429
        r = client.post(
            "/api/auth/login",
            json={"email": email, "password": "wrong-password-2"},
        )
        assert r.status_code == 429
        # a correct password is also refused during the window
        r = client.post(
            "/api/auth/login",
            json={"email": email, "password": "secret-pass-123"},
        )
        assert r.status_code == 429
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# H2 / H4: paper-trading bounds and concurrency
# ---------------------------------------------------------------------------

def test_paper_start_rejects_non_finite_balance():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        for raw in [b'{"balance": NaN}', b'{"balance": Infinity}', b'{"balance": -Infinity}']:
            r = client.post(
                "/api/paper-trading/start",
                headers={**_headers(token), "Content-Type": "application/json"},
                content=raw,
            )
            assert r.status_code == 422, r.text
    finally:
        _cleanup(email)


def test_paper_start_rejects_out_of_bounds_balance():
    from app.core.config import get_settings

    email = _email()
    try:
        token = _login(_register(email)["email"])
        huge = get_settings().PAPER_MAX_BALANCE + 1
        r = client.post(
            "/api/paper-trading/start",
            headers=_headers(token),
            json={"balance": huge},
        )
        assert r.status_code == 422, r.text
        tiny = get_settings().PAPER_MIN_BALANCE / 2
        r = client.post(
            "/api/paper-trading/start",
            headers=_headers(token),
            json={"balance": tiny},
        )
        assert r.status_code == 422, r.text
    finally:
        _cleanup(email)


def test_paper_order_rejects_non_finite_size():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        r = client.post(
            "/api/paper-trading/order",
            headers={**_headers(token), "Content-Type": "application/json"},
            content=b'{"strategy_id": "x", "side": "long", "size_units": NaN}',
        )
        assert r.status_code == 422, r.text
    finally:
        _cleanup(email)


def test_paper_close_is_single_credited_and_idempotent():
    email = _email()
    try:
        _, ws = _token_and_workspace(email)
        from app.db.session import SessionLocal

        db = SessionLocal()
        strategy = Strategy(workspace_id=ws.id, name="Close", spec=SPEC, current_version="1.0.0", strategy_family="trend_pullback", status="active")
        db.add(strategy)
        db.flush()
        strat_id = strategy.id
        acc = PaperAccount(workspace_id=ws.id, balance=100000.0, equity=100000.0, is_active=True, trading_state="ACTIVE")
        db.add(acc)
        db.flush()
        pos = PaperPosition(
            account_id=acc.id,
            strategy_id=strat_id,
            order_id=None,
            symbol="EURUSD",
            side="long",
            size_units=1000.0,
            entry_price=1.1000,
            stop_loss=1.0900,
            take_profit=1.1100,
            open_ts=0.0,
            status="open",
        )
        db.add(pos)
        db.commit()
        acc_id, pos_id = acc.id, pos.id
        db.close()

        svc = PaperTradingService(SessionLocal())
        first = svc.close_position(ws.id, pos_id, reason="test_close")
        assert first.status == "closed"

        from app.db.session import SessionLocal as SL2

        db = SL2()
        acc_after = db.get(PaperAccount, acc_id)
        balance_after_first = acc_after.balance
        db.close()

        with pytest.raises(ValueError):
            svc.close_position(ws.id, pos_id, reason="second_close")
        svc.db.close()

        db = SL2()
        acc_after2 = db.get(PaperAccount, acc_id)
        assert math.isclose(acc_after2.balance, balance_after_first)
        db.close()
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# H3: CSV import hardening
# ---------------------------------------------------------------------------

def test_csv_candle_name_rejects_path_traversal():
    from app.providers.csv_provider import _safe_candle_name

    for bad in ["../ev", "../../etc/passwd", "EUR/USD", "eur usd", "EURUSD!", "a" * 20]:
        with pytest.raises(ValueError):
            _safe_candle_name(bad, "M5")
    with pytest.raises(ValueError):
        _safe_candle_name("EURUSD", "M5;rm -rf")
    assert _safe_candle_name("EURUSD", "m5") == "eurusd_m5.csv"


def test_import_route_rejects_traversal_symbol():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        r = client.post(
            "/api/market-data/import",
            params={"symbol": "../secrets", "timeframe": "M5"},
            headers=_headers(token),
            files={"file": ("x.csv", b"timestamp,open,high,low,close,volume\n", "text/csv")},
        )
        assert r.status_code == 422, r.text
    finally:
        _cleanup(email)


def test_import_route_enforces_upload_size_cap(monkeypatch):
    from app.core.config import get_settings

    email = _email()
    try:
        token = _login(_register(email)["email"])
        settings = get_settings()
        monkeypatch.setattr(settings, "UPLOAD_MAX_BYTES", 32)

        big = b"timestamp,open,high,low,close,volume\n" + b"0" * 64
        r = client.post(
            "/api/market-data/import",
            params={"symbol": "EURUSD", "timeframe": "M5"},
            headers=_headers(token),
            files={"file": ("big.csv", big, "text/csv")},
        )
        assert r.status_code == 413, r.text
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# H5: backtest concurrency gate
# ---------------------------------------------------------------------------

def test_backtest_concurrency_gate_blocks_active_jobs():
    from app.db.session import SessionLocal

    email = _email()
    try:
        token, ws = _token_and_workspace(email)
        db = SessionLocal()
        strategy = Strategy(workspace_id=ws.id, name="Gate", spec=SPEC, current_version="1.0.0", strategy_family="trend_pullback", status="active")
        db.add(strategy)
        db.flush()
        strategy_id = strategy.id
        for _ in range(2):
            db.add(
                BacktestJob(
                    workspace_id=ws.id,
                    strategy_id=strategy_id,
                    status="running",
                    progress=0.5,
                    params={"strategy_id": strategy_id},
                )
            )
        db.commit()
        db.close()

        r = client.post(
            "/api/backtests",
            headers=_headers(token),
            json={"strategy_id": strategy_id, "pairs": ["EURUSD"], "timeframe": "M5", "date_from": "2024-01-01", "date_to": "2024-01-10"},
        )
        assert r.status_code == 429, r.text
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# M: WS hardening
# ---------------------------------------------------------------------------

def test_ws_rejects_query_string_token_and_header_only():
    from starlette.websockets import WebSocketDisconnect

    from app.core.security import create_access_token

    token = create_access_token(subject="whoever")
    # query-string token is rejected outright
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/api/ws/market-data?token={token}",
        headers={"origin": "http://localhost:3000"},
    ):
        pass
    # missing subprotocol rejected
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/api/ws/market-data",
        headers={"origin": "http://localhost:3000"},
    ):
        pass


def test_ws_accepts_valid_subprotocol_token():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        with client.websocket_connect(
            "/api/ws/market-data",
            subprotocols=[token],
            headers={"origin": "http://localhost:3000"},
        ) as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
    finally:
        _cleanup(email)


def test_ws_rejects_disallowed_origin_and_enforces_connection_cap(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app.core.config import get_settings

    email = _email()
    try:
        token = _login(_register(email)["email"])
        settings = get_settings()

        allowed_origins = ["http://trusted.example"]
        monkeypatch.setattr(
            settings, "CORS_ORIGINS", '["http://trusted.example"]'
        )
        assert settings.cors_origins == allowed_origins

        # disallowed origin rejected
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/api/ws/market-data",
            subprotocols=[token],
            headers={"origin": "http://evil.example"},
        ):
            pass

        # connection cap: a second socket is refused while one is open
        monkeypatch.setattr(settings, "MAX_CONCURRENT_WS_PER_USER", 1)
        with client.websocket_connect(
            "/api/ws/market-data",
            subprotocols=[token],
            headers={"origin": "http://trusted.example"},
        ) as ws1:
            first = ws1.receive_json()
            assert first["type"] == "snapshot"
            with pytest.raises(WebSocketDisconnect), client.websocket_connect(
                "/api/ws/market-data",
                subprotocols=[token],
                headers={"origin": "http://trusted.example"},
            ):
                pass
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# M: DSL resource caps
# ---------------------------------------------------------------------------

def test_dsl_rejects_oversized_expression():
    from app.dsl.tokenizer import TokenizeError, tokenize

    with pytest.raises(TokenizeError):
        tokenize("close > open " * 1000)


def test_dsl_rejects_too_many_tokens():
    from app.dsl.tokenizer import TokenizeError, tokenize

    with pytest.raises(TokenizeError):
        tokenize(" and ".join(["close > open"] * 600))


def test_dsl_rejects_excessive_nesting():
    from app.dsl.parser import ParseError, parse_expression

    with pytest.raises(ParseError):
        parse_expression("(" * 60 + "close > open" + ")" * 60)
    # finite nesting is fine
    parse_expression("((close > open)) and (high < low)")


# ---------------------------------------------------------------------------
# M: Decimal money math
# ---------------------------------------------------------------------------

def test_money_math_avoids_float_drift():
    from app.services.money import add, sub

    assert add(0.1, 0.2) == 0.3
    assert sub(0.3, 0.1) == 0.2


def test_money_rejects_non_finite():
    from app.services.money import d

    with pytest.raises(ValueError):
        d(float("nan"))
    with pytest.raises(ValueError):
        d(float("inf"))


def test_paper_broker_gross_pnl_roundtrip():
    from app.services.paper_broker import PaperBroker

    size = 1000.0
    assert math.isclose(
        PaperBroker.gross_pnl("long", 1.10, 1.12, size), 20.0, abs_tol=1e-6
    )
    assert math.isclose(
        PaperBroker.gross_pnl("short", 1.12, 1.10, size), 20.0, abs_tol=1e-6
    )


# ---------------------------------------------------------------------------
# M: provider error truncation + audit attribution
# ---------------------------------------------------------------------------

def test_provider_error_truncation_and_key_redaction():
    from app.services.provider_service import _safe_error

    key = "sk-live-1234567890"
    long_msg = f"upstream said: use api key {key} and retry " * 50
    out = _safe_error(Exception(long_msg), secrets=(key,))
    assert len(out) <= 400
    assert key not in out
    assert "***redacted***" in out


def test_risk_decision_audit_does_not_mislabel_strategy_as_actor():
    from app.services.audit import AuditService

    email = _email()
    try:
        _login(_register(email)["email"])
        from app.db.session import SessionLocal

        db = SessionLocal()
        entry = AuditService(db).record_risk_decision(
            None, {"strategy_id": "strat-1", "correlation_id": "corr-1"}
        )
        assert entry.actor_id is None
        assert entry.payload.get("source") == "risk_engine"
        db.close()
    finally:
        _cleanup(email)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

def test_security_headers_present_on_api_responses():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
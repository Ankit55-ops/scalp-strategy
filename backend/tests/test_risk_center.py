"""Tests for the risk-center fixes (workspace-scoped kill switches, real
engine state, risk-profile management) and the strategy-check system."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MIN_SPEC = {
    "name": "CheckMe",
    "version": "1.0.0",
    "strategy_family": "momentum",
    "supported_pairs": ["EURUSD"],
    "supported_timeframes": ["M5"],
    "sessions_utc": [{"name": "London", "start": "00:00", "end": "23:59"}],
    "market_regime": {"preferred": [], "avoid": []},
    "indicators": [{"name": "EMA", "parameters": {"period": 20}}],
    "entry_rules": [
        {"id": "long_1", "description": "momentum", "expression": "close > sma(close, 20) and rsi(close, 14) > 50"}
    ],
    "exit_rules": [
        {"id": "exit_1", "description": "momentum fade", "expression": "close < sma(close, 20) or rsi(close, 14) > 70"}
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
        "max_spread_pips": 1.2,
        "max_slippage_pips": 0.5,
        "minimum_atr_pips": 0.0,
        "news_blackout_minutes_before": 0,
        "news_blackout_minutes_after": 0,
    },
}


def _email():
    return f"risk-{uuid.uuid4().hex[:8]}@example.com"


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


def _strategy(token, spec=None):
    r = client.post("/api/strategies", headers=_h(token), json={"spec": spec or MIN_SPEC})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# -- kill switches ---------------------------------------------------------

def test_kill_switch_scoped_status_and_isolation():
    a, b = _email(), _email()
    try:
        ta = _login(_register(a)["email"])
        tb = _login(_register(b)["email"])

        engaged = client.post(
            "/api/risk/kill-switch",
            headers=_h(ta),
            json={"scope": "global", "resource_id": "global", "enabled": True, "reason": "E2E"},
        )
        assert engaged.status_code == 200, engaged.text

        pair = client.post(
            "/api/risk/kill-switch",
            headers=_h(ta),
            json={"scope": "pair", "resource_id": "EURUSD", "enabled": True, "reason": "pair halt"},
        )
        assert pair.status_code == 200

        sid = _strategy(ta)
        strat = client.post(
            "/api/risk/kill-switch",
            headers=_h(ta),
            json={"scope": "strategy", "resource_id": sid, "enabled": True, "reason": "strat halt"},
        )
        assert strat.status_code == 200

        status = client.get("/api/risk/kill-switch", headers=_h(ta)).json()
        assert status["global"] is True
        assert status["pair"].get("EURUSD") is True
        assert status["strategy"].get(sid) is True

        engagements = client.get("/api/risk/kill-switch/engagements", headers=_h(ta)).json()
        assert len(engagements) == 3

        # other workspace is not affected
        other = client.get("/api/risk/kill-switch", headers=_h(tb)).json()
        assert other["global"] is False
        assert "EURUSD" not in other["pair"]

        # disarm global removes it
        dis = client.post(
            "/api/risk/kill-switch",
            headers=_h(ta),
            json={"scope": "global", "resource_id": "global", "enabled": False, "reason": "re-arm"},
        )
        assert dis.status_code == 200
        status2 = client.get("/api/risk/kill-switch", headers=_h(ta)).json()
        assert status2["global"] is False
        assert status2["pair"].get("EURUSD") is True
    finally:
        _cleanup(a)
        _cleanup(b)


def test_kill_switch_blocks_paper_order():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _strategy(token)
        r = client.post("/api/risk/profiles", headers=_h(token), json={"name": "p", "max_spread_pips": 5.0})
        assert r.status_code == 201, r.text
        client.post("/api/paper-trading/start", headers=_h(token), json={"balance": 10000})

        client.post(
            "/api/risk/kill-switch",
            headers=_h(token),
            json={"scope": "global", "resource_id": "global", "enabled": True, "reason": "halt all"},
        )
        placed = client.post("/api/paper-trading/order", headers=_h(token), json={"strategy_id": sid, "side": "long"})
        assert placed.status_code == 200
        assert placed.json()["approved"] is False
        assert "kill switch" in placed.json()["reason"]
    finally:
        _cleanup(email)


def test_risk_profile_crud_and_order_approval():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _strategy(token)

        # no profile -> rejected
        client.post("/api/paper-trading/start", headers=_h(token), json={"balance": 10000})
        placed = client.post("/api/paper-trading/order", headers=_h(token), json={"strategy_id": sid, "side": "long"})
        assert placed.json()["approved"] is False
        assert placed.json()["reason"] == "no risk profile"

        created = client.post(
            "/api/risk/profiles",
            headers=_h(token),
            json={"name": "Aggressive", "risk_per_trade_pct": 0.5, "max_open_positions": 2, "max_spread_pips": 5.0, "is_active": True},
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        assert created.json()["is_active"] is True

        second = client.post(
            "/api/risk/profiles",
            headers=_h(token),
            json={"name": "Conservative", "risk_per_trade_pct": 0.1, "is_active": False},
        )
        act = client.post(f"/api/risk/profiles/{second.json()['id']}/activate", headers=_h(token))
        assert act.status_code == 200 and act.json()["is_active"] is True

        # activating second deactivated first
        rows = client.get("/api/risk/profiles", headers=_h(token)).json()
        active = [p for p in rows if p["is_active"]]
        assert len(active) == 1 and active[0]["id"] == second.json()["id"]

        patched = client.patch(
            f"/api/risk/profiles/{pid}",
            headers=_h(token),
            json={"max_open_positions": 5, "name": "Aggressive v2", "is_active": True},
        )
        assert patched.status_code == 200
        assert patched.json()["max_open_positions"] == 5
        assert patched.json()["is_active"] is True
        rows = client.get("/api/risk/profiles", headers=_h(token)).json()
        assert len([p for p in rows if p["is_active"]]) == 1

        # now approved (profile exists and active has max_spread_pips 5)
        placed2 = client.post("/api/paper-trading/order", headers=_h(token), json={"strategy_id": sid, "side": "long"})
        assert placed2.status_code == 200
        assert placed2.json()["approved"] is True, placed2.text

        deleted = client.delete(f"/api/risk/profiles/{pid}", headers=_h(token))
        assert deleted.status_code == 200
    finally:
        _cleanup(email)


# -- strategy check --------------------------------------------------------

def test_strategy_check_flags_tautology_and_reports():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        bad_spec = dict(MIN_SPEC)
        bad_spec["entry_rules"] = [
            {"id": "taut", "description": "bad", "expression": "close > close"},
            {"id": "cross", "description": "bad", "expression": "crossover(sma(close, 20), sma(close, 20))"},
        ]
        sid = _strategy(token, bad_spec)

        r = client.post(f"/api/strategies/{sid}/check", headers=_h(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["strategy_id"] == sid
        assert body["overall"] == "fail"
        checks = body["checks"]
        assert any(c["severity"] == "fail" and "tautological" in c["detail"] for c in checks)
        assert any(c["check"] == "backtest" and c["severity"] == "info" for c in checks)
    finally:
        _cleanup(email)


def test_strategy_check_clean_spec_not_failing():
    email = _email()
    try:
        token = _login(_register(email)["email"])
        sid = _strategy(token)
        r = client.post(f"/api/strategies/{sid}/check", headers=_h(token))
        assert r.status_code == 200
        body = r.json()
        assert body["overall"] in ("pass", "warn")
        assert not any(c["severity"] == "fail" for c in body["checks"])
    finally:
        _cleanup(email)


# -- risk engine: weekly loss + drawdown -----------------------------------

def test_engine_weekly_loss_and_drawdown_gates():
    from app.risk.engine import ProposedOrder, RiskEngine
    from app.risk.killswitch import KillSwitchRegistry
    from app.models import RiskProfile

    profile = RiskProfile(
        workspace_id="w",
        name="p",
        risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_weekly_loss_pct=2.0,
        max_drawdown_pct=5.0,
        max_consecutive_losses=3,
        max_open_positions=5,
        max_trades_per_day=100,
        max_correlated_exposure_pct=2.0,
        max_spread_pips=5.0,
        max_slippage_pips=0.5,
        news_blackout_minutes_before=0,
        news_blackout_minutes_after=0,
        is_active=True,
    )

    def order(equity):
        return ProposedOrder(
            symbol="EURUSD",
            side="buy",
            size_units=1000.0,
            entry_price=1.10,
            stop_price=1.099,
            account_balance=100000.0,
            account_equity=equity,
            spread_pips=0.8,
            ts=0.0,
        )

    ks = KillSwitchRegistry()

    # weekly loss: day flat, equity down 3% vs week start 100k (2% limit breached)
    engine = RiskEngine(ks, profile)
    engine._day_start_equity = 94000.0
    engine._week_start_equity = 100000.0
    engine._peak_equity = 100000.0
    d1 = engine.evaluate(order(97000.0))
    assert d1.approved is False
    assert any(c.check == "weekly_loss_limit" and not c.passed for c in d1.checks)

    # drawdown: day flat, weekly baseline below equity, but 6% under the 100k peak
    engine2 = RiskEngine(ks, profile)
    engine2._day_start_equity = 94000.0
    engine2._week_start_equity = 93000.0
    engine2._peak_equity = 100000.0
    d2 = engine2.evaluate(order(94000.0))
    assert d2.approved is False
    assert any(c.check == "max_drawdown" and not c.passed for c in d2.checks)


def _cleanup(email):
    from app.db.session import SessionLocal
    from app.models import User, Workspace

    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if user:
        for ws in db.query(Workspace).filter(Workspace.owner_id == user.id).all():
            db.delete(ws)
        db.delete(user)
        db.commit()
    db.close()
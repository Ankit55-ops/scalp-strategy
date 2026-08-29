"""Tests for the real market-data provider upgrade: normalization, paper
broker pricing, security (no key leakage), and stale-feed risk blocking."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.oanda import OandaMarketDataProvider
from app.providers.twelvedata import TwelveDataMarketDataProvider
from app.providers.models import build_candle, build_quote
from app.services import feed_health
from app.services.paper_broker import PaperBroker, _pip
from app.services.provider_service import ProviderConnectionError, get_active_provider

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
    return f"md-{uuid.uuid4().hex[:8]}@example.com"


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
    token = _login(email)
    return email, token


def _strategy(token, spec=None):
    r = client.post("/api/strategies", json={"name": "MarketDataCheck", "spec": spec or MIN_SPEC}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ===========================================================================
# symbol normalization
# ===========================================================================
def test_oanda_symbol_normalization():
    assert OandaMarketDataProvider.to_canonical("EUR_USD") == "EURUSD"
    assert OandaMarketDataProvider.to_canonical("USD_JPY") == "USDJPY"
    assert OandaMarketDataProvider.to_provider("eurusd") == "EURUSD"
    assert OandaMarketDataProvider.to_provider("EUR/USD") == "EURUSD"


def test_twelvedata_symbol_normalization():
    assert TwelveDataMarketDataProvider.to_canonical("EUR/USD") == "EURUSD"
    assert TwelveDataMarketDataProvider.to_canonical("EURUSD") == "EURUSD"
    assert TwelveDataMarketDataProvider.to_provider("USDJPY") == "USD/JPY"


# ===========================================================================
# quote / candle normalization and spread math
# ===========================================================================
def test_quote_normalization_fields():
    q = build_quote("EURUSD", 1.1, 1.10008, ts=1000.0, source="test")
    assert q["symbol"] == "EURUSD"
    assert q["mid"] > q["bid"] and q["mid"] < q["ask"]
    assert abs(q["spread_price"] - 0.00008) < 1e-9
    assert abs(q["spread_pips"] - 0.8) < 1e-6
    assert q["timestamp_utc"] == "1970-01-01T00:16:40+00:00"
    assert q["is_stale"] is False


def test_spread_in_pips_jpy_vs_non_jpy():
    jpy = build_quote("USDJPY", 150.0, 150.05, ts=1000.0, source="test")
    assert abs(jpy["spread_pips"] - 5.0) < 1e-6
    eur = build_quote("EURUSD", 1.1000, 1.1005, ts=1000.0, source="test")
    assert abs(eur["spread_pips"] - 5.0) < 1e-6


def test_candle_normalization_fields():
    c = build_candle("EURUSD", "M5", 1000.0, 1.1, 1.2, 1.05, 1.15, 10, source="oanda", is_complete=False)
    assert c["symbol"] == "EURUSD"
    assert c["is_complete"] is False
    assert c["bid_ask_basis"] == "mid"
    assert c["source"] == "oanda"
    assert c["volume"] == 10.0
    assert c["close_time_utc"] > c["open_time_utc"]


def test_paper_broker_pip_inference():
    assert abs(_pip({"symbol": "EURUSD"}) - 0.0001) < 1e-12
    assert abs(_pip({"symbol": "USDJPY"}) - 0.01) < 1e-12
    # derived from spread_price / spread_pips
    q = build_quote("EURUSD", 1.1, 1.1001, ts=1000.0, source="test")
    assert abs(_pip(q) - 0.0001) < 1e-12


# ===========================================================================
# paper broker bid/ask pricing + costs
# ===========================================================================
def _quote(symbol="EURUSD", bid=1.1, ask=1.10008):
    return build_quote(symbol, bid, ask, ts=datetime.now(timezone.utc).timestamp(), source="test")


def test_long_entry_pays_ask_plus_slippage():
    broker = PaperBroker(slippage_pips=1.0)
    q = _quote(bid=1.1, ask=1.10008)
    assert broker.entry_price(q, "long") == pytest.approx(1.10008 + 0.0001, abs=1e-9)


def test_short_entry_pays_bid_minus_slippage():
    broker = PaperBroker(slippage_pips=1.0)
    q = _quote(bid=1.1, ask=1.10008)
    assert broker.entry_price(q, "short") == pytest.approx(1.1 - 0.0001, abs=1e-9)


def test_long_exit_receives_bid_minus_slippage():
    broker = PaperBroker(slippage_pips=1.0)
    q = _quote(bid=1.101, ask=1.10108)
    assert broker.exit_price(q, "long") == pytest.approx(1.101 - 0.0001, abs=1e-9)


def test_short_exit_pays_ask_plus_slippage():
    broker = PaperBroker(slippage_pips=1.0)
    q = _quote(bid=1.101, ask=1.10108)
    assert broker.exit_price(q, "short") == pytest.approx(1.10108 + 0.0001, abs=1e-9)


def test_slippage_commission_net_pnl():
    broker = PaperBroker(slippage_pips=0.5, commission_per_lot=7.0)
    q = _quote(bid=1.1, ask=1.10008)
    size = 10000.0
    entry = broker.entry_price(q, "long")
    exit = broker.exit_price(_quote(bid=1.101, ask=1.10108), "long")
    gross = broker.gross_pnl("long", entry, exit, size)
    costs = broker.costs(q, "long", size)
    net = gross - costs.total
    assert gross == pytest.approx(((1.101 - 0.00005) - (1.10008 + 0.00005)) * size, abs=1e-6)
    assert costs.spread_cost == pytest.approx(0.00008 * size, abs=1e-9)
    assert costs.slippage_cost == pytest.approx(0.00005 * size, abs=1e-9)
    assert costs.commission == pytest.approx((size / 100000) * 7.0 * 2, abs=1e-9)
    assert net < gross


# ===========================================================================
# feed health / staleness
# ===========================================================================
def test_feed_state_live_then_stale_via_threshold(monkeypatch):
    monkeypatch.setattr(feed_health, "stale_threshold_seconds", lambda: 0.1)
    ws, provider, symbol = "ws", "mock", "EURUSD"
    feed_health.mark_quote_seen(ws, provider, symbol, time.time())
    assert feed_health.feed_state(ws, provider, symbol, market_status="open") == "LIVE"
    feed_health.mark_quote_seen(ws, provider, symbol, time.time() - 60)
    assert feed_health.feed_state(ws, provider, symbol, market_status="open") == "STALE"


def test_feed_state_closed_market_is_maintenance_not_stale(monkeypatch):
    monkeypatch.setattr(feed_health, "stale_threshold_seconds", lambda: 0.1)
    ws, provider, symbol = "ws2", "mock", "EURUSD"
    feed_health.mark_quote_seen(ws, provider, symbol, time.time() - 60)
    assert feed_health.feed_state(ws, provider, symbol, market_status="closed") == "MAINTENANCE"


def test_get_quote_marks_stale_flag(monkeypatch):
    class AncientProvider:
        name = "ancient"
        bid_ask_basis = "mid"

        def get_latest_quote(self, symbol):
            return build_quote(symbol, 1.1, 1.1001, ts=time.time() - 600, source="ancient")

    monkeypatch.setattr(feed_health, "get_active_provider", lambda db, ws: AncientProvider())
    monkeypatch.setattr(feed_health, "stale_threshold_seconds", lambda: 0.1)
    monkeypatch.setattr(feed_health, "_persist_health", lambda *a, **k: None)
    q = feed_health.get_quote(None, "wsX", "EURUSD")
    assert q["is_stale"] is True
    assert q["feed_state"] == "STALE"


# ===========================================================================
# credential security + provider fallback
# ===========================================================================
def test_active_provider_falls_back_to_mock(db_session):
    provider = get_active_provider(db_session, "no-such-workspace")
    assert provider.name == "mock"


def test_connect_error_never_leaks_key(monkeypatch):
    email, token = _login_setup()
    bad_key = "super-secret-oanda-key-per-test"

    def fake_build(provider, api_key=None, account_id=None, env=None):
        raise ProviderConnectionError("connection rejected")

    monkeypatch.setattr("app.services.provider_service.build_provider", fake_build)
    r = client.post(
        "/api/market-data/providers/connect",
        json={"provider": "oanda", "api_key": bad_key, "account_id": "000-000-000"},
        headers=_h(token),
    )
    assert r.status_code == 422, r.text
    st = client.get("/api/market-data/providers/status", headers=_h(token))
    assert st.status_code == 200
    body = st.text
    assert bad_key not in body
    assert "encrypted_secret" not in body
    assert "api_key" not in body


# ===========================================================================
# stale feed blocks paper orders (risk integration)
# ===========================================================================
def test_paper_order_blocked_when_feed_stale(monkeypatch):
    email, token = _login_setup()
    sid = _strategy(token)
    assert client.post("/api/paper-trading/start", json={"balance": 100000.0}, headers=_h(token)).status_code == 201

    from app.services import paper_service as ps

    def stale_get_quote(db, workspace_id, symbol, mark_stale=True):
        q = build_quote(symbol, 1.1, 1.1001, ts=time.time() - 120, source="old")
        q["is_stale"] = True
        q["feed_state"] = "STALE"
        q["market_status"] = "open"
        q["spread_pips"] = 0.8
        return q

    monkeypatch.setattr(ps.feed_health, "get_quote", stale_get_quote)
    r = client.post(
        "/api/paper-trading/order",
        json={"strategy_id": sid, "side": "long"},
        headers=_h(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["approved"] is False
    assert "not fresh" in body["reason"]


def test_global_kill_switch_blocks_paper_order():
    email, token = _login_setup()
    sid = _strategy(token)
    client.post("/api/paper-trading/start", json={"balance": 100000.0}, headers=_h(token))
    client.post("/api/risk/kill-switch", json={"scope": "global", "resource_id": "global", "enabled": True, "reason": "test"}, headers=_h(token))
    r = client.post("/api/paper-trading/order", json={"strategy_id": sid, "side": "long"}, headers=_h(token))
    assert r.status_code == 200
    assert r.json()["approved"] is False
    assert "kill switch" in r.json()["reason"].lower()
    client.post("/api/risk/kill-switch", json={"scope": "global", "resource_id": "global", "enabled": False, "reason": "re-arm"}, headers=_h(token))


def test_paper_order_rejected_on_excessive_spread():
    email, token = _login_setup()
    sid = _strategy(token)
    client.post("/api/paper-trading/start", json={"balance": 100000.0}, headers=_h(token))
    client.post(
        "/api/risk/profiles",
        json={"name": "Tight", "max_spread_pips": 0.05, "risk_per_trade_pct": 0.25, "is_active": True},
        headers=_h(token),
    )
    r = client.post("/api/paper-trading/order", json={"strategy_id": sid, "side": "long"}, headers=_h(token))
    assert r.status_code == 200
    assert r.json()["approved"] is False
    assert "spread" in r.json()["reason"].lower()


# ===========================================================================
# strategy check live-data audit events
# ===========================================================================
def test_strategy_check_reports_live_blocked_on_stale_feed(monkeypatch):
    email, token = _login_setup()
    sid = _strategy(token)

    from app.services import feed_health as fh

    class StaleOnlyProvider:
        name = "mock"
        bid_ask_basis = "mid"

        def list_symbols(self):
            return ["EURUSD"]

        def get_latest_quote(self, symbol):
            q = build_quote(symbol, 1.1, 1.1001, ts=time.time() - 120, source="mock")
            q["spread_pips"] = 0.8
            q["market_status"] = "open"
            return q

    # Both the route's and feed_health's own references must see the stub.
    monkeypatch.setattr("app.services.provider_service.get_active_provider", lambda db, ws: StaleOnlyProvider())
    monkeypatch.setattr(fh, "get_active_provider", lambda db, ws: StaleOnlyProvider())
    monkeypatch.setattr(fh, "stale_threshold_seconds", lambda: 0.1)

    r = client.post(f"/api/strategies/{sid}/check", headers=_h(token))
    assert r.status_code == 200, r.text
    report = r.json()
    live = next(c for c in report["checks"] if c["check"] == "live_data")
    assert live["severity"] == "fail"
    assert "blocked" in live["detail"].lower()


def test_strategy_check_reports_live_healthy_with_mock_feed():
    email, token = _login_setup()
    sid = _strategy(token)
    r = client.post(f"/api/strategies/{sid}/check", headers=_h(token))
    assert r.status_code == 200, r.text
    report = r.json()
    checks = {c["check"]: c for c in report["checks"]}
    assert "live_data" in checks
    assert checks["live_data"]["severity"] in ("pass", "info", "warn")


# ===========================================================================
# helper fixtures
# ===========================================================================
@pytest.fixture
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    yield session
    session.close()
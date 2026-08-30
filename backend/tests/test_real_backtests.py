"""Tests for the /api/real-backtests/* endpoints (AI Strategy Tester data source).

Reuses the real-data validation engine: connected provider required (no silent
mock fallback), immutable strategy version, Data Quality Gate, bid/ask-aware
execution, chart payload with indicator overlays + trade markers + gaps, and
cancel. Also checks the chart overlay computation against the strategy spec.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MIN_SPEC = {
    "name": "RBStrategy",
    "version": "1.0.0",
    "strategy_family": "trend_pullback",
    "supported_pairs": ["EURUSD"],
    "supported_timeframes": ["M5"],
    "sessions_utc": [{"name": "Full", "start": "00:00", "end": "23:59"}],
    "market_regime": {"preferred": ["trending"], "avoid": []},
    "indicators": [
        {"name": "EMA", "parameters": {"period": 6}},
        {"name": "EMA", "parameters": {"period": 12}},
        {"name": "ATR", "parameters": {"period": 14}},
    ],
    "entry_rules": [
        {
            "id": "long_1",
            "description": "pullback to fast EMA while trend up",
            "expression": "ema(close,12) > ema(close,6) and low <= ema(close,6) and close > ema(close,6)",
        },
        {
            "id": "short_1",
            "description": "pullback to fast EMA while trend down",
            "expression": "ema(close,12) < ema(close,6) and high >= ema(close,6) and close < ema(close,6)",
        },
    ],
    "exit_rules": [
        {"id": "exit_1", "description": "exit when price crosses back", "expression": "close < ema(close,6)"}
    ],
    "risk_management": {
        "risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_consecutive_losses": 5,
        "max_open_positions": 1,
        "max_trades_per_day": 20,
        "stop_loss_method": "ATR",
        "stop_loss_parameters": {"atr_period": 14, "atr_multiplier": 1.2},
        "take_profit_method": "risk_reward",
        "take_profit_parameters": {"risk_reward_ratio": 1.5},
    },
    "execution_filters": {
        "max_spread_pips": 5.0,
        "max_slippage_pips": 1.0,
        "minimum_atr_pips": 0.0,
        "news_blackout_minutes_before": 0,
        "news_blackout_minutes_after": 0,
    },
}

RUN = {
    "provider": "exness",
    "provider_symbol": "EURUSD",
    "timeout": "M5",
    "start_time_utc": "2026-01-01T00:00:00Z",
    "end_time_utc": "2026-01-05T00:00:00Z",
    "cost": {
        "spread_model": "provider_bid_ask",
        "commission_model": "fixed_per_lot",
        "commission_per_lot": 2.0,
        "slippage_model": "fixed_adverse",
        "fixed_slippage_pips": 0.3,
        "swap_enabled": True,
        "swap_points_per_night": 0.2,
        "account_currency": "USD",
        "starting_balance": 100000,
        "execution_model": "BID_ASK_HISTORICAL_WHERE_AVAILABLE",
    },
}


def _email():
    return f"rbt-{uuid.uuid4().hex[:8]}@example.com"


def _user():
    email = _email()
    r = client.post("/api/auth/register", json={"email": email, "password": "secret-pass-123"})
    assert r.status_code == 201, r.text
    token = client.post("/api/auth/login", json={"email": email, "password": "secret-pass-123"}).json()["access_token"]
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _strategy(token, spec=None):
    r = client.post("/api/strategies", json={"name": "RBStrategy", "spec": spec or MIN_SPEC}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _connect(token):
    r = client.post("/api/providers/exness-mt5/connect", json={
        "connection_mode": "server_side_mt5",
        "display_name": "RBT MT5",
        "environment": "demo",
        "login": "1234567",
        "server": "Exness-MT5Trial",
        "password": "s3cret-pass-abc",
        "use_read_only": True,
        "confirm_read_only": True,
        "idempotency_key": f"rbtconn-{uuid.uuid4().hex[:8]}",
    }, headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["connection"]["id"]


def _make_run(token, strat_id, conn_id):
    payload = dict(RUN)
    payload.update({
        "strategy_id": strat_id,
        "connection_id": conn_id,
        "idempotency_key": f"rbt-{uuid.uuid4().hex[:8]}",
    })
    r = client.post("/api/real-backtests", json=payload, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# run lifecycle via /real-backtests
# ---------------------------------------------------------------------------
def test_real_backtest_completes():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _make_run(token, strat_id, conn_id)
    assert run["run_status"] == "COMPLETED", run
    assert run["candle_count"] > 0
    assert run["source_data_type"] == "bid_ask"


def test_real_backtest_requires_connection_no_silent_mock():
    token = _user()
    strat_id = _strategy(token)
    payload = dict(RUN)
    payload.update({"strategy_id": strat_id, "idempotency_key": f"rbt-noconn-{uuid.uuid4().hex[:8]}"})
    r = client.post("/api/real-backtests", json=payload, headers=_h(token))
    assert r.status_code == 201  # run is queued/created
    body = r.json()
    assert body["run_status"] in ("FAILED", "PROVIDER_UNAVAILABLE", "CANCELLED"), body
    assert "PROVIDER_UNAVAILABLE" in (body.get("error_safe") or "")


def test_real_backtest_preview_reports_not_configured_without_connection():
    token = _user()
    strat_id = _strategy(token)
    r = client.post("/api/real-backtests/preview", json={
        "strategy_id": strat_id,
        "provider": "exness",
        "provider_symbol": "EURUSD",
        "timeout": "M5",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "end_time_utc": "2026-01-05T00:00:00Z",
    }, headers=_h(token))
    # Preview is informational: it must NOT claim readiness it never verified.
    assert r.status_code == 200
    assert r.json()["provider_status"] in ("NOT_CONFIGURED", "DISCONNECTED", "FAILED")


def test_real_backtest_list_get_metrics_trades_quality():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    _make_run(token, strat_id, conn_id)

    rows = client.get("/api/real-backtests", headers=_h(token))
    assert rows.status_code == 200 and len(rows.json()) >= 1

    run_id = rows.json()[0]["id"]
    one = client.get(f"/api/real-backtests/{run_id}", headers=_h(token))
    assert one.status_code == 200 and one.json()["id"] == run_id

    metrics = client.get(f"/api/real-backtests/{run_id}/metrics", headers=_h(token))
    assert metrics.status_code == 200
    m = metrics.json()["metrics"]
    assert "net_profit" in m or "total_net_pnl" in m or len(m) >= 10

    trades = client.get(f"/api/real-backtests/{run_id}/trades", headers=_h(token))
    assert trades.status_code == 200

    quality = client.get(f"/api/real-backtests/{run_id}/data-quality", headers=_h(token))
    assert quality.status_code == 200 and quality.json()["quality_status"] in ("PASS", "PASS_WITH_WARNINGS", "FAIL")


# ---------------------------------------------------------------------------
# chart payload (candles + overlays + markers + gaps)
# ---------------------------------------------------------------------------
def test_real_backtest_chart_contains_overlays_and_markers():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    _make_run(token, strat_id, conn_id)

    run_id = client.get("/api/real-backtests", headers=_h(token)).json()[0]["id"]
    chart = client.get(f"/api/real-backtests/{run_id}/chart", headers=_h(token))
    assert chart.status_code == 200, chart.text
    payload = chart.json()
    assert payload["run"]["run_status"] == "COMPLETED"
    assert len(payload["candles"]) > 0
    # overlays generated from the spec's indicators (EMA6/12)
    overlay_names = set(payload["overlays"].keys())
    assert any("EMA" in k for k in overlay_names), overlay_names
    # trades/signals assembled for markers
    assert "trades" in payload and "signals" in payload
    assert "gaps" in payload


def test_cross_workspace_chart_denied():
    token_a = _user()
    token_b = _user()
    strat_a = _strategy(token_a)
    conn_a = _connect(token_a)
    _make_run(token_a, strat_a, conn_a)
    run_id = client.get("/api/real-backtests", headers=_h(token_a)).json()[0]["id"]
    r = client.get(f"/api/real-backtests/{run_id}/chart", headers=_h(token_b))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------
def test_real_backtest_cancel_missing_run_404():
    token = _user()
    r = client.post(f"/api/real-backtests/{uuid.uuid4()}/cancel", headers=_h(token))
    assert r.status_code == 404
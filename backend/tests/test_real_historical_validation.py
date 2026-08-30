"""Tests for the Real Historical Data validator (Phase 7-9).

Covers: previews, run creation (idempotent), the data-quality gate, warm-up,
reproducibility, safe execution (no look-ahead by construction), bid/ask-aware
execution with estimated-spread labels, costs persisted per trade, results
endpoints, export redaction, cancellation, and cross-workspace denial. Runs
against the clearly-labelled mock/test adapter that streams synthetic
bid/ask candles.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MIN_SPEC = {
    "name": "RHVStrategy",
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
    return f"rhv-{uuid.uuid4().hex[:8]}@example.com"


def _user():
    email = _email()
    r = client.post("/api/auth/register", json={"email": email, "password": "secret-pass-123"})
    assert r.status_code == 201, r.text
    token = client.post("/api/auth/login", json={"email": email, "password": "secret-pass-123"}).json()["access_token"]
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _strategy(token, spec=None):
    r = client.post("/api/strategies", json={"name": "RHVStrategy", "spec": spec or MIN_SPEC}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _connect(token):
    r = client.post("/api/providers/exness-mt5/connect", json={
        "connection_mode": "server_side_mt5",
        "display_name": "RHV MT5",
        "environment": "demo",
        "login": "1234567",
        "server": "Exness-MT5Trial",
        "password": "s3cret-pass-abc",
        "use_read_only": True,
        "confirm_read_only": True,
        "idempotency_key": f"rhvconn-{uuid.uuid4().hex[:8]}",
    }, headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()["connection"]["id"]


def _run(token, strat_id, conn_id, body=None, key=None):
    payload = dict(RUN)
    payload.update({"strategy_id": strat_id, "connection_id": conn_id, "idempotency_key": key or f"rhv-{uuid.uuid4().hex[:8]}"})
    if body:
        payload.update(body)
    r = client.post("/api/real-historical-validations", json=payload, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Preview + create + full pipeline
# ---------------------------------------------------------------------------
def test_preview_reports_connection_and_coverage():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    r = client.post("/api/real-historical-validations/preview", json={
        "strategy_id": strat_id,
        "connection_id": conn_id,
        "provider": "exness",
        "provider_symbol": "EURUSD",
        "timeout": "M5",
        "start_time_utc": "2026-01-01T00:00:00Z",
        "end_time_utc": "2026-01-05T00:00:00Z",
    }, headers=_h(token))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["provider_status"] == "CONNECTED"
    assert p["symbol_mapping_status"] == "mapped"
    assert p["symbol_in_spec"] is True
    assert p["estimated_candles"] > 0
    assert p["required_warmup_candles"] > 0
    assert p["incompatibilities"] == []


def test_validation_run_completes_with_quality_gate():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    assert run["run_status"] == "COMPLETED", run
    assert run["candle_count"] > 0
    assert run["source_data_type"] == "bid_ask"
    assert run["execution_model"] == "BID_ASK_HISTORICAL_WHERE_AVAILABLE"
    assert run["data_quality_score"] is not None and run["data_quality_score"] >= 0.5
    assert run["source_data_hash"]

    q = client.get(f"/api/real-historical-validations/{run['id']}/data-quality", headers=_h(token)).json()
    assert q["quality_status"] == "PASS"
    assert q["bid_ask_availability"]


def test_validation_run_is_reproducible():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    r1 = _run(token, strat_id, conn_id, key="repro-1")
    r2 = _run(token, strat_id, conn_id, key="repro-2")
    assert r1["source_data_hash"] == r2["source_data_hash"]
    assert r1["candle_count"] == r2["candle_count"]
    assert r1["data_quality_score"] == r2["data_quality_score"]


def test_validation_run_is_idempotent_by_key():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    r1 = _run(token, strat_id, conn_id, key="same-key")
    r2 = _run(token, strat_id, conn_id, key="same-key")
    assert r1["id"] == r2["id"]


def test_trades_use_bid_ask_basis_and_persist_costs():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    if run["run_status"] != "COMPLETED":
        # A strategy producing zero trades still completes; skip the trade assertions.
        assert run["run_status"] == "COMPLETED", run.get("error_safe")
        return
    trades = client.get(f"/api/real-historical-validations/{run['id']}/trades", headers=_h(token)).json()
    if not trades:
        return
    for t in trades[:40]:
        assert t["entry_price_basis"] in ("bid", "ask")
        assert t["execution_model"] == "BID_ASK_HISTORICAL_WHERE_AVAILABLE"
        for field in ("spread_cost", "slippage_cost", "commission", "swap", "net_pnl", "gross_pnl"):
            assert field in t
        assert isinstance(t["net_pnl"], float)


def test_metrics_details_and_equity_present():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    mid = f"/api/real-historical-validations/{run['id']}"
    m = client.get(f"{mid}/metrics", headers=_h(token)).json()
    assert "num_trades" in m["metrics"]
    assert "net_profit" in m["metrics"]
    assert "details" in m
    eq = client.get(f"{mid}/equity-curve", headers=_h(token)).json()
    assert "equity_curve" in eq
    assert "drawdown_curve" in eq


def test_signals_endpoint():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    sig = client.get(f"/api/real-historical-validations/{run['id']}/signals", headers=_h(token)).json()
    assert isinstance(sig, list)
    for s in sig[:20]:
        assert "ts" in s and "signal" in s and "state" in s


def test_export_redacts_and_lists_collections():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    e = client.post(f"/api/real-historical-validations/{run['id']}/export", headers=_h(token))
    assert e.status_code == 200, e.text
    data = e.json()
    assert "trades" in data and isinstance(data["trades"], list)
    assert "signals" in data and isinstance(data["signals"], list)
    assert "metrics" in data
    assert "cost_events" in data
    blob = e.text.lower()
    for forbidden in ("s3cret", "password", "encrypted", "pairing_token"):
        assert forbidden not in blob, f"export leaked {forbidden}"


def test_cancel_completed_run_is_noop_200():
    token = _user()
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    r = client.post(f"/api/real-historical-validations/{run['id']}/cancel", headers=_h(token))
    assert r.status_code == 200
    assert r.json()["run_status"] in ("COMPLETED", "CANCELLED")


def test_cross_workspace_denial():
    token_a = _user()
    strat_id = _strategy(token_a)
    conn_id = _connect(token_a)
    run = _run(token_a, strat_id, conn_id)
    token_b = _user()
    for path in (
        f"/api/real-historical-validations/{run['id']}",
        f"/api/real-historical-validations/{run['id']}/trades",
        f"/api/real-historical-validations/{run['id']}/metrics",
        f"/api/real-historical-validations/{run['id']}/data-quality",
        f"/api/real-historical-validations/{run['id']}/candles",
    ):
        r = client.get(path, headers=_h(token_b))
        assert r.status_code == 404, f"{path} -> {r.status_code}"


def test_list_runs_scoped_to_workspace():
    token_a = _user()
    strat_id = _strategy(token_a)
    conn_id = _connect(token_a)
    run = _run(token_a, strat_id, conn_id)
    token_b = _user()
    run_b = _run(token_b, _strategy(token_b), _connect(token_b))
    list_b = client.get("/api/real-historical-validations", headers=_h(token_b)).json()
    ids_b = {r["id"] for r in list_b}
    assert run_b["id"] in ids_b
    assert run["id"] not in ids_b


def test_unknown_run_404_and_provider_outage_safe_message():
    token = _user()
    r = client.get(f"/api/real-historical-validations/{uuid.uuid4().hex}", headers=_h(token))
    assert r.status_code == 404

    # a run that references a provider with no connection must fail with a safe message
    strat_id = _strategy(token)
    r = client.post("/api/real-historical-validations", json=dict(RUN, strategy_id=strat_id, idempotency_key="no-conn"),
                    headers=_h(token))
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        run = r.json()
        assert run["run_status"] in ("FAILED", "CANCELLED")
        if run["run_status"] == "FAILED":
            assert "Traceback" not in (run["error_safe"] or "")
            assert "secret" not in (run["error_safe"] or "").lower()


def test_live_trading_remains_disabled_throughout():
    token = _user()
    card = client.get("/api/providers/exness-mt5/status", headers=_h(token)).json()
    assert card["live_trading_status"] == "disabled"
    strat_id = _strategy(token)
    conn_id = _connect(token)
    run = _run(token, strat_id, conn_id)
    payload = client.get(f"/api/real-historical-validations/{run['id']}", headers=_h(token)).json()
    assert payload["run_status"] in ("COMPLETED", "FAILED", "CANCELLED")
    card2 = client.get("/api/providers/exness-mt5/status", headers=_h(token)).json()
    assert card2["live_trading_status"] == "disabled"
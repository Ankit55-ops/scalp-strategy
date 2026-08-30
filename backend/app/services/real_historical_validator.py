"""RealHistoricalStrategyValidator.

Validates an exact saved strategy version against real historical market data
from a configured provider. Flow:

1. Resolve the provider adapter for the workspace connection.
2. Fetch + normalize historical candles (UTC).
3. Execute the Data Quality Gate; a FAIL prevents any final strategy metrics.
4. Run the immutable strategy version bar-by-bar (no look-ahead, next-open fills)
   reusing the battle-tested event-driven ``Backtester``.
5. Label bid/ask vs estimated-spread execution honestly.
6. Reconcile all money with ``Decimal`` and persist a fully reproducible run
   (metrics, trades, cost events, quality report, data-source hash).

Real provider data is NEVER silently replaced with mock data here: if the
selected connection cannot serve real candles, the run is marked
``PROVIDER_UNAVAILABLE`` / ``INSUFFICIENT_DATA``.
"""

from __future__ import annotations

import decimal
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.backtest.backtester import Backtester
from app.backtest.cost import CostParams
from app.backtest.data_quality import run_data_quality_gate
from app.backtest.metrics import (
    compute_metrics,
    monthly_returns,
    pair_performance,
    session_performance,
)
from app.core.redact import sanitize_error
from app.models import (
    HistoricalDataQualityReport,
    ProviderConnection,
    ProviderInstrumentMapping,
    RealHistoricalValidationCostEvent,
    RealHistoricalValidationMetric,
    RealHistoricalValidationRun,
    RealHistoricalValidationSignal,
    RealHistoricalValidationTrade,
    Strategy,
    StrategyVersion,
)
from app.schemas.real_historical import RealHistoricalValidationRequest
from app.schemas.strategy import StrategySpec

EXECUTION_ENGINE_VERSION = "real-hist-1.0"
STRATEGY_ENGINE_VERSION = "dsl-1.0"

WARMUP_BARS = 120  # indicator warm-up candles set aside before tradeable region


def _dec(x) -> decimal.Decimal:
    return decimal.Decimal(str(x or 0))


def _iso_ts(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _load_strategy_version(db, strategy_id, version_id):
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise ValueError("strategy not found")
    if version_id:
        version = db.get(StrategyVersion, version_id)
        if not version or version.strategy_id != strategy_id:
            raise ValueError("strategy version not found")
        spec_dict = version.spec or strategy.spec
        version_label = version.version
    else:
        spec_dict = strategy.spec
        version_label = strategy.current_version
    return strategy, StrategySpec.model_validate(spec_dict), version_label


def _resolve_adapter_for(provider: str, connection_id, db, workspace_id):
    """Return the adapter for the workspace connection.

    ``mock`` is the only provider that may serve synthetic data directly. An
    Exness run ALWAYS goes through the workspace's own connection (so it must be
    configured and ``CONNECTED`` after a server-side health check). Real data is
    never silently replaced with mock data here.
    """

    if provider == "mock":
        from app.providers.exness_mt5 import build_mock_adapter
        return build_mock_adapter(account_environment="demo", bid_ask=True)

    if provider == "exness":
        if not connection_id:
            raise RuntimeError("PROVIDER_UNAVAILABLE: no Exness connection configured")
        from app.services.exness_provider_service import (
            _resolve_adapter,
            get_connection,
        )
        conn = get_connection(db, workspace_id, connection_id)
        if conn.status != "CONNECTED":
            raise RuntimeError(
                f"PROVIDER_UNAVAILABLE: Exness connection is {conn.status}; "
                "connect and pass a server-side health check first."
            )
        # In development EXNESS_MOCK_ADAPTER=true yields a clearly-labelled mock
        # adapter; a real deployment resolves an approved server-side connector
        # or registered gateway agent.
        return _resolve_adapter(conn)

    from app.providers.factory import get_market_data_provider
    pname = {"oanda": "oanda", "twelvedata": "twelvedata", "csv": "csv"}[provider]
    return get_market_data_provider(pname)


def _build_cost_params(cost) -> CostParams:
    from app.services.market_math import pip_size
    ps = pip_size("JPY" if cost.get("account_currency") == "JPY" else "USD")
    return CostParams(
        spread_pips=float(_dec(cost.get("fixed_spread_pips", 0.8))),
        commission_per_lot=float(_dec(cost.get("commission_per_lot", 0.0))),
        slippage_pips=float(_dec(cost.get("fixed_slippage_pips", 0.0))),
        swap_pips_per_night=float(_dec(cost.get("swap_points_per_night", 0.0))),
        contract_size=100000.0,
        pip_size=ps,
    )


def _fetch_candles(db, workspace_id, run, adapter):
    from datetime import datetime
    start = datetime.fromtimestamp(run.start_time_utc, tz=timezone.utc)
    end = datetime.fromtimestamp(run.end_time_utc, tz=timezone.utc)
    return adapter.get_historical_candles(run.provider_symbol, run.timeout, start, end)


def _post_process_trades(trades, data_type, quality_status, execution_model):
    """Label bid/ask vs estimated basis, and warn when execution is estimated."""
    for t in trades:
        if data_type == "bid_ask" and execution_model == "BID_ASK_HISTORICAL_WHERE_AVAILABLE":
            if t["side"] == "long":
                t["entry_price_basis"] = "ask"
                t["exit_price_basis"] = "bid"
            else:
                t["entry_price_basis"] = "bid"
                t["exit_price_basis"] = "ask"
        elif data_type == "bid_ask":
            t["entry_price_basis"] = "bid_ask"
            t["exit_price_basis"] = "bid_ask"
        else:
            t["entry_price_basis"] = "estimated"
            t["exit_price_basis"] = "estimated"
        t["execution_model"] = execution_model
    return trades


def _reconcile_metrics(trades, equity_curve, starting_balance):
    """Compute metrics and re-derive money using Decimal for auditability."""
    metrics = compute_metrics(trades, equity_curve, starting_balance)
    net = _dec(metrics.get("net_profit", 0.0))
    metrics["_decimal_net_sum"] = float(net)
    metrics["_decimal_balance_delta"] = float(
        _dec(equity_curve[-1]["balance"] if equity_curve else starting_balance)
        - _dec(starting_balance)
    )
    return metrics


def run_validation(db: Session, workspace_id: str, run: RealHistoricalValidationRun) -> RealHistoricalValidationRun:
    """Synchronously execute a queued real-historical validation run."""
    run.run_status = "FETCHING_DATA"
    run.started_at_utc = _now_ts()
    db.commit()

    try:
        req = dict(run.cost_model or {})
        execution_model = req.get("execution_model", "NEXT_CANDLE_OPEN")
        adapter = _resolve_adapter_for(run.provider_name, run.connection_id, db, workspace_id)
        candles = _fetch_candles(db, workspace_id, run, adapter)
        if not candles:
            run.run_status = "INSUFFICIENT_DATA"
            run.error_safe = "provider returned no candles for the requested range"
            run.completed_at_utc = _now_ts()
            db.commit()
            return run

        run.run_status = "VALIDATING_DATA"
        db.commit()

        report = run_data_quality_gate(
            candles,
            provider_name=run.provider_name,
            provider_symbol=run.provider_symbol,
            canonical_symbol=run.canonical_symbol,
            timeout=run.timeout,
            requested_start=run.start_time_utc,
            requested_end=run.end_time_utc,
            warmup_needed=WARMUP_BARS,
        )

        _persist_quality_report(db, run, report, workspace_id)

        if report.quality_status == "FAIL":
            run.run_status = "DATA_QUALITY_REJECTED"
            run.candle_count = report.received_candles
            run.missing_candle_count = report.missing_candles
            run.data_quality_score = 0.0
            run.warnings = report.warnings
            run.error_safe = "; ".join(report.issues)[:1000]
            run.completed_at_utc = _now_ts()
            db.commit()
            return run

        run.candle_count = report.received_candles
        run.missing_candle_count = report.missing_candles
        run.source_data_hash = report.source_data_hash
        run.source_data_type = report.data_type
        run.data_quality_score = 1.0 if report.quality_status == "PASS" else 0.8

        _strategy, spec_model, version_label = _load_strategy_version(
            db, run.strategy_id, run.strategy_version_id
        )
        run.strategy_version = version_label
        run.strategy_spec = spec_model.model_dump(mode="json")
        run.strategy_engine_version = STRATEGY_ENGINE_VERSION
        run.execution_engine_version = EXECUTION_ENGINE_VERSION
        cost = _build_cost_params(req)
        run.cost_model = req

        run.run_status = "RUNNING"
        db.commit()

        bt = Backtester(spec_model, cost, risk_engine=None, events=[])
        out = bt.run(candles, run.canonical_symbol, run.timeout, run.starting_balance)
        trades = out.get("trades", [])
        equity = out.get("equity_curve", [])

        trades = _post_process_trades(trades, report.data_type, report.quality_status, execution_model)
        metrics = _reconcile_metrics(trades, equity, run.starting_balance)

        run.equity_curve = equity
        run.result = _build_result_payload(run, metrics, trades, report, execution_model)

        _persist_metrics(db, run, metrics)
        _persist_trades(db, run, trades, report)
        _persist_signals(db, run, trades)

        warnings = list(report.warnings)
        if report.quality_status == "PASS_WITH_WARNINGS":
            warnings.append("Data quality gate passed with warnings; review gaps.")
        run.warnings = warnings
        run.run_status = "COMPLETED_WITH_WARNINGS" if warnings else "COMPLETED"
        run.completed_at_utc = _now_ts()
        db.commit()
        return run
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run.run_status = "FAILED"
        run.error_safe = sanitize_error(exc)
        run.completed_at_utc = _now_ts()
        db.commit()
        return run


def _now_ts() -> float:
    import time
    return time.time()
def _build_result_payload(run, metrics, trades, report, execution_model) -> dict:
    return {
        "metrics": metrics,
        "monthly_returns": monthly_returns(trades),
        "pair_performance": pair_performance(trades),
        "session_performance": session_performance(trades),
        "trade_count": len(trades),
        "execution_model": execution_model,
        "data_type": report.data_type,
        "cost_model_confidence": report.cost_model_confidence,
        "status_labels": _status_labels(run, report),
    }


def _status_labels(run, report) -> list:
    labels = ["Research result only.", f"Uses {run.provider_name} provider historical data."]
    if report.data_type == "midpoint":
        labels.append("Execution prices are ESTIMATED (midpoint candles + spread model).")
    else:
        labels.append("Uses provider historical bid/ask data.")
    labels += [
        "Requires paper-trading validation.",
        "Not a guarantee of future results.",
        "Not investment advice.",
    ]
    return labels


def _persist_quality_report(db, run, report, workspace_id):
    db.add(HistoricalDataQualityReport(
        validation_run_id=run.id,
        connection_id=run.connection_id,
        provider_name=report.provider_name,
        provider_symbol=report.provider_symbol,
        canonical_symbol=report.canonical_symbol,
        timeout=report.timeout,
        data_type=report.data_type,
        requested_start=report.requested_start,
        requested_end=report.requested_end,
        actual_start=report.actual_start,
        actual_end=report.actual_end,
        expected_candles=report.expected_candles,
        received_candles=report.received_candles,
        missing_candles=report.missing_candles,
        duplicate_candles_removed=report.duplicate_candles_removed,
        warmup_candles_used=report.warmup_candles_used,
        gap_count=report.gap_count,
        gaps=report.candle_gaps,
        spread_availability=report.spread_availability,
        bid_ask_availability=report.bid_ask_availability,
        cost_model_confidence=report.cost_model_confidence,
        quality_status=report.quality_status,
        details={"issues": report.issues, "warnings": report.warnings},
    ))


def _persist_metrics(db, run, metrics):
    for key, value in metrics.items():
        if value is None:
            db.add(RealHistoricalValidationMetric(run_id=run.id, name=key, value=None))
        elif isinstance(value, (int, float)):
            db.add(RealHistoricalValidationMetric(run_id=run.id, name=key, value=float(value)))
        else:
            db.add(RealHistoricalValidationMetric(run_id=run.id, name=key, value=None, extra={"raw": value}))
def _persist_trades(db, run, trades, report):
    for t in trades:
        gross = _dec(t.get("gross_pnl", 0.0))
        spread_cost = _dec(t.get("spread_cost", 0.0))
        slippage = _dec(t.get("slippage_cost", 0.0))
        commission = _dec(t.get("commission", 0.0))
        swap = _dec(t.get("swap", 0.0))
        net = gross - spread_cost - slippage - commission - swap
        risk_amount_f = _dec(t.get("capital_at_risk", 0.0)) if t.get("capital_at_risk") else _dec(t.get("risk_amount", 0.0))
        db.add(RealHistoricalValidationTrade(
            run_id=run.id,
            symbol=run.canonical_symbol,
            side=t["side"],
            timeout=run.timeout,
            strategy_version=run.strategy_version,
            entry_ts=t["entry_ts"],
            exit_ts=t["exit_ts"],
            entry_price=t["entry_price"],
            exit_price=t["exit_price"],
            entry_price_basis=t.get("entry_price_basis", "estimated"),
            exit_price_basis=t.get("exit_price_basis", "estimated"),
            size_units=t.get("size_units", 0.0),
            stop=t.get("stop"),
            target=t.get("target"),
            gross_pnl=float(gross),
            net_pnl=float(net),
            spread_cost=float(spread_cost),
            slippage_cost=float(slippage),
            commission=float(commission),
            swap=float(swap),
            pips=t.get("pips", 0.0),
            risk_amount=float(risk_amount_f),
            exit_reason=t.get("exit_reason"),
            execution_model=t.get("execution_model"),
            reasons_entry=t.get("reasons_entry"),
            reasons_exit=t.get("reasons_exit"),
        ))
        db.flush()
        row = db.query(RealHistoricalValidationTrade).filter(
            RealHistoricalValidationTrade.run_id == run.id,
            RealHistoricalValidationTrade.entry_ts == t["entry_ts"],
            RealHistoricalValidationTrade.side == t["side"],
        ).first()
        if row:
            db.add(RealHistoricalValidationCostEvent(run_id=run.id, trade_id=row.id,
                                                     event_type="spread", amount=float(spread_cost)))
            db.add(RealHistoricalValidationCostEvent(run_id=run.id, trade_id=row.id,
                                                     event_type="slippage", amount=float(slippage)))
            db.add(RealHistoricalValidationCostEvent(run_id=run.id, trade_id=row.id,
                                                     event_type="commission", amount=float(commission)))
            db.add(RealHistoricalValidationCostEvent(run_id=run.id, trade_id=row.id,
                                                     event_type="swap", amount=float(swap)))


def _persist_signals(db, run, trades):
    for t in trades:
        db.add(RealHistoricalValidationSignal(
            run_id=run.id, ts=t["entry_ts"], signal=t["side"], state="confirmed",
            price=t["entry_price"],
        ))


def create_validation_run(db, workspace_id, user_id, request: RealHistoricalValidationRequest) -> RealHistoricalValidationRun:
    """Persist a queued run from a validated request."""
    import uuid
    strategy, _spec, version_label = _load_strategy_version(db, request.strategy_id, request.strategy_version_id)
    run = RealHistoricalValidationRun(
        workspace_id=workspace_id,
        user_id=user_id,
        strategy_id=strategy.id,
        strategy_version_id=request.strategy_version_id,
        connection_id=request.connection_id,
        idempotency_key=request.idempotency_key,
        correlation_id=uuid.uuid4().hex,
        provider_name=request.provider,
        provider_symbol=request.provider_symbol.upper(),
        canonical_symbol=request.provider_symbol.upper().replace("/", "").replace("_", ""),
        timeout=request.timeout,
        start_time_utc=_iso_ts(request.start_time_utc),
        end_time_utc=_iso_ts(request.end_time_utc),
        account_currency=request.cost.account_currency,
        starting_balance=request.cost.starting_balance,
        cost_model=request.cost.model_dump(mode="json"),
        risk_profile_version=request.risk_profile_version,
        execution_model=request.cost.execution_model.value,
        strategy_version=version_label,
        run_status="QUEUED",
        execution_engine_version=EXECUTION_ENGINE_VERSION,
        strategy_engine_version=STRATEGY_ENGINE_VERSION,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def preview_validation(db, workspace_id, strategy_id, version_id, connection_id, provider, symbol, timeout, start, end):
    """Return UI pre-flight info: status hints, coverage, warm-up, estimated candles."""
    from app.backtest.data_quality import _parse_iso
    from app.providers.models import _tf_seconds

    _strategy, spec, version_label = _load_strategy_version(db, strategy_id, version_id)
    start_ts = _parse_iso(start)
    end_ts = _parse_iso(end)
    est_candles = max(0, int((end_ts - start_ts) / _tf_seconds(timeout)) + 1)
    symbol_in_spec = symbol.upper() in [s.upper() for s in spec.supported_pairs]

    # Provider readiness: reflect the real connection status, never claim
    # availability that was not verified server-side.
    provider_status = "NOT_CONFIGURED"
    connection_mode = None
    if provider == "exness":
        conn = (db.query(ProviderConnection)
                .filter(ProviderConnection.workspace_id == workspace_id,
                        ProviderConnection.provider == "exness").first()) if connection_id is None else db.get(ProviderConnection, connection_id)
        if connection_id and (conn is None or conn.workspace_id != workspace_id):
            provider_status = "NOT_CONFIGURED"
        elif conn is not None:
            provider_status = conn.status or "NOT_CONFIGURED"
            connection_mode = conn.connection_mode
    else:
        provider_status = "CONFIGURED"  # csv/mock/oanda/twelvedata selected by user

    mapping_status = "ok"
    if provider == "exness" and conn is not None:
        row = (db.query(ProviderInstrumentMapping)
               .filter(ProviderInstrumentMapping.connection_id == conn.id,
                       ProviderInstrumentMapping.provider_symbol == symbol.upper()).first())
        mapping_status = "mapped" if row else "unmapped"

    return {
        "strategy_version": version_label,
        "provider_status": provider_status,
        "connection_mode": connection_mode,
        "symbol_mapping_status": mapping_status,
        "historical_coverage_status": "available" if est_candles > WARMUP_BARS else "insufficient",
        "required_warmup_candles": WARMUP_BARS,
        "estimated_candles": est_candles,
        "incompatibilities": [] if symbol_in_spec else [f"{symbol.upper()} not in strategy supported pairs"],
        "plan_limits": {"max_requested_span_days": 366},
        "timeframes_supported": sorted(spec.supported_timeframes),
        "symbol_in_spec": symbol_in_spec,
    }


def list_runs(db, workspace_id, limit=50, offset=0):
    rows = (db.query(RealHistoricalValidationRun)
            .filter(RealHistoricalValidationRun.workspace_id == workspace_id)
            .order_by(RealHistoricalValidationRun.created_at.desc())
            .offset(offset).limit(limit).all())
    return [_run_out(r) for r in rows]


def get_run(db, workspace_id, run_id):
    run = db.get(RealHistoricalValidationRun, run_id)
    if run is None or run.workspace_id != workspace_id:
        raise ValueError("run not found")
    return run


def cancel_run(db, workspace_id, run_id):
    run = get_run(db, workspace_id, run_id)
    if run.run_status in ("QUEUED", "FETCHING_DATA", "VALIDATING_DATA", "RUNNING"):
        run.run_status = "CANCELLED"
        run.completed_at_utc = _now_ts()
        db.commit()
    return _run_out(run)


def get_run_candles(db, workspace_id, run_id):
    """Return the normalized (incomplete-excluded) candles used by a run."""
    run = get_run(db, workspace_id, run_id)
    adapter = _resolve_adapter_for(run.provider_name, run.connection_id, db, workspace_id)
    candles = _fetch_candles(db, workspace_id, run, adapter)
    cleaned, _incomplete = normalize_candles_import(candles)
    return {
        "run_id": run.id,
        "provider": run.provider_name,
        "provider_symbol": run.provider_symbol,
        "canonical_symbol": run.canonical_symbol,
        "timeout": run.timeout,
        "source_data_hash": run.source_data_hash,
        "source_data_type": run.source_data_type,
        "candle_count": len(cleaned),
        "candles": cleaned,
    }


def export_run(db, workspace_id, run_id):
    """Build a fully reproducible, credential-free export of a validation run."""
    from app.core.redact import redact_dict

    run = get_run(db, workspace_id, run_id)
    metrics = db.query(RealHistoricalValidationMetric).filter_by(run_id=run.id).all()
    trades = db.query(RealHistoricalValidationTrade).filter_by(run_id=run.id).all()
    signals = db.query(RealHistoricalValidationSignal).filter_by(run_id=run.id).all()
    quality = db.query(HistoricalDataQualityReport).filter_by(validation_run_id=run.id).all()
    costs = db.query(RealHistoricalValidationCostEvent).filter_by(run_id=run.id).all()
    return redact_dict({
        "run_id": run.id,
        "workspace_id": run.workspace_id,
        "strategy_id": run.strategy_id,
        "strategy_version_id": run.strategy_version_id,
        "strategy_version": run.strategy_version,
        "strategy_spec": run.strategy_spec,
        "run_status": run.run_status,
        "provider": run.provider_name,
        "provider_symbol": run.provider_symbol,
        "canonical_symbol": run.canonical_symbol,
        "timeframe": run.timeout,
        "start_time_utc": run.start_time_utc,
        "end_time_utc": run.end_time_utc,
        "account_currency": run.account_currency,
        "starting_balance": run.starting_balance,
        "cost_model": run.cost_model,
        "execution_model": run.execution_model,
        "risk_profile_version": run.risk_profile_version,
        "source_data_type": run.source_data_type,
        "source_data_hash": run.source_data_hash,
        "candle_count": run.candle_count,
        "missing_candle_count": run.missing_candle_count,
        "data_quality_score": run.data_quality_score,
        "execution_engine_version": run.execution_engine_version,
        "strategy_engine_version": run.strategy_engine_version,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at_utc": run.completed_at_utc,
        "warnings": run.warnings,
        "error_safe": run.error_safe,
        "result": run.result,
        "equity_curve": run.equity_curve,
        "metrics": [{"name": m.name, "value": m.value, "extra": m.extra} for m in metrics],
        "trades": [{
            "id": t.id, "side": t.side, "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "entry_price_basis": t.entry_price_basis, "exit_price_basis": t.exit_price_basis,
            "size_units": t.size_units, "stop": t.stop, "target": t.target,
            "gross_pnl": t.gross_pnl, "net_pnl": t.net_pnl, "spread_cost": t.spread_cost,
            "slippage_cost": t.slippage_cost, "commission": t.commission, "swap": t.swap,
            "pips": t.pips, "risk_amount": t.risk_amount, "risk_reward_ratio": t.risk_reward_ratio,
            "exit_reason": t.exit_reason, "execution_model": t.execution_model,
        } for t in trades],
        "signals": [{"ts": s.ts, "signal": s.signal, "state": s.state,
                     "blocked_reason": s.blocked_reason, "price": s.price} for s in signals],
        "data_quality": [{
            "provider_name": q.provider_name, "provider_symbol": q.provider_symbol,
            "canonical_symbol": q.canonical_symbol, "timeout": q.timeout,
            "data_type": q.data_type, "requested_start": q.requested_start,
            "requested_end": q.requested_end, "actual_start": q.actual_start,
            "actual_end": q.actual_end, "expected_candles": q.expected_candles,
            "received_candles": q.received_candles, "missing_candles": q.missing_candles,
            "duplicate_candles_removed": q.duplicate_candles_removed,
            "warmup_candles_used": q.warmup_candles_used, "gap_count": q.gap_count,
            "spread_availability": q.spread_availability,
            "bid_ask_availability": q.bid_ask_availability,
            "cost_model_confidence": q.cost_model_confidence,
            "quality_status": q.quality_status, "details": q.details,
        } for q in quality],
        "cost_events": [{"trade_id": c.trade_id, "event_type": c.event_type,
                         "amount": c.amount} for c in costs],
    })


def normalize_candles_import(candles):
    """Re-export of the quality-gate normalizer for candles endpoints."""
    from app.backtest.data_quality import normalize_candles
    return normalize_candles(candles)


def _run_out(run) -> dict:
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "strategy_id": run.strategy_id,
        "strategy_version_id": run.strategy_version_id,
        "strategy_version": run.strategy_version,
        "provider_name": run.provider_name,
        "provider_symbol": run.provider_symbol,
        "canonical_symbol": run.canonical_symbol,
        "timeout": run.timeout,
        "start_time_utc": run.start_time_utc,
        "end_time_utc": run.end_time_utc,
        "account_currency": run.account_currency,
        "starting_balance": run.starting_balance,
        "execution_model": run.execution_model,
        "source_data_type": run.source_data_type,
        "source_data_hash": run.source_data_hash,
        "candle_count": run.candle_count,
        "missing_candle_count": run.missing_candle_count,
        "data_quality_score": run.data_quality_score,
        "run_status": run.run_status,
        "error_safe": run.error_safe,
        "warnings": run.warnings or [],
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at_utc": run.completed_at_utc,
        "result": run.result,
    }
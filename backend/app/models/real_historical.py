"""Models for the Real Historical Data add-on and secure provider connections.

These tables support persisting provider connections (Exness via MT5, OANDA,
Twelve Data, CSV, Mock) with encrypted-at-rest credentials and capability
discovery, MT5 Gateway Agent pairing, data-quality reports, and reproducible
``RealHistoricalValidationRun`` records.

Sensitive values are never stored in plaintext: credentials and connection
metadata live in ``encrypted_credentials`` / ``encrypted_connection_metadata``
(Fernet ciphertext). Secret-bearing keys are redacted before any row is returned
to a frontend client.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderConnectionCapability(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_connection_capabilities"

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    capability: Mapped[str] = mapped_column(String(48), index=True)
    # enum: available | unavailable | permission_denied | not_verified
    availability: Mapped[str] = mapped_column(String(24), default="not_verified")
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verified_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ProviderConnectionHealthEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_connection_health_events"

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24))  # CONNECTED | DEGRADED | ...
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    feed_health: Mapped[str | None] = mapped_column(String(24), nullable=True)
    detail_safe: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checked_at: Mapped[float] = mapped_column(Float)


class ProviderInstrumentMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_instrument_mappings"

    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(24), index=True)
    provider_symbol: Mapped[str] = mapped_column(String(32), index=True)
    canonical_symbol: Mapped[str] = mapped_column(String(16), index=True)
    display_symbol: Mapped[str] = mapped_column(String(16))
    asset_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    base_currency: Mapped[str] = mapped_column(String(8))
    quote_currency: Mapped[str] = mapped_column(String(8))
    digits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pip_size: Mapped[float] = mapped_column(Float, default=0.0001)
    contract_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum_lot: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_step: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    freeze_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    trading_sessions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    swap_long: Mapped[float | None] = mapped_column(Float, nullable=True)
    swap_short: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_supported: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_verified_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ProviderHistoricalDataCache(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_historical_data_cache"

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(24), index=True)
    provider_symbol: Mapped[str] = mapped_column(String(32), index=True)
    canonical_symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    start_time_utc: Mapped[float] = mapped_column(Float)
    end_time_utc: Mapped[float] = mapped_column(Float)
    candle_count: Mapped[int] = mapped_column(Integer, default=0)
    data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # normalized candles
class HistoricalDataQualityReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "historical_data_quality_reports"

    validation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider_name: Mapped[str] = mapped_column(String(32))
    provider_symbol: Mapped[str] = mapped_column(String(32))
    canonical_symbol: Mapped[str] = mapped_column(String(16))
    timeout: Mapped[str] = mapped_column(String(8), index=True)
    data_type: Mapped[str] = mapped_column(String(32))  # historical_candles | bid_ask | midpoint | estimated_spread
    requested_start: Mapped[float] = mapped_column(Float)
    requested_end: Mapped[float] = mapped_column(Float)
    actual_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_candles: Mapped[int] = mapped_column(Integer, default=0)
    received_candles: Mapped[int] = mapped_column(Integer, default=0)
    missing_candles: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_candles_removed: Mapped[int] = mapped_column(Integer, default=0)
    warmup_candles_used: Mapped[int] = mapped_column(Integer, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    gaps: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    feed_delay_warning: Mapped[str | None] = mapped_column(String(128), nullable=True)
    spread_availability: Mapped[str] = mapped_column(String(24), default="not_checked")
    bid_ask_availability: Mapped[str] = mapped_column(String(24), default="not_checked")
    cost_model_confidence: Mapped[str] = mapped_column(String(24), default="high")
    quality_status: Mapped[str] = mapped_column(String(24), default="FAIL")  # PASS | PASS_WITH_WARNINGS | FAIL
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[RealHistoricalValidationRun | None] = relationship(back_populates="data_quality")


class MT5GatewayAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "mt5_gateway_agents"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    gateway_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    device_name: Mapped[str] = mapped_column(String(128))
    public_identity_pin: Mapped[str | None] = mapped_column(String(256), nullable=True)
    encrypted_pairing_token: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    pairing_token_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PAIRING")  # PAIRING | ONLINE | OFFLINE | REVOKED | EXPIRED
    ip_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class MT5GatewayPairingEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "mt5_gateway_pairing_events"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    gateway_id: Mapped[str | None] = mapped_column(
        ForeignKey("mt5_gateway_agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(24))  # pairing_initiated | token_issued | paired | rejected | revoked | token_expired
    detail_safe: Mapped[str | None] = mapped_column(String(512), nullable=True)
    issued_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ProviderConnectionAuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_connection_audit_logs"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(32))  # create | test | update | rotate | disconnect | delete | pair | pair_revoked
    status: Mapped[str] = mapped_column(String(24))
    detail_safe: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class RealHistoricalValidationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "real_historical_validation_runs"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(32))  # mock | exness | oanda | twelvedata | csv
    provider_connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_symbol: Mapped[str] = mapped_column(String(32))
    canonical_symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeout: Mapped[str] = mapped_column(String(8), index=True)
    start_time_utc: Mapped[float] = mapped_column(Float)
    end_time_utc: Mapped[float] = mapped_column(Float)
    account_currency: Mapped[str] = mapped_column(String(8), default="USD")
    starting_balance: Mapped[float] = mapped_column(Float, default=100000.0)
    cost_model: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_profile_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_data_type: Mapped[str] = mapped_column(String(32), default="historical_candles")
    source_data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candle_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_candle_count: Mapped[int] = mapped_column(Integer, default=0)
    data_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_model: Mapped[str] = mapped_column(String(40), default="NEXT_CANDLE_OPEN")
    strategy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # run_status: QUEUED | FETCHING_DATA | VALIDATING_DATA | RUNNING | COMPLETED |
    # COMPLETED_WITH_WARNINGS | FAILED | CANCELLED | INSUFFICIENT_DATA |
    # PROVIDER_UNAVAILABLE | INVALID_STRATEGY | DATA_QUALITY_REJECTED
    run_status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    error_safe: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    execution_engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at_utc: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed_at_utc: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    equity_curve: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    drawdown_curve: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    data_quality: Mapped[list[HistoricalDataQualityReport]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    metrics: Mapped[list[RealHistoricalValidationMetric]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    trades: Mapped[list[RealHistoricalValidationTrade]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    signals: Mapped[list[RealHistoricalValidationSignal]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    cost_events: Mapped[list[RealHistoricalValidationCostEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
class RealHistoricalValidationMetric(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "real_historical_validation_metrics"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[RealHistoricalValidationRun] = relationship(back_populates="metrics")


class RealHistoricalValidationTrade(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "real_historical_validation_trades"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))  # long | short
    timeout: Mapped[str] = mapped_column(String(8))
    strategy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entry_ts: Mapped[float] = mapped_column(Float)
    exit_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price_basis: Mapped[str] = mapped_column(String(24), default="mid")  # bid | ask | mid | estimated
    exit_price_basis: Mapped[str] = mapped_column(String(24), default="mid")
    size_units: Mapped[float] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    spread_cost: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    pips: Mapped[float] = mapped_column(Float, default=0.0)
    risk_amount: Mapped[float] = mapped_column(Float, default=0.0)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    execution_model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reasons_entry: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reasons_exit: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    risk_engine_decision: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[RealHistoricalValidationRun] = relationship(back_populates="trades")


class RealHistoricalValidationSignal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "real_historical_validation_signals"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[float] = mapped_column(Float)
    signal: Mapped[str] = mapped_column(String(32))  # long | short
    state: Mapped[str] = mapped_column(String(24))  # confirmed | blocked | invalidated
    blocked_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[RealHistoricalValidationRun] = relationship(back_populates="signals")


class RealHistoricalValidationCostEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "real_historical_validation_cost_events"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str | None] = mapped_column(
        ForeignKey("real_historical_validation_trades.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(24))  # spread | slippage | commission | swap | financing
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped[RealHistoricalValidationRun] = relationship(back_populates="cost_events")
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
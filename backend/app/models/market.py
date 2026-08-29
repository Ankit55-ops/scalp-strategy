from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Candle(Base):
    __tablename__ = "candles"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[float] = mapped_column(Float, primary_key=True)  # epoch seconds UTC
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(32), default="csv")
    bid_ask_basis: Mapped[str] = mapped_column(String(24), default="mid")
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True)


class Tick(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ticks"

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="")


class Spread(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "spreads"

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    spread_pips: Mapped[float] = mapped_column(Float)


class EconomicEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "economic_events"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    country: Mapped[str] = mapped_column(String(8), index=True)
    currency: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(255))
    impact: Mapped[str] = mapped_column(String(16))  # low | medium | high
    event_time: Mapped[float] = mapped_column(Float, index=True)
    actual: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forecast: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ProviderCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An encrypted, server-side-only data-provider credential per workspace."""

    __tablename__ = "provider_credentials"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(24))  # oanda | twelvedata
    label: Mapped[str] = mapped_column(String(120), default="default")
    encrypted_secret: Mapped[str] = mapped_column(String(2048))
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # non-secret config
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_connections"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), default="disconnected")
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_connected_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class InstrumentMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "instrument_mappings"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    canonical_symbol: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    provider_symbol: Mapped[str] = mapped_column(String(32))
    display_symbol: Mapped[str] = mapped_column(String(16))
    base_currency: Mapped[str] = mapped_column(String(8))
    quote_currency: Mapped[str] = mapped_column(String(8))
    pip_size: Mapped[float] = mapped_column(Float)
    price_precision: Mapped[int] = mapped_column(Integer, default=5)
    is_supported: Mapped[bool] = mapped_column(Boolean, default=True)


class MarketFeedHealth(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "market_feed_health"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(24))
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    feed_status: Mapped[str] = mapped_column(String(24), default="CONNECTING")
    last_quote_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)


class MarketDataGap(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "market_data_gaps"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(24))
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    detected_at: Mapped[float] = mapped_column(Float)


class StrategySignalEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Audit record for every strategy-checker evaluation, taken or not."""

    __tablename__ = "strategy_signal_events"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    strategy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16))
    timeframe: Mapped[str] = mapped_column(String(8))
    signal: Mapped[str] = mapped_column(String(32))
    signal_label: Mapped[str] = mapped_column(String(48))  # CONFIRMED_CANDLE_CLOSE | INTRABAR_PREVIEW | BLOCKED_*
    state: Mapped[str] = mapped_column(String(24))  # monitoring | ready | signal_found | blocked | error
    blocked_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    spread_pips: Mapped[float] = mapped_column(Float, default=0.0)
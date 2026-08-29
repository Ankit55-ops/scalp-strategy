from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
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


class Tick(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ticks"

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)


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

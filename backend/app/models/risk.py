from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class RiskProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "risk_profiles"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=0.25)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=1.0)
    max_weekly_loss_pct: Mapped[float] = mapped_column(Float, default=3.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=10.0)
    max_consecutive_losses: Mapped[int] = mapped_column(Float, default=3)
    max_open_positions: Mapped[int] = mapped_column(Float, default=1)
    max_trades_per_day: Mapped[int] = mapped_column(Float, default=5)
    max_correlated_exposure_pct: Mapped[float] = mapped_column(Float, default=2.0)
    max_spread_pips: Mapped[float] = mapped_column(Float, default=1.2)
    max_slippage_pips: Mapped[float] = mapped_column(Float, default=0.5)
    news_blackout_minutes_before: Mapped[int] = mapped_column(Float, default=15)
    news_blackout_minutes_after: Mapped[int] = mapped_column(Float, default=15)
    correlated_currency_groups: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )
    hard_stop_distance_pips: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RiskEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "risk_events"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    immutable: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Alert(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alerts"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


class SavedChartLayout(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "saved_chart_layouts"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    layout: Mapped[dict] = mapped_column(JSONB)

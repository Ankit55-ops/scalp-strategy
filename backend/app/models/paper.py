from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PaperAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "paper_accounts"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), default="Default Paper Account")
    balance: Mapped[float] = mapped_column(Float, default=100000.0)
    equity: Mapped[float] = mapped_column(Float, default=100000.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    day_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    day_start_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    week_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    week_start_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    trading_state: Mapped[str] = mapped_column(String(24), default="INACTIVE")
    state_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class PaperPosition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "paper_positions"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulated_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    size_units: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    open_ts: Mapped[float] = mapped_column(Float)
    exit_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pips: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="open")

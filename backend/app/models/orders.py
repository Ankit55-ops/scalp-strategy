from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SimulatedOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "simulated_orders"

    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    paper_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    order_type: Mapped[str] = mapped_column(String(16))  # market | limit | stop
    entry_ts: Mapped[float] = mapped_column(Float)
    exit_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    size_units: Mapped[float] = mapped_column(Float)
    risk_amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="open")
    reasons_entry: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reasons_exit: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    spread_cost: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pips: Mapped[float] = mapped_column(Float, default=0.0)

    run: Mapped["BacktestRun | None"] = relationship(back_populates="trades")


class SimulatedFill(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "simulated_fills"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("simulated_orders.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(8))
    fill_type: Mapped[str] = mapped_column(String(16))  # entry | exit

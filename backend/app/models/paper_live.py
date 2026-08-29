"""Paper-account execution ledger: orders, fills, and margin events.

Kept separate from ``SimulatedOrder``/``SimulatedFill`` (which remain the
backtest-style trade record) so the paper broker can audit the full order
lifecycle with a proper state machine:

  order: PENDING -> APPROVED -> FILLED   (entry)
                -> REJECTED
         APPROVED -> CLOSED               (exit)

``PaperFill`` records each execution leg with cost decomposition, and
``PaperMarginEvent`` is an append-only log of equity/drawdown transitions used
for risk-center monitoring and deployment review.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PaperOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "paper_orders"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_positions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trade_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulated_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    order_type: Mapped[str] = mapped_column(String(16), default="market")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    size_units: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_ts: Mapped[float] = mapped_column(Float, index=True)
    approval_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_side: Mapped[str | None] = mapped_column(String(8), nullable=True)  # entry | exit
    worker_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class PaperFill(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One executed leg of a paper order (entry or exit) with cost breakdown."""

    __tablename__ = "paper_fills"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("paper_orders.id", ondelete="CASCADE"), index=True
    )
    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_positions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trade_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulated_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ts: Mapped[float] = mapped_column(Float, index=True)
    price: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    fill_type: Mapped[str] = mapped_column(String(16))  # entry | exit
    spread_cost: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    bid_ask_basis: Mapped[str] = mapped_column(String(24), default="mid")
    provider: Mapped[str] = mapped_column(String(24), default="mock")


class PaperMarginEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "paper_margin_events"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[float] = mapped_column(Float, index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # position_opened | position_closed | drawdown_gate | state_change
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)
    trading_state: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
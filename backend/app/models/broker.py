from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, uuid_str


class BrokerConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "broker_connections"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128))
    encrypted_api_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    encrypted_api_secret: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ForexSymbol(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "forex_symbols"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), index=True)
    canonical: Mapped[str] = mapped_column(String(32), index=True)
    provider_symbol: Mapped[str] = mapped_column(String(64), index=True)
    base_currency: Mapped[str] = mapped_column(String(8))
    quote_currency: Mapped[str] = mapped_column(String(8))
    pip_position: Mapped[int] = mapped_column(Integer, default=4)
    pip_value: Mapped[float] = mapped_column(default=0.0)
    contract_size: Mapped[float] = mapped_column(default=100000.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MarketDataSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "market_data_sources"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="configured")
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

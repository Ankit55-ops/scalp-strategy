from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class LiveDeploymentRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "live_deployment_requests"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    broker_connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft")
    checks: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    deployment_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

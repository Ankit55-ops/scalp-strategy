from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Strategy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategies"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True)
    strategy_family: Mapped[str] = mapped_column(String(64), index=True)
    current_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    spec: Mapped[dict] = mapped_column(JSONB, nullable=True)

    versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )
    rules: Mapped[list["StrategyRule"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class StrategyVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategy_versions"

    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(32))
    spec: Mapped[dict] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="versions")


class StrategyRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategy_rules"

    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_type: Mapped[str] = mapped_column(String(16))  # entry | exit
    description: Mapped[str] = mapped_column(Text)
    expression: Mapped[str] = mapped_column(Text)
    is_valid: Mapped[bool] = mapped_column(default=True)
    validation_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="rules")

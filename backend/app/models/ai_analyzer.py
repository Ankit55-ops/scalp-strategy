"""Model for AI Strategy Analyzer results cache."""

from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class StrategyAnalysisCache(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per unique (workspace, analyzed-text) so an identical prompt is
    never sent to the AI twice. Credentials are never part of prompt text."""

    __tablename__ = "strategy_analysis_cache"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "text_sha256", name="uq_strategy_analysis_ws_hash"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )
    text_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    prompt_text: Mapped[str] = mapped_column(String(8000), nullable=False)
    provider_used: Mapped[str] = mapped_column(String(16), default="mock")
    testability_status: Mapped[str] = mapped_column(String(24), default="NEEDS_USER_INPUT")
    analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    strategy_spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    converted: Mapped[bool] = mapped_column(default=False, nullable=False)
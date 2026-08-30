"""add AI strategy analysis cache

Revision ID: e1c4d2f8a9b3
Revises: 9b4e07c3a208
Create Date: 2026-08-30 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e1c4d2f8a9b3"
down_revision = "9b4e07c3a208"
branch_labels = None
depends_on = None

UUID = sa.String(36)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "strategy_analysis_cache",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_text", sa.String(8000), nullable=False),
        sa.Column("provider_used", sa.String(16), server_default="mock", nullable=False),
        sa.Column("testability_status", sa.String(24), server_default="NEEDS_USER_INPUT", nullable=False),
        sa.Column("analysis", postgresql.JSONB(), nullable=True),
        sa.Column("strategy_spec", postgresql.JSONB(), nullable=True),
        sa.Column("converted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index(
        "ix_strategy_analysis_cache_workspace_id",
        "strategy_analysis_cache", ["workspace_id"],
    )
    op.create_index(
        "ix_strategy_analysis_cache_text_sha256",
        "strategy_analysis_cache", ["text_sha256"],
    )
    op.create_unique_constraint(
        "uq_strategy_analysis_ws_hash",
        "strategy_analysis_cache",
        ["workspace_id", "text_sha256"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_strategy_analysis_ws_hash", "strategy_analysis_cache", type_="unique")
    op.drop_index("ix_strategy_analysis_cache_text_sha256", table_name="strategy_analysis_cache")
    op.drop_index("ix_strategy_analysis_cache_workspace_id", table_name="strategy_analysis_cache")
    op.drop_table("strategy_analysis_cache")
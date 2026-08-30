"""widen execution_model / exit_reason on validation trades

Revision ID: 9b4e07c3a208
Revises: 8a3c05b2d1f7
Create Date: 2026-08-30 12:45:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9b4e07c3a208"
down_revision = "8a3c05b2d1f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "real_historical_validation_trades",
        "execution_model",
        type_=sa.String(40),
        existing_type=sa.String(32),
        existing_nullable=True,
    )
    op.alter_column(
        "real_historical_validation_trades",
        "exit_reason",
        type_=sa.String(48),
        existing_type=sa.String(32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "real_historical_validation_trades",
        "execution_model",
        type_=sa.String(32),
        existing_type=sa.String(40),
        existing_nullable=True,
    )
    op.alter_column(
        "real_historical_validation_trades",
        "exit_reason",
        type_=sa.String(32),
        existing_type=sa.String(48),
        existing_nullable=True,
    )
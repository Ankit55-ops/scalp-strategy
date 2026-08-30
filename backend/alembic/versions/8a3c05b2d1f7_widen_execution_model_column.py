"""widen execution_model column for longest enum value

Revision ID: 8a3c05b2d1f7
Revises: d9f1e2a4b7c3
Create Date: 2026-08-30 12:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8a3c05b2d1f7"
down_revision = "d9f1e2a4b7c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "real_historical_validation_runs",
        "execution_model",
        type_=sa.String(40),
        existing_type=sa.String(32),
        server_default="NEXT_CANDLE_OPEN",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "real_historical_validation_runs",
        "execution_model",
        type_=sa.String(32),
        existing_type=sa.String(40),
        server_default="NEXT_CANDLE_OPEN",
        existing_nullable=False,
    )
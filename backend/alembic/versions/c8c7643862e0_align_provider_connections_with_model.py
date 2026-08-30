"""align provider_connections with model

Revision ID: c8c7643862e0
Revises: 3e53fa76c144
Create Date: 2026-08-30 11:11:47.382430

"""
from alembic import op
import sqlalchemy as sa


revision = 'c8c7643862e0'
down_revision = '3e53fa76c144'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = [row[0] for row in bind.exec_driver_sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'provider_connections'"
    )]
    if "user_id" not in cols:
        op.add_column(
            "provider_connections",
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        )
        op.create_index(
            "ix_provider_connections_user_id", "provider_connections", ["user_id"]
        )
    if "display_name" not in cols:
        op.add_column("provider_connections", sa.Column("display_name", sa.String(128), nullable=True))
    if "connection_mode" not in cols:
        op.add_column("provider_connections", sa.Column("connection_mode", sa.String(24), nullable=True))
    if "environment" not in cols:
        op.add_column("provider_connections", sa.Column("environment", sa.String(12), nullable=True))
    if "encrypted_credentials" not in cols:
        op.add_column("provider_connections", sa.Column("encrypted_credentials", sa.String(4096), nullable=True))
    if "encrypted_connection_metadata" not in cols:
        op.add_column("provider_connections", sa.Column("encrypted_connection_metadata", sa.String(4096), nullable=True))
    if "capability_metadata" not in cols:
        op.add_column("provider_connections", sa.Column("capability_metadata", sa.JSON(), nullable=True))
    if "health_status" not in cols:
        op.add_column("provider_connections", sa.Column("health_status", sa.String(24), nullable=True))
    if "last_successful_data_at" not in cols:
        op.add_column("provider_connections", sa.Column("last_successful_data_at", sa.Float(), nullable=True))
    if "last_error_code" not in cols:
        op.add_column("provider_connections", sa.Column("last_error_code", sa.String(32), nullable=True))
    if "last_error_message_safe" not in cols:
        op.add_column("provider_connections", sa.Column("last_error_message_safe", sa.String(512), nullable=True))
    # Model maps JSONB; the table was created with `metadata json`. Cast in
    # place so ORM writes do not fail with a json/jsonb type mismatch.
    op.execute(
        "ALTER TABLE provider_connections ALTER COLUMN metadata TYPE jsonb USING metadata::jsonb"
    )
    op.execute(
        "ALTER TABLE provider_connections ALTER COLUMN provider SET DEFAULT 'exness'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    cols = [row[0] for row in bind.exec_driver_sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'provider_connections'"
    )]
    for col in (
        "user_id",
        "display_name",
        "connection_mode",
        "environment",
        "encrypted_credentials",
        "encrypted_connection_metadata",
        "capability_metadata",
        "health_status",
        "last_successful_data_at",
        "last_error_code",
        "last_error_message_safe",
    ):
        if col in cols:
            op.drop_column("provider_connections", col)
    op.execute(
        "ALTER TABLE provider_connections ALTER COLUMN metadata TYPE json USING metadata::json"
    )
    op.execute(
        "ALTER TABLE provider_connections ALTER COLUMN provider SET DEFAULT 'oanda'"
    )

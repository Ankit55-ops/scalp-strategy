"""kill switches table and paper risk tracking columns

Revision ID: 9f7ae2c0d3b1
Revises: c2f41a9b8d01
Create Date: 2026-08-29 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '9f7ae2c0d3b1'
down_revision = 'c2f41a9b8d01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'kill_switches',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('resource_id', sa.String(length=64), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'scope', 'resource_id', name='uq_kill_switch_ws_scope_res'),
    )
    op.create_index(op.f('ix_kill_switches_workspace_id'), 'kill_switches', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_kill_switches_scope'), 'kill_switches', ['scope'], unique=False)

    op.add_column('paper_accounts', sa.Column('day_key', sa.String(length=16), nullable=True))
    op.add_column('paper_accounts', sa.Column('day_start_equity', sa.Float(), nullable=True))
    op.add_column('paper_accounts', sa.Column('week_key', sa.String(length=16), nullable=True))
    op.add_column('paper_accounts', sa.Column('week_start_equity', sa.Float(), nullable=True))
    op.add_column('paper_accounts', sa.Column('equity_peak', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('paper_accounts', 'equity_peak')
    op.drop_column('paper_accounts', 'week_start_equity')
    op.drop_column('paper_accounts', 'week_key')
    op.drop_column('paper_accounts', 'day_start_equity')
    op.drop_column('paper_accounts', 'day_key')
    op.drop_index(op.f('ix_kill_switches_scope'), table_name='kill_switches')
    op.drop_index(op.f('ix_kill_switches_workspace_id'), table_name='kill_switches')
    op.drop_table('kill_switches')
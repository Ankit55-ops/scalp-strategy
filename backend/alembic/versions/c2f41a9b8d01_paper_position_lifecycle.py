"""paper position lifecycle columns

Revision ID: c2f41a9b8d01
Revises: af440c752e5f
Create Date: 2026-08-29 10:12:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c2f41a9b8d01'
down_revision = 'af440c752e5f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('paper_positions', sa.Column('order_id', sa.String(length=36), nullable=True))
    op.add_column('paper_positions', sa.Column('exit_ts', sa.Float(), nullable=True))
    op.add_column('paper_positions', sa.Column('exit_price', sa.Float(), nullable=True))
    op.add_column('paper_positions', sa.Column('gross_pnl', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('paper_positions', sa.Column('net_pnl', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('paper_positions', sa.Column('pips', sa.Float(), nullable=False, server_default='0.0'))
    op.add_column('paper_positions', sa.Column('exit_reason', sa.String(length=32), nullable=True))
    op.create_foreign_key(
        'fk_paper_positions_order_id_simulated_orders',
        'paper_positions', 'simulated_orders',
        ['order_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(op.f('ix_paper_positions_order_id'), 'paper_positions', ['order_id'], unique=False)
    op.add_column('backtest_jobs', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_backtest_jobs_idempotency_key'), 'backtest_jobs', ['idempotency_key'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_backtest_jobs_idempotency_key'), table_name='backtest_jobs')
    op.drop_column('backtest_jobs', 'idempotency_key')
    op.drop_index(op.f('ix_paper_positions_order_id'), table_name='paper_positions')
"""real market data providers and live checkers

Revision ID: 6142d2b1d825
Revises: 9f7ae2c0d3b1
Create Date: 2026-08-29 15:49:25.318966

"""
from alembic import op
import sqlalchemy as sa


revision = '6142d2b1d825'
down_revision = '9f7ae2c0d3b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('candles', sa.Column('source', sa.String(length=32), nullable=False, server_default='csv'))
    op.add_column('candles', sa.Column('bid_ask_basis', sa.String(length=24), nullable=False, server_default='mid'))
    op.add_column('candles', sa.Column('is_complete', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('ticks', sa.Column('source', sa.String(length=32), nullable=False, server_default=''))

    op.create_table(
        'provider_credentials',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=24), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False, server_default='default'),
        sa.Column('encrypted_secret', sa.String(length=2048), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_provider_credentials_workspace_id', 'provider_credentials', ['workspace_id'])

    op.create_table(
        'provider_connections',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=24), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='disconnected'),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('last_connected_at', sa.Float(), nullable=True),
        sa.Column('error', sa.String(length=512), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_provider_connections_provider', 'provider_connections', ['provider'])
    op.create_index('ix_provider_connections_workspace_id', 'provider_connections', ['workspace_id'])

    op.create_table(
        'instrument_mappings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=True),
        sa.Column('canonical_symbol', sa.String(length=16), nullable=False),
        sa.Column('provider', sa.String(length=24), nullable=False),
        sa.Column('provider_symbol', sa.String(length=32), nullable=False),
        sa.Column('display_symbol', sa.String(length=16), nullable=False),
        sa.Column('base_currency', sa.String(length=8), nullable=False),
        sa.Column('quote_currency', sa.String(length=8), nullable=False),
        sa.Column('pip_size', sa.Float(), nullable=False),
        sa.Column('price_precision', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('is_supported', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_instrument_mappings_canonical_symbol', 'instrument_mappings', ['canonical_symbol'])
    op.create_index('ix_instrument_mappings_workspace_id', 'instrument_mappings', ['workspace_id'])

    op.create_table(
        'market_feed_health',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=24), nullable=False),
        sa.Column('symbol', sa.String(length=16), nullable=False),
        sa.Column('feed_status', sa.String(length=24), nullable=False, server_default='CONNECTING'),
        sa.Column('last_quote_ts', sa.Float(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=True),
        sa.Column('last_error', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_market_feed_health_symbol', 'market_feed_health', ['symbol'])
    op.create_index('ix_market_feed_health_workspace_id', 'market_feed_health', ['workspace_id'])

    op.create_table(
        'market_data_gaps',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=24), nullable=False),
        sa.Column('symbol', sa.String(length=16), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('start_ts', sa.Float(), nullable=False),
        sa.Column('end_ts', sa.Float(), nullable=False),
        sa.Column('gap_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('detected_at', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_market_data_gaps_symbol', 'market_data_gaps', ['symbol'])
    op.create_index('ix_market_data_gaps_workspace_id', 'market_data_gaps', ['workspace_id'])

    op.create_table(
        'strategy_signal_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('strategy_id', sa.String(length=36), nullable=True),
        sa.Column('strategy_version', sa.String(length=64), nullable=True),
        sa.Column('symbol', sa.String(length=16), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('signal', sa.String(length=32), nullable=False),
        sa.Column('signal_label', sa.String(length=48), nullable=False),
        sa.Column('state', sa.String(length=24), nullable=False),
        sa.Column('blocked_reason', sa.String(length=512), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('price', sa.Float(), nullable=False, server_default='0'),
        sa.Column('spread_pips', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_strategy_signal_events_strategy_id', 'strategy_signal_events', ['strategy_id'])
    op.create_index('ix_strategy_signal_events_workspace_id', 'strategy_signal_events', ['workspace_id'])


def downgrade() -> None:
    op.drop_index('ix_strategy_signal_events_workspace_id', table_name='strategy_signal_events')
    op.drop_index('ix_strategy_signal_events_strategy_id', table_name='strategy_signal_events')
    op.drop_table('strategy_signal_events')
    op.drop_index('ix_market_data_gaps_workspace_id', table_name='market_data_gaps')
    op.drop_index('ix_market_data_gaps_symbol', table_name='market_data_gaps')
    op.drop_table('market_data_gaps')
    op.drop_index('ix_market_feed_health_workspace_id', table_name='market_feed_health')
    op.drop_index('ix_market_feed_health_symbol', table_name='market_feed_health')
    op.drop_table('market_feed_health')
    op.drop_index('ix_instrument_mappings_workspace_id', table_name='instrument_mappings')
    op.drop_index('ix_instrument_mappings_canonical_symbol', table_name='instrument_mappings')
    op.drop_table('instrument_mappings')
    op.drop_index('ix_provider_connections_workspace_id', table_name='provider_connections')
    op.drop_index('ix_provider_connections_provider', table_name='provider_connections')
    op.drop_table('provider_connections')
    op.drop_index('ix_provider_credentials_workspace_id', table_name='provider_credentials')
    op.drop_table('provider_credentials')
    op.drop_column('ticks', 'source')
    op.drop_column('candles', 'is_complete')
    op.drop_column('candles', 'bid_ask_basis')
    op.drop_column('candles', 'source')
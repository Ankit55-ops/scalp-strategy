"""add real historical + provider connection tables

Revision ID: d9f1e2a4b7c3
Revises: c8c7643862e0
Create Date: 2026-08-30 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9f1e2a4b7c3"
down_revision = "c8c7643862e0"
branch_labels = None
depends_on = None

UUID = sa.String(36)
NOW = sa.text("now()")


def _pk(name: str) -> list:
    return [sa.Column("id", UUID, primary_key=True)]


def _timestamps() -> list:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "provider_connection_capabilities",
        *_pk("id"),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capability", sa.String(48), nullable=False),
        sa.Column("availability", sa.String(24), server_default="not_verified", nullable=False),
        sa.Column("detail", sa.String(512), nullable=True),
        sa.Column("verified_at", sa.Float(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_provider_connection_capabilities_connection_id", "provider_connection_capabilities", ["connection_id"])
    op.create_index("ix_provider_connection_capabilities_capability", "provider_connection_capabilities", ["capability"])

    op.create_table(
        "provider_connection_health_events",
        *_pk("id"),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("feed_health", sa.String(24), nullable=True),
        sa.Column("detail_safe", sa.String(512), nullable=True),
        sa.Column("checked_at", sa.Float(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_provider_connection_health_events_connection_id", "provider_connection_health_events", ["connection_id"])

    op.create_table(
        "provider_instrument_mappings",
        *_pk("id"),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("provider_symbol", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(16), nullable=False),
        sa.Column("display_symbol", sa.String(16), nullable=False),
        sa.Column("asset_class", sa.String(24), nullable=True),
        sa.Column("base_currency", sa.String(8), nullable=False),
        sa.Column("quote_currency", sa.String(8), nullable=False),
        sa.Column("digits", sa.Integer(), nullable=True),
        sa.Column("pip_size", sa.Float(), server_default="0.0001", nullable=False),
        sa.Column("contract_size", sa.Float(), nullable=True),
        sa.Column("minimum_lot", sa.Float(), nullable=True),
        sa.Column("lot_step", sa.Float(), nullable=True),
        sa.Column("stop_level", sa.Float(), nullable=True),
        sa.Column("freeze_level", sa.Float(), nullable=True),
        sa.Column("trading_sessions", postgresql.JSONB(), nullable=True),
        sa.Column("swap_long", sa.Float(), nullable=True),
        sa.Column("swap_short", sa.Float(), nullable=True),
        sa.Column("is_supported", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("provider_metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("last_verified_at", sa.Float(), nullable=True),
        *_timestamps(),
    )
    for col in ("connection_id", "workspace_id", "provider", "provider_symbol", "canonical_symbol"):
        op.create_index(f"ix_provider_instrument_mappings_{col}", "provider_instrument_mappings", [col])

    op.create_table(
        "provider_historical_data_cache",
        *_pk("id"),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("provider_symbol", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("start_time_utc", sa.Float(), nullable=False),
        sa.Column("end_time_utc", sa.Float(), nullable=False),
        sa.Column("candle_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("data_hash", sa.String(64), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    for col in ("connection_id", "provider", "provider_symbol", "canonical_symbol", "timeframe"):
        op.create_index(f"ix_provider_historical_data_cache_{col}", "provider_historical_data_cache", [col])

    op.create_table(
        "mt5_gateway_agents",
        *_pk("id"),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("gateway_url", sa.String(512), nullable=True),
        sa.Column("device_name", sa.String(128), nullable=False),
        sa.Column("public_identity_pin", sa.String(256), nullable=True),
        sa.Column("encrypted_pairing_token", sa.String(4096), nullable=True),
        sa.Column("pairing_token_expires_at", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), server_default="PAIRING", nullable=False),
        sa.Column("ip_registered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_mt5_gateway_agents_workspace_id", "mt5_gateway_agents", ["workspace_id"])

    op.create_table(
        "mt5_gateway_pairing_events",
        *_pk("id"),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gateway_id", UUID, sa.ForeignKey("mt5_gateway_agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("detail_safe", sa.String(512), nullable=True),
        sa.Column("issued_at", sa.Float(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_mt5_gateway_pairing_events_workspace_id", "mt5_gateway_pairing_events", ["workspace_id"])

    op.create_table(
        "provider_connection_audit_logs",
        *_pk("id"),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("detail_safe", sa.String(1024), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_provider_connection_audit_logs_workspace_id", "provider_connection_audit_logs", ["workspace_id"])
    op.create_index("ix_provider_connection_audit_logs_connection_id", "provider_connection_audit_logs", ["connection_id"])

    op.create_table(
        "real_historical_validation_runs",
        *_pk("id"),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("strategy_version_id", UUID, sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("provider_id", sa.String(36), nullable=True),
        sa.Column("provider_name", sa.String(32), nullable=False),
        sa.Column("provider_connection_id", sa.String(36), nullable=True),
        sa.Column("provider_symbol", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(16), nullable=False),
        sa.Column("timeout", sa.String(8), nullable=False),
        sa.Column("start_time_utc", sa.Float(), nullable=False),
        sa.Column("end_time_utc", sa.Float(), nullable=False),
        sa.Column("account_currency", sa.String(8), server_default="USD", nullable=False),
        sa.Column("starting_balance", sa.Float(), server_default="100000", nullable=False),
        sa.Column("cost_model", postgresql.JSONB(), nullable=True),
        sa.Column("risk_profile_version", sa.String(32), nullable=True),
        sa.Column("source_data_type", sa.String(32), server_default="historical_candles", nullable=False),
        sa.Column("source_data_hash", sa.String(64), nullable=True),
        sa.Column("candle_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_candle_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("data_quality_score", sa.Float(), nullable=True),
        sa.Column("execution_model", sa.String(32), server_default="NEXT_CANDLE_OPEN", nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=True),
        sa.Column("strategy_spec", postgresql.JSONB(), nullable=True),
        sa.Column("run_status", sa.String(32), server_default="QUEUED", nullable=False),
        sa.Column("error_safe", sa.String(1024), nullable=True),
        sa.Column("execution_engine_version", sa.String(32), nullable=True),
        sa.Column("strategy_engine_version", sa.String(32), nullable=True),
        sa.Column("started_at_utc", sa.Float(), nullable=True),
        sa.Column("completed_at_utc", sa.Float(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("equity_curve", postgresql.JSONB(), nullable=True),
        sa.Column("drawdown_curve", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    for col in (
        "workspace_id",
        "strategy_id",
        "strategy_version_id",
        "connection_id",
        "idempotency_key",
        "correlation_id",
        "canonical_symbol",
        "timeout",
        "run_status",
    ):
        op.create_index(f"ix_real_historical_validation_runs_{col}", "real_historical_validation_runs", [col])

    op.create_table(
        "historical_data_quality_reports",
        *_pk("id"),
        sa.Column("validation_run_id", UUID, sa.ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("connection_id", UUID, sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("provider_name", sa.String(32), nullable=False),
        sa.Column("provider_symbol", sa.String(32), nullable=False),
        sa.Column("canonical_symbol", sa.String(16), nullable=False),
        sa.Column("timeout", sa.String(8), nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("requested_start", sa.Float(), nullable=False),
        sa.Column("requested_end", sa.Float(), nullable=False),
        sa.Column("actual_start", sa.Float(), nullable=True),
        sa.Column("actual_end", sa.Float(), nullable=True),
        sa.Column("expected_candles", sa.Integer(), server_default="0", nullable=False),
        sa.Column("received_candles", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_candles", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_candles_removed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warmup_candles_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("gap_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("gaps", postgresql.JSONB(), nullable=True),
        sa.Column("feed_delay_warning", sa.String(128), nullable=True),
        sa.Column("spread_availability", sa.String(24), server_default="not_checked", nullable=False),
        sa.Column("bid_ask_availability", sa.String(24), server_default="not_checked", nullable=False),
        sa.Column("cost_model_confidence", sa.String(24), server_default="high", nullable=False),
        sa.Column("quality_status", sa.String(24), server_default="FAIL", nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_historical_data_quality_reports_validation_run_id", "historical_data_quality_reports", ["validation_run_id"])

    op.create_table(
        "real_historical_validation_metrics",
        *_pk("id"),
        sa.Column("run_id", UUID, sa.ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_real_historical_validation_metrics_run_id", "real_historical_validation_metrics", ["run_id"])

    op.create_table(
        "real_historical_validation_trades",
        *_pk("id"),
        sa.Column("run_id", UUID, sa.ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("timeout", sa.String(8), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=True),
        sa.Column("entry_ts", sa.Float(), nullable=False),
        sa.Column("exit_ts", sa.Float(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("entry_price_basis", sa.String(24), server_default="mid", nullable=False),
        sa.Column("exit_price_basis", sa.String(24), server_default="mid", nullable=False),
        sa.Column("size_units", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=True),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column("gross_pnl", sa.Float(), server_default="0", nullable=False),
        sa.Column("net_pnl", sa.Float(), server_default="0", nullable=False),
        sa.Column("spread_cost", sa.Float(), server_default="0", nullable=False),
        sa.Column("slippage_cost", sa.Float(), server_default="0", nullable=False),
        sa.Column("commission", sa.Float(), server_default="0", nullable=False),
        sa.Column("swap", sa.Float(), server_default="0", nullable=False),
        sa.Column("pips", sa.Float(), server_default="0", nullable=False),
        sa.Column("risk_amount", sa.Float(), server_default="0", nullable=False),
        sa.Column("risk_reward_ratio", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(32), nullable=True),
        sa.Column("execution_model", sa.String(32), nullable=True),
        sa.Column("reasons_entry", postgresql.JSONB(), nullable=True),
        sa.Column("reasons_exit", postgresql.JSONB(), nullable=True),
        sa.Column("risk_engine_decision", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_real_historical_validation_trades_run_id", "real_historical_validation_trades", ["run_id"])

    op.create_table(
        "real_historical_validation_signals",
        *_pk("id"),
        sa.Column("run_id", UUID, sa.ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("signal", sa.String(32), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("blocked_reason", sa.String(512), nullable=True),
        sa.Column("price", sa.Float(), server_default="0", nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_real_historical_validation_signals_run_id", "real_historical_validation_signals", ["run_id"])

    op.create_table(
        "real_historical_validation_cost_events",
        *_pk("id"),
        sa.Column("run_id", UUID, sa.ForeignKey("real_historical_validation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_id", UUID, sa.ForeignKey("real_historical_validation_trades.id", ondelete="CASCADE"), nullable=True),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("amount", sa.Float(), server_default="0", nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_real_historical_validation_cost_events_run_id", "real_historical_validation_cost_events", ["run_id"])
    op.create_index("ix_real_historical_validation_cost_events_trade_id", "real_historical_validation_cost_events", ["trade_id"])


def downgrade() -> None:
    op.drop_table("real_historical_validation_cost_events")
    op.drop_table("real_historical_validation_signals")
    op.drop_table("real_historical_validation_trades")
    op.drop_table("real_historical_validation_metrics")
    op.drop_table("historical_data_quality_reports")
    op.drop_table("real_historical_validation_runs")
    op.drop_table("provider_connection_audit_logs")
    op.drop_table("mt5_gateway_pairing_events")
    op.drop_table("mt5_gateway_agents")
    op.drop_table("provider_historical_data_cache")
    op.drop_table("provider_instrument_mappings")
    op.drop_table("provider_connection_health_events")
    op.drop_table("provider_connection_capabilities")
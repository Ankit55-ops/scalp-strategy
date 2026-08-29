from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class BacktestJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "backtest_jobs"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped["BacktestRun | None"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class BacktestRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "backtest_runs"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_jobs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="running")
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float] = mapped_column(Float)
    equity_curve: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    drawdown_curve: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    robustness: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    job: Mapped["BacktestJob"] = relationship(back_populates="run")
    metrics: Mapped[list["BacktestMetric"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    trades: Mapped[list["SimulatedOrder"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BacktestMetric(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "backtest_metrics"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped["BacktestRun"] = relationship(back_populates="metrics")

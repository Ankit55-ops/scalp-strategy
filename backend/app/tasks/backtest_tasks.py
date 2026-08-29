"""Background backtest jobs, executed by an RQ worker when BACKTEST_ASYNC=1."""

from __future__ import annotations


def run_backtest_job(job_id: str) -> dict:
    from app.db.session import SessionLocal
    from app.models import BacktestJob, Strategy
    from app.services.backtest_service import run_backtest

    db = SessionLocal()
    try:
        job = db.get(BacktestJob, job_id)
        if job is None:
            return {"status": "missing"}
        if job.status in ("completed", "failed"):
            return {"status": job.status}
        job.status = "running"
        db.commit()
        strategy = db.get(Strategy, job.strategy_id)
        if strategy is None:
            job.status = "failed"
            job.error = "strategy not found"
            db.commit()
            return {"status": "failed", "error": job.error}
        try:
            run_backtest(db, job, strategy)
            return {"status": job.status}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.get(BacktestJob, job_id)
            job.status = "failed"
            job.error = str(exc)[:1000]
            db.commit()
            return {"status": "failed", "error": job.error}
    finally:
        db.close()
"""Background real-historical validation jobs, executed by an RQ worker when
VALIDATION_ASYNC=1. Synchronous execution is used by default in development.
"""

from __future__ import annotations


def run_validation_job(run_id: str) -> dict:
    from app.db.session import SessionLocal
    from app.models import RealHistoricalValidationRun
    from app.services.real_historical_validator import run_validation

    db = SessionLocal()
    try:
        run = db.get(RealHistoricalValidationRun, run_id)
        if run is None:
            return {"status": "missing"}
        if run.run_status not in ("QUEUED",):
            return {"status": run.run_status}
        run = run_validation(db, run.workspace_id, run)
        return {"status": run.run_status, "run_id": run.id}
    finally:
        db.close()
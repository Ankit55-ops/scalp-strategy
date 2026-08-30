"""AI Strategy Analyzer endpoints.

POST /strategy-analyzer/analyze
    Convert a plain-English strategy description into a structured analysis.
    Single AI call per unique prompt text (cached by SHA-256), strict input
    caps, per-workspace rate limiting, and no credential/key data in or out.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.analyzer import analyze_strategy
from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import User, Workspace
from app.schemas.ai_analyzer import StrategyAnalyzeRequest, StrategyAnalyzeResponse
from app.services.audit import AuditService

router = APIRouter(prefix="/strategy-analyzer", tags=["strategy-analyzer"])


@router.post("/analyze", response_model=StrategyAnalyzeResponse)
def analyze(
    payload: StrategyAnalyzeRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> StrategyAnalyzeResponse:
    try:
        result = analyze_strategy(db, str(ws.id), payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Safe audit trail: never log prompt contents, only its digest + status.
    try:
        AuditService(db).record(
            workspace_id=str(ws.id),
            actor_id=str(user.id),
            action="strategy_analyze",
            resource_type="strategy_analysis",
            payload={
                "provider": result.provider_used,
                "cache_hit": result.cache_hit,
                "status": result.analysis.testability_status,
                "sha256_prefix": result.text_sha256[:16],
            },
        )
    except (RuntimeError, ValueError, TypeError):  # pragma: no cover - audit must not break analysis
        db.rollback()

    return result
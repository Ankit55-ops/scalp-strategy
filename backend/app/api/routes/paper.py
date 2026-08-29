from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import Strategy, User, Workspace
from app.schemas.paper import PaperStatus, PaperTradingStart, PaperTradingStop
from app.services.paper_service import PaperTradingService

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])


@router.post("/start", status_code=201, response_model=PaperStatus)
def start_paper_trading(
    payload: PaperTradingStart,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PaperStatus:
    svc = PaperTradingService(db)
    svc.start(ws.id, payload.balance)
    return PaperStatus(**svc.status(ws.id))


@router.post("/stop", response_model=PaperStatus)
def stop_paper_trading(
    payload: PaperTradingStop,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PaperStatus:
    svc = PaperTradingService(db)
    svc.stop(ws.id, payload.close_positions)
    return PaperStatus(**svc.status(ws.id))


@router.get("/status", response_model=PaperStatus)
def paper_trading_status(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PaperStatus:
    svc = PaperTradingService(db)
    return PaperStatus(**svc.status(ws.id))


@router.get("/signals", response_model=dict)
def latest_signals(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    svc = PaperTradingService(db)
    strategies = db.query(Strategy).filter(Strategy.workspace_id == ws.id).all()
    out = []
    for s in strategies:
        try:
            out.append({"strategy": s.name, **svc.evaluate_strategy(s)})
        except Exception as exc:  # noqa: BLE001
            out.append({"strategy": s.name, "signal": "none", "error": str(exc)[:200]})
    return {"signals": out}

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import Strategy, User, Workspace
from app.schemas.paper import (
    PaperCloseResult,
    PaperOrderRequest,
    PaperOrderResult,
    PaperPositionOut,
    PaperStatus,
    PaperTradingStart,
    PaperTradingStop,
)
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
    try:
        svc.start(ws.id, payload.balance)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@router.post("/order", response_model=PaperOrderResult)
def place_paper_order(
    payload: PaperOrderRequest,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PaperOrderResult:
    svc = PaperTradingService(db)
    result = svc.place_order(ws.id, payload.strategy_id, payload.side, payload.size_units)
    if result.approved and result.position is not None:
        return PaperOrderResult(
            approved=True,
            position_id=result.position.id,
            order_id=result.order.id if result.order else None,
            symbol=result.position.symbol,
            entry_price=result.position.entry_price,
            stop_loss=result.position.stop_loss,
            take_profit=result.position.take_profit,
        )
    return PaperOrderResult(
        approved=False,
        reason=result.reason,
        correlation_id=result.correlation_id,
    )


@router.get("/positions", response_model=list[PaperPositionOut])
def list_open_positions(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[PaperPositionOut]:
    svc = PaperTradingService(db)
    return [PaperPositionOut(**p) for p in svc.open_positions(ws.id)]


@router.post("/positions/{position_id}/close", response_model=PaperCloseResult)
def close_paper_position(
    position_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> PaperCloseResult:
    svc = PaperTradingService(db)
    try:
        pos = svc.close_position(ws.id, position_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaperCloseResult(
        id=pos.id,
        status=pos.status,
        exit_price=round(pos.exit_price or 0.0, 5),
        net_pnl=round(pos.net_pnl, 4),
        pips=round(pos.pips, 2),
    )


@router.get("/trades", response_model=list[dict])
def list_closed_trades(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    svc = PaperTradingService(db)
    return svc.closed_trades(ws.id, limit=limit)


@router.get("/orders", response_model=list[dict])
def list_paper_orders(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
    status: str | None = Query(None),
) -> list[dict]:
    return PaperTradingService(db).paper_orders(ws.id, limit=limit, status=status)


@router.get("/fills", response_model=list[dict])
def list_paper_fills(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return PaperTradingService(db).paper_fills(ws.id, limit=limit)


@router.get("/margin-events", response_model=list[dict])
def list_margin_events(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return PaperTradingService(db).margin_events(ws.id, limit=limit)


@router.get("/account-state", response_model=PaperStatus)
def account_state(
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
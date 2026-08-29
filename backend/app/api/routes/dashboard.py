from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.backtest.sessions import in_session
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Alert, RiskEvent, Strategy, User
from app.providers.factory import get_market_data_provider
from app.risk.killswitch import KillSwitchRegistry
from app.schemas.strategy import SessionWindow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_SESSIONS = {
    "Asian": SessionWindow(name="Asian", start="00:00", end="07:00"),
    "London": SessionWindow(name="London", start="07:00", end="12:00"),
    "New York": SessionWindow(name="New York", start="12:00", end="17:00"),
    "London-New York Overlap": SessionWindow(name="London-NY Overlap", start="12:00", end="16:00"),
}


@router.get("/overview", response_model=dict)
def dashboard_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    now = datetime.now(timezone.utc).timestamp()
    active_strategies = db.query(Strategy).filter(Strategy.status == "active").count()
    alerts = db.query(Alert).filter(Alert.is_read.is_(False)).count()
    risk_events = db.query(RiskEvent).count()
    feed = get_market_data_provider()
    symbols = feed.list_symbols()

    open_sessions = [
        {
            "name": name,
            "start": w.start,
            "end": w.end,
            "active": in_session(now, [w]),
        }
        for name, w in _SESSIONS.items()
    ]
    return {
        "account": {"currency": "USD"},
        "paper_account": {
            "balance": 0.0,
            "equity": 0.0,
            "is_active": False,
        },
        "active_strategies": active_strategies,
        "daily_pnl": 0.0,
        "drawdown_pct": 0.0,
        "risk_alerts": alerts,
        "risk_events": risk_events,
        "sessions": open_sessions,
        "data_feed": {"provider": feed.name, "symbols": len(symbols), "ok": True},
        "kill_switch": KillSwitchRegistry().is_global_halted(),
        "utc_now": datetime.now(timezone.utc).isoformat(),
        "config": {"app_env": get_settings().APP_ENV, "llm_provider": get_settings().LLM_PROVIDER},
    }


@router.get("/symbols", response_model=list[dict])
def list_symbols(
    user: User = Depends(get_current_user),
) -> list[dict]:
    feed = get_market_data_provider()
    return [{"canonical": s, "provider": feed.name} for s in feed.list_symbols()]

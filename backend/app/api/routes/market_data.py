"""Market data endpoints: providers, instruments, quotes, candles, feed health."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status as http_status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.core.config import get_settings
from app.db.session import get_db
from app.models import EconomicEvent, User, Workspace
from app.providers.csv_provider import CSVMarketDataProvider
from app.providers.factory import get_market_data_provider
from app.schemas.market import (
    ConnectProviderRequest,
    ProviderStatusResponse,
    QuoteResponse,
)
from app.services import feed_health
from app.services.ingestion import ingestion_status, start_ingestion, stop_ingestion
from app.services.provider_service import (
    ProviderConnectionError,
    connect_provider,
    detect_gaps,
    get_active_provider,
    get_candles_from_db,
    persist_candles,
    provider_status,
)

router = APIRouter(prefix="/market-data", tags=["market-data"])

MAX_CANDLES = 5000
MAX_SPAN_DAYS = 366
_SAFE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,16}$")
_SAFE_TIMEFRAMES = frozenset({"M1", "M5", "M15", "M30", "H1", "H4", "D1"})


@router.get("/economic-calendar", response_model=list[dict])
def economic_calendar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=90),
    currency: str | None = Query(None),
    impact: str | None = Query(None),
) -> list[dict]:
    start = datetime.now(timezone.utc).timestamp()
    end = start + days * 86400
    q = db.query(EconomicEvent).filter(
        EconomicEvent.event_time >= start, EconomicEvent.event_time <= end
    )
    if currency:
        q = q.filter(EconomicEvent.currency == currency.upper())
    if impact:
        q = q.filter(EconomicEvent.impact == impact.lower())
    rows = q.order_by(EconomicEvent.event_time.asc()).limit(200).all()
    return [
        {
            "id": e.id,
            "country": e.country,
            "currency": e.currency,
            "name": e.name,
            "impact": e.impact,
            "event_time": e.event_time,
            "event_time_iso": datetime.fromtimestamp(e.event_time, tz=timezone.utc).isoformat(),
            "actual": e.actual,
            "forecast": e.forecast,
            "previous": e.previous,
        }
        for e in rows
    ]


@router.get("/symbols", response_model=list[dict])
def list_symbols(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[dict]:
    provider = get_active_provider(db, workspace.id)
    symbols = [{"symbol": s, "provider": provider.name} for s in provider.list_symbols()]
    csv = CSVMarketDataProvider()
    seen = {s["symbol"] for s in symbols}
    for s in csv.list_symbols():
        if s not in seen:
            symbols.append({"symbol": s, "provider": "csv"})
    return symbols


@router.post("/providers/connect", response_model=dict)
def connect(
    payload: ConnectProviderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> dict:
    try:
        result = connect_provider(
            db,
            workspace.id,
            payload.provider,
            payload.api_key,
            payload.account_id,
            payload.env or ("practice" if payload.provider == "oanda" else None),
        )
    except ProviderConnectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "provider": result.provider,
        "status": result.status,
        "latency_ms": result.latency_ms,
        "detail": result.detail,
        "instruments": result.instruments[:500],
    }


@router.get("/providers/status", response_model=ProviderStatusResponse)
def status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> ProviderStatusResponse:
    return provider_status(db, workspace.id)


@router.get("/instruments", response_model=list[dict])
def instruments(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[dict]:
    provider = get_active_provider(db, workspace.id)
    try:
        rows = provider.list_instruments()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"provider unavailable: {exc}") from exc
    return [
        {
            "canonical_symbol": r.canonical_symbol,
            "display_symbol": r.display_symbol,
            "provider_symbol": r.provider_symbol,
            "base_currency": r.base_currency,
            "quote_currency": r.quote_currency,
            "pip_size": r.pip_size,
            "price_precision": r.price_precision,
            "contract_size": r.contract_size,
            "minimum_lot": r.minimum_lot,
            "data_provider": r.data_provider,
            "data_delay_status": r.data_delay_status,
        }
        for r in rows
    ]


@router.get("/quotes/{symbol}", response_model=QuoteResponse)
def quote(
    symbol: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> QuoteResponse:
    canon = symbol.upper().replace("/", "").replace("_", "")
    try:
        q = feed_health.get_quote(db, workspace.id, canon)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"quote unavailable: {exc}") from exc
    return QuoteResponse(**{k: q.get(k) for k in QuoteResponse.model_fields})


@router.get("/feed-health", response_model=list[dict])
def feed_health_list(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> list[dict]:
    return feed_health.list_feed_health(db, workspace.id)


@router.post("/ingest/start", response_model=dict)
def ingest_start(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> dict:
    start_ingestion(workspace.id)
    return {"started": True, **ingestion_status(workspace.id)}


@router.post("/ingest/stop", response_model=dict)
def ingest_stop(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> dict:
    stop_ingestion(workspace.id)
    return {"started": False, **ingestion_status(workspace.id)}


@router.get("/ingest/status", response_model=dict)
def ingest_status_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
) -> dict:
    return ingestion_status(workspace.id)


@router.get("/candles/{symbol}", response_model=dict)
def candles(
    symbol: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(get_current_workspace),
    timeframe: str = Query("M5", pattern="^(M1|M5|M15|M30|H1|H4|D1)$"),
    start: str | None = Query(None, description="ISO start (default: 7 days ago)"),
    end: str | None = Query(None, description="ISO end (default: now)"),
    provider: str | None = Query(None, description="force oanda|twelvedata|csv|mock"),
    save: bool = Query(True, description="persist fetched candles to the DB"),
) -> dict:
    canon = symbol.upper().replace("/", "").replace("_", "")
    end_dt = _parse_dt(end) if end else datetime.now(timezone.utc)
    start_dt = _parse_dt(start) if start else end_dt - timedelta(days=7)
    if start_dt > end_dt:
        raise HTTPException(status_code=422, detail="start must be <= end")
    if (end_dt - start_dt).days > MAX_SPAN_DAYS:
        raise HTTPException(status_code=422, detail=f"requested span exceeds {MAX_SPAN_DAYS} days")

    select = provider.lower() if provider else None
    if select == "csv":
        data_provider = CSVMarketDataProvider()
    elif select and select not in ("oanda", "twelvedata", "mock"):
        raise HTTPException(status_code=422, detail=f"unknown provider '{provider}'")
    elif select:
        data_provider = get_market_data_provider(select)
    else:
        data_provider = get_active_provider(db, workspace.id)

    try:
        rows = data_provider.get_historical_candles(canon, timeframe, start_dt, end_dt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"candles unavailable: {exc}") from exc
    if len(rows) > MAX_CANDLES:
        rows = rows[:MAX_CANDLES]
    source = data_provider.name
    gaps: list[int] = []
    if save and source not in ("mock",):
        persist_candles(db, workspace.id, source, canon, timeframe, rows)
        gaps = detect_gaps(db, workspace.id, source, canon, timeframe, rows)
    return {
        "symbol": canon,
        "timeframe": timeframe,
        "provider": source,
        "count": len(rows),
        "gaps": gaps,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "candles": rows,
    }


@router.post("/import", response_model=dict)
async def import_csv(
    symbol: str = "",
    timeframe: str = "M5",
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    sym = symbol.strip().upper()
    tf = timeframe.strip().upper()
    if not _SAFE_SYMBOL_RE.match(sym):
        raise HTTPException(status_code=422, detail="symbol must match ^[A-Z0-9]{1,16}$")
    if tf not in _SAFE_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe must be one of {sorted(_SAFE_TIMEFRAMES)}")

    settings = get_settings()
    content = await file.read(settings.UPLOAD_MAX_BYTES + 1)
    if len(content) > settings.UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file exceeds {settings.UPLOAD_MAX_BYTES} byte upload limit",
        )

    fd, tmp = tempfile.mkstemp(prefix="fxscalper_upload_", suffix=".csv")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        provider = get_market_data_provider("csv")
        count = provider.import_file(sym, tf, tmp)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"failed to import: {exc}") from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"imported": count, "symbol": sym, "timeframe": tf}


def _parse_dt(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid datetime: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
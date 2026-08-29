"""Provider credential management, resolution, and candle persistence.

API keys are encrypted at rest with ``app.core.secrets`` and are never
returned to clients. The runtime market-data source is chosen by:

1. ``MARKET_DATA_PROVIDER`` env var, if it points at a configured real
   provider (``oanda`` / ``twelvedata``) or ``csv``;
2. otherwise the workspace's active provider connection (created via
   ``POST /api/market-data/providers/connect``), if healthy;
3. otherwise the ``mock`` provider (used only when no real source is
   configured — never a silent production default).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.secrets import decrypt_secret, encrypt_secret
from app.models import (
    Candle,
    InstrumentMapping,
    MarketDataGap,
    MarketFeedHealth,
    ProviderConnection,
    ProviderCredential,
)
from app.providers.factory import get_market_data_provider

PROVIDER_DISPLAY = {
    "oanda": "OANDA",
    "twelvedata": "Twelve Data",
    "csv": "CSV import",
    "mock": "Simulated (mock)",
}


class ProviderConnectionError(RuntimeError):
    pass


def _safe_error(exc: Exception, secrets: tuple[str, ...] = ()) -> str:
    """Error text safe to persist/return: capped length and key redaction."""
    msg = str(exc) or exc.__class__.__name__
    for secret in secrets:
        if secret:
            msg = msg.replace(str(secret), "***redacted***")
    return msg[:400]


@dataclass
class ConnectionResult:
    provider: str
    status: str
    latency_ms: float | None
    detail: str
    instruments: list[str]


def build_provider(provider: str, api_key: str | None = None, account_id: str | None = None, env: str | None = None):
    """Construct a provider instance from explicit credentials (no globals)."""
    from app.providers.oanda import OandaMarketDataProvider
    from app.providers.twelvedata import TwelveDataMarketDataProvider

    provider = provider.lower()
    if provider == "oanda":
        if not api_key or not account_id:
            raise ProviderConnectionError("OANDA requires an API key and account id")
        return OandaMarketDataProvider(api_key=api_key, account_id=account_id, env=env or "practice")
    if provider == "twelvedata":
        if not api_key:
            raise ProviderConnectionError("Twelve Data requires an API key")
        return TwelveDataMarketDataProvider(api_key=api_key)
    raise ProviderConnectionError(f"provider '{provider}' is not supported")


def save_credentials(
    db: Session,
    workspace_id: str,
    provider: str,
    api_key: str,
    account_id: str | None = None,
    env: str | None = None,
) -> ProviderCredential:
    """Encrypt + store workspace credentials and mark them active."""
    provider = provider.lower()
    existing = (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.provider == provider,
        )
        .first()
    )
    row = existing or ProviderCredential(workspace_id=workspace_id, provider=provider)
    row.label = f"{provider} active"
    row.encrypted_secret = encrypt_secret(api_key)
    row.config = {"account_id": account_id or "", "env": env or ""}
    row.is_active = True
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_credentials(db: Session, workspace_id: str, provider: str) -> ProviderCredential | None:
    return (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.provider == provider,
            ProviderCredential.is_active.is_(True),
        )
        .first()
    )


def connect_provider(db: Session, workspace_id: str, provider: str, api_key: str, account_id: str | None = None, env: str | None = None) -> ConnectionResult:
    provider = provider.lower()
    try:
        instance = build_provider(provider, api_key, account_id, env)
        health = instance.health_check()
        if health.status != "ok":
            raise ProviderConnectionError(health.detail or "provider returned unhealthy status")
        cred = save_credentials(db, workspace_id, provider, api_key, account_id, env)
        _upsert_connection(db, workspace_id, provider, status="connected", latency_ms=health.latency_ms, error=None)
        symbols = []
        try:
            symbols = instance.list_symbols()
            _persist_instrument_mappings(db, workspace_id, provider, instance)
        except Exception:  # noqa: BLE001
            pass
        return ConnectionResult(
            provider=provider,
            status="connected",
            latency_ms=health.latency_ms,
            detail="connected and healthy",
            instruments=symbols,
        )
    except Exception as exc:  # noqa: BLE001
        _upsert_connection(db, workspace_id, provider, status="error", latency_ms=None, error=_safe_error(exc, secrets=(api_key,)))
        db.rollback()
        raise ProviderConnectionError(_safe_error(exc, secrets=(api_key,))) from exc


def get_active_provider(db: Session, workspace_id: str):
    """Resolve the runtime provider for a workspace (see module docstring)."""
    from app.core.config import get_settings

    configured = get_settings().MARKET_DATA_PROVIDER.lower()
    if configured in ("oanda", "twelvedata"):
        try:
            return get_market_data_provider(configured)
        except RuntimeError:
            pass  # misconfigured env -> fall through to workspace creds
    # Workspace-level credentials, most recently used first.
    creds = (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.is_active.is_(True),
        )
        .order_by(ProviderCredential.updated_at.desc())
        .all()
    )
    for cred in creds:
        try:
            return build_provider(
                cred.provider,
                decrypt_secret(cred.encrypted_secret),
                (cred.config or {}).get("account_id"),
                (cred.config or {}).get("env"),
            )
        except Exception:  # noqa: BLE001
            continue
    if configured == "csv":
        return get_market_data_provider("csv")
    return get_market_data_provider("mock")


def provider_status(db: Session, workspace_id: str) -> dict:
    from app.core.config import get_settings

    configured = get_settings().MARKET_DATA_PROVIDER.lower()
    provider = get_active_provider(db, workspace_id)
    health = provider.health_check()
    conns = db.query(ProviderConnection).filter(ProviderConnection.workspace_id == workspace_id).all()
    connections = {
        c.provider: {
            "status": c.status,
            "latency_ms": c.latency_ms,
            "last_connected_at": c.last_connected_at,
            "error": c.error,
        }
        for c in conns
    }
    # Persist a feed-health row for the active provider if missing.
    _sync_feed_health(db, workspace_id, provider.name)
    return {
        "active_provider": provider.name,
        "active_provider_label": PROVIDER_DISPLAY.get(provider.name, provider.name),
        "env_selected": configured,
        "bid_ask_basis": provider.bid_ask_basis,
        "health": {
            "status": health.status,
            "latency_ms": health.latency_ms,
            "detail": health.detail,
            "checked_at": health.checked_at,
        },
        "connections": connections,
        "stale_threshold_seconds": get_settings().STALE_QUOTE_THRESHOLD_SECONDS,
    }


def _upsert_connection(db: Session, workspace_id: str, provider: str, status: str, latency_ms: float | None, error: str | None) -> None:
    row = (
        db.query(ProviderConnection)
        .filter(ProviderConnection.workspace_id == workspace_id, ProviderConnection.provider == provider)
        .first()
    )
    now_ts = datetime.now(timezone.utc).timestamp()
    if row is None:
        row = ProviderConnection(workspace_id=workspace_id, provider=provider)
        db.add(row)
    row.status = status
    row.latency_ms = latency_ms
    row.error = error
    if status == "connected":
        row.last_connected_at = now_ts
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def _persist_instrument_mappings(db: Session, workspace_id: str, provider_name: str, instance) -> None:
    try:
        for inst in instance.list_instruments():
            row = (
                db.query(InstrumentMapping)
                .filter(
                    InstrumentMapping.workspace_id == workspace_id,
                    InstrumentMapping.provider == provider_name,
                    InstrumentMapping.canonical_symbol == inst.canonical_symbol,
                )
                .first()
            )
            if row is None:
                row = InstrumentMapping(workspace_id=workspace_id, provider=provider_name)
                db.add(row)
            row.provider_symbol = inst.provider_symbol
            row.display_symbol = inst.display_symbol
            row.base_currency = inst.base_currency
            row.quote_currency = inst.quote_currency
            row.pip_size = inst.pip_size
            row.price_precision = inst.price_precision
            row.is_supported = True
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def _sync_feed_health(db: Session, workspace_id: str, provider_name: str) -> None:
    try:
        row = (
            db.query(MarketFeedHealth)
            .filter(
                MarketFeedHealth.workspace_id == workspace_id,
                MarketFeedHealth.provider == provider_name,
                MarketFeedHealth.symbol == "*",
            )
            .first()
        )
        if row is None:
            db.add(
                MarketFeedHealth(
                    workspace_id=workspace_id,
                    provider=provider_name,
                    symbol="*",
                    feed_status="CONNECTING",
                    last_error=None,
                )
            )
            db.commit()
    except SQLAlchemyError:
        db.rollback()


# -- candle persistence -----------------------------------------------------
def persist_candles(db: Session, workspace_id: str, provider_name: str, symbol: str, timeframe: str, candles: list[dict]) -> int:
    """Upsert normalized candles into the `candles` table; returns rows written."""
    written = 0
    seen_ts: set[float] = set()
    for c in candles:
        ts = float(c["ts"])
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        row = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == timeframe, Candle.ts == ts)
            .first()
        )
        if row is None:
            db.add(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=ts,
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c.get("volume", 0.0),
                    source=c.get("source", provider_name),
                    bid_ask_basis=c.get("bid_ask_basis", "mid"),
                    is_complete=c.get("is_complete", True),
                )
            )
            written += 1
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return written


def get_candles_from_db(db: Session, symbol: str, timeframe: str, start: float, end: float) -> list[dict]:
    rows = (
        db.query(Candle)
        .filter(Candle.symbol == symbol, Candle.timeframe == timeframe)
        .filter(Candle.ts >= start, Candle.ts <= end)
        .order_by(Candle.ts.asc())
        .all()
    )
    return [
        {
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "ts": r.ts,
            "open_time_utc": datetime.fromtimestamp(r.ts, tz=timezone.utc).isoformat(),
            "close_time_utc": datetime.fromtimestamp(r.ts, tz=timezone.utc).isoformat(),
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "source": r.source,
            "bid_ask_basis": r.bid_ask_basis,
            "is_complete": r.is_complete,
        }
        for r in rows
    ]


def detect_gaps(db: Session, workspace_id: str, provider_name: str, symbol: str, timeframe: str, candles: list[dict]) -> list[int]:
    """Find gaps between consecutive candle open times; record them for audit."""
    from app.providers.models import _tf_seconds

    step = _tf_seconds(timeframe)
    gaps: list[int] = []
    prev: float | None = None
    for c in candles:
        ts = float(c["ts"])
        if prev is not None and ts - prev > step * 2:
            gaps.append(int(ts - prev - step))
        prev = ts
    if gaps and workspace_id:
        try:
            db.add(
                MarketDataGap(
                    workspace_id=workspace_id,
                    provider=provider_name,
                    symbol=symbol,
                    timeframe=timeframe,
                    start_ts=float(candles[0]["ts"]),
                    end_ts=float(candles[-1]["ts"]),
                    gap_count=len(gaps),
                    detected_at=datetime.now(timezone.utc).timestamp(),
                )
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
    return gaps
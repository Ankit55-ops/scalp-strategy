"""Feed-health tracking and quote-staleness detection.

Every quote pulled through this module is timestamped at receipt and compared
against the configured freshness threshold. States follow the platform
contract: CONNECTING / LIVE / DEGRADED / STALE / DISCONNECTED / RATE_LIMITED /
MAINTENANCE. A closed market is reported as MAINTENANCE (not STALE), because a
stale flag on a closed pair would be meaningless and would block legitimate
research data access.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.models import MarketFeedHealth
from app.providers.factory import get_market_data_provider
from app.services.provider_service import get_active_provider

_lock = threading.Lock()
# key: (workspace_id, provider, symbol) -> {last_ts, last_status, last_error}
_latest: dict[tuple[str, str, str], dict] = {}


def _key(workspace_id: str, provider: str, symbol: str) -> tuple[str, str, str]:
    return (workspace_id, provider, symbol.upper())


def stale_threshold_seconds() -> float:
    return float(get_settings().STALE_QUOTE_THRESHOLD_SECONDS)


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


# per-workspace active-provider bid/ask basis captured at resolution time
_provider_basis: dict[str, str] = {}


def set_provider_basis(workspace_id: str, basis: str) -> None:
    with _lock:
        _provider_basis[workspace_id] = basis


def get_provider_basis(workspace_id: str) -> str:
    with _lock:
        return _provider_basis.get(workspace_id, "mid")


def mark_quote_seen(workspace_id: str, provider: str, symbol: str, ts: float) -> None:
    with _lock:
        _latest[_key(workspace_id, provider, symbol)] = {
            "last_ts": ts,
            "received_at": _now(),
            "status": None,
            "error": None,
        }


def mark_feed_error(workspace_id: str, provider: str, symbol: str, error: str) -> None:
    with _lock:
        _latest[_key(workspace_id, provider, symbol)] = {
            "last_ts": None,
            "received_at": _now(),
            "status": "DISCONNECTED",
            "error": error[:512],
        }


def feed_state(workspace_id: str, provider: str, symbol: str, market_status: str = "open", latency_ms: float | None = None) -> str:
    """Compute the current feed-health state for one symbol."""
    with _lock:
        state = _latest.get(_key(workspace_id, provider, symbol), {})
    if state.get("error"):
        return "DISCONNECTED"
    last_ts = state.get("last_ts")
    if last_ts is None:
        return "CONNECTING"
    if market_status in ("closed", "post", "pre"):
        return "MAINTENANCE"
    age = _now() - float(last_ts)
    threshold = stale_threshold_seconds()
    if age > threshold:
        return "STALE"
    if latency_ms is not None and latency_ms > 5000:
        return "DEGRADED"
    return "LIVE"


def get_quote(db, workspace_id: str, symbol: str, mark_stale: bool = True) -> dict:
    """Fetch a normalized quote for a symbol from the workspace provider.

    Sets ``is_stale`` and adds ``feed_state`` based on the freshness tracker.
    """
    provider = get_active_provider(db, workspace_id)
    quote = provider.get_latest_quote(symbol)
    mark_quote_seen(workspace_id, provider.name, symbol, float(quote.get("ts") or _now()))
    state = feed_state(workspace_id, provider.name, symbol, quote.get("market_status", "open"), quote.get("latency_ms"))
    quote["is_stale"] = mark_stale and state == "STALE"
    quote["feed_state"] = state
    quote["provider"] = provider.name
    quote["bid_ask_basis"] = provider.bid_ask_basis
    _persist_health(db, workspace_id, provider.name, symbol, state, quote.get("latency_ms"), None)
    return quote


def list_feed_health(db, workspace_id: str) -> list[dict]:
    provider = get_active_provider(db, workspace_id)
    rows = (
        db.query(MarketFeedHealth)
        .filter(MarketFeedHealth.workspace_id == workspace_id, MarketFeedHealth.provider == provider.name)
        .filter(MarketFeedHealth.symbol != "*")
        .order_by(MarketFeedHealth.symbol.asc())
        .all()
    )
    return [
        {
            "symbol": row.symbol,
            "provider": row.provider,
            "feed_status": row.feed_status,
            "last_quote_ts": row.last_quote_ts,
            "latency_ms": row.latency_ms,
            "last_error": row.last_error,
        }
        for row in rows
    ]


def _persist_health(db, workspace_id: str, provider_name: str, symbol: str, state: str, latency_ms: float | None, error: str | None) -> None:
    row = (
        db.query(MarketFeedHealth)
        .filter(
            MarketFeedHealth.workspace_id == workspace_id,
            MarketFeedHealth.provider == provider_name,
            MarketFeedHealth.symbol == symbol.upper(),
        )
        .first()
    )
    if row is None:
        row = MarketFeedHealth(workspace_id=workspace_id, provider=provider_name, symbol=symbol.upper())
        db.add(row)
    row.feed_status = state
    row.last_quote_ts = _now()
    row.latency_ms = latency_ms
    if error:
        row.last_error = error[:512]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def stale_supported_symbols(db, workspace_id: str):
    """Return canonical symbols whose feed is currently stale/disconnected.

    Used by paper-trading and the strategy checker to block new decisions.
    A symbol that has never streamed a quote (CONNECTING) is not treated as a
    dead feed — there is no evidence of a problem yet — so it does not block.
    """
    provider = get_active_provider(db, workspace_id)
    stale: set[str] = set()
    for symbol in provider.list_symbols():
        state = feed_state(workspace_id, provider.name, symbol)
        if state in ("STALE", "DISCONNECTED", "DEGRADED"):
            stale.add(symbol.upper())
    return stale
"""Normalized market-data models shared across providers.

Candles and quotes intentionally stay dict-shaped downstream (the backtester
and paper service consume ``ts/open/high/low/close/volume`` and ``bid/ask``),
but every provider is expected to emit the extra normalized keys documented
here so the platform can display data provenance, completeness, and staleness
without rewriting consumers.

All timestamps are stored as epoch seconds UTC (the ``ts`` key) and mirrored as
ISO-8601 UTC strings (``*_utc`` keys) for the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Candle keys every provider must emit (dict-shaped for backtester compat).
CANDLE_KEYS = (
    "symbol",
    "timeframe",
    "ts",
    "open_time_utc",
    "close_time_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "is_complete",
    "source",
    "bid_ask_basis",
)

# Quote keys every provider must emit.
QUOTE_KEYS = (
    "symbol",
    "provider_symbol",
    "ts",
    "timestamp_utc",
    "bid",
    "ask",
    "mid",
    "spread_price",
    "spread_pips",
    "provider_timestamp",
    "received_timestamp",
    "latency_ms",
    "source",
    "is_stale",
    "market_status",
    "data_delay_status",
)

FEED_STATES = (
    "CONNECTING",
    "LIVE",
    "DEGRADED",
    "STALE",
    "DISCONNECTED",
    "RATE_LIMITED",
    "MAINTENANCE",
)


@dataclass(frozen=True)
class InstrumentMetadata:
    """Canonical instrument description independent of the data vendor."""

    canonical_symbol: str
    display_symbol: str
    provider_symbol: str
    base_currency: str
    quote_currency: str
    pip_size: float
    price_precision: int = 5
    quantity_precision: int = 2
    contract_size: float = 100000.0
    minimum_lot: float | None = None
    lot_step: float | None = None
    trading_sessions: dict[str, str] | None = None
    margin_metadata: dict[str, Any] | None = None
    data_provider: str = "unknown"
    data_delay_status: str = "realtime"
    bid_ask_basis: str = "provider_defined"


@dataclass(frozen=True)
class MarketStatus:
    symbol: str
    market_status: str  # open | closed | pre | post | unknown
    reason: str | None = None
    provider_symbol: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    status: str  # ok | degraded | unavailable
    latency_ms: float | None = None
    detail: str | None = None
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def quote_ts(quote: dict) -> float:
    return float(quote.get("ts") or 0.0)


def build_candle(
    symbol: str,
    timeframe: str,
    ts: float | datetime,
    open: float,  # noqa: A002
    high: float,
    low: float,
    close: float,
    volume: float = 0.0,
    source: str = "unknown",
    is_complete: bool = True,
    bid_ask_basis: str = "mid",
) -> dict:
    """Return a normalized candle dict (backtester-compatible)."""
    ts_f = ts.timestamp() if isinstance(ts, datetime) else float(ts)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "ts": ts_f,
        "open_time_utc": _iso(ts_f),
        "close_time_utc": _iso(ts_f + _tf_seconds(timeframe)),
        "open": round(float(open), 8),
        "high": round(float(high), 8),
        "low": round(float(low), 8),
        "close": round(float(close), 8),
        "volume": float(volume or 0.0),
        "is_complete": bool(is_complete),
        "source": source,
        "bid_ask_basis": bid_ask_basis,
    }


def build_quote(
    symbol: str,
    bid: float,
    ask: float,
    ts: float | datetime | None = None,
    provider_symbol: str | None = None,
    source: str = "unknown",
    market_status: str = "open",
    data_delay_status: str = "realtime",
    received_ts: float | None = None,
    instrument: InstrumentMetadata | None = None,
) -> dict:
    """Return a normalized quote dict with bid/ask/mid/spread/latency fields."""
    now = datetime.now(timezone.utc).timestamp()
    ts_f = ts.timestamp() if isinstance(ts, datetime) else (float(ts) if ts else now)
    received = received_ts if received_ts is not None else now
    pip = (instrument.pip_size if instrument else None) or _pip_for(symbol)
    return {
        "symbol": _canonical(symbol),
        "provider_symbol": provider_symbol or _canonical(symbol),
        "ts": ts_f,
        "timestamp_utc": _iso(ts_f),
        "bid": round(float(bid), 8),
        "ask": round(float(ask), 8),
        "mid": round((float(bid) + float(ask)) / 2.0, 8),
        "spread_price": round(float(ask) - float(bid), 8),
        "spread_pips": round((float(ask) - float(bid)) / pip, 3),
        "provider_timestamp": _iso(ts_f),
        "received_timestamp": _iso(received),
        "latency_ms": max(0.0, round((received - ts_f) * 1000.0, 2)) if ts_f else None,
        "source": source,
        "is_stale": False,
        "market_status": market_status,
        "data_delay_status": data_delay_status,
    }


def _canonical(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("_", "")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _pip_for(symbol: str) -> float:
    return 0.01 if _canonical(symbol).endswith("JPY") else 0.0001


_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def _tf_seconds(timeframe: str) -> int:
    return _TF_SECONDS.get(timeframe.upper(), 300)
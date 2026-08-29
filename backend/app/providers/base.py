"""Market data provider abstractions.

Providers emit **normalized** candles and quotes (see ``app.providers.models``)
so vendors like OANDA (``EUR_USD``) and Twelve Data (``EUR/USD``) map onto a
single canonical form (``EURUSD``). The interface is synchronous: the
backtester, strategy checker, and paper service are synchronous consumers, and
real REST providers are called with ``requests``/``httpx``. Live ``stream_quotes``
is a polling generator over the provider's REST pricing endpoint; a WebSocket
stream is planned but not required by the current consumers.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterator

from app.providers.models import (
    InstrumentMetadata,
    MarketStatus,
    ProviderHealth,
    build_quote,
)


class MarketDataProvider(ABC):
    name = "base"
    # How raw data is referenced: bid | ask | mid | provider_defined.
    bid_ask_basis = "mid"

    # -- discovery ---------------------------------------------------------
    @abstractmethod
    def list_symbols(self) -> list[str]:
        """Canonical symbols (e.g. EURUSD) this provider can serve."""

    @abstractmethod
    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Normalized candle dicts sorted ascending by open time."""

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """Normalized quote dict with bid/ask/mid/spread/latency fields."""

    @abstractmethod
    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        """Latest spread in pips for a symbol."""

    def list_instruments(self) -> list[InstrumentMetadata]:
        """Rich instrument metadata; default derives from ``list_symbols``."""
        return [self.get_instrument_metadata(s) or _default_instrument(s, self.name) for s in self.list_symbols()]

    def get_instrument_metadata(self, symbol: str) -> InstrumentMetadata | None:
        """Metadata for one symbol, normalized to canonical naming."""
        return _default_instrument(symbol, self.name)

    def get_market_status(self, symbol: str) -> MarketStatus:
        return MarketStatus(symbol=symbol, market_status="unknown", provider_symbol=symbol)

    def health_check(self) -> ProviderHealth:
        try:
            self.get_latest_quote(self.list_symbols()[0])
            return ProviderHealth(provider=self.name, status="ok")
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(provider=self.name, status="unavailable", detail=str(exc))

    def stream_quotes(self, symbols: list[str], poll_interval: float = 1.5) -> Iterator[dict]:
        """Poll-based quote generator; yields normalized quote dicts."""
        known = set(self.list_symbols())
        pending = [s for s in symbols if s.upper() not in known]
        if pending:
            raise ValueError(f"symbols not served by {self.name}: {', '.join(pending)}")
        while True:
            for s in symbols:
                try:
                    yield self.get_latest_quote(s)
                except Exception:  # noqa: BLE001
                    continue
            time.sleep(poll_interval)

    def get_economic_calendar(
        self, currency: str | None = None, impact: str | None = None
    ) -> list[dict]:
        return []


def _default_instrument(symbol: str, provider: str) -> InstrumentMetadata:
    canonical = symbol.upper().replace("/", "").replace("_", "")
    quote = canonical[3:]
    return InstrumentMetadata(
        canonical_symbol=canonical,
        display_symbol=f"{canonical[:3]}/{canonical[3:]}",
        provider_symbol=symbol.upper(),
        base_currency=canonical[:3],
        quote_currency=quote,
        pip_size=0.01 if quote == "JPY" else 0.0001,
        data_provider=provider,
    )
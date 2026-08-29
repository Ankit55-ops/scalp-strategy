"""Market data provider abstractions and the mock default implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class MarketDataProvider(ABC):
    name = "base"

    @abstractmethod
    def list_symbols(self) -> list[str]:
        ...

    @abstractmethod
    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        ...

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        ...

    @abstractmethod
    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        ...

    def get_economic_calendar(
        self, currency: str | None = None, impact: str | None = None
    ) -> list[dict]:
        return []

    def stream_quotes(self, symbol: str):
        # Generator-based streaming. Base returns nothing.
        return iter(())

"""Provider factory resolving configured market-data and broker providers."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.providers.broker import BrokerProvider, SimulatedBroker
from app.providers.base import MarketDataProvider
from app.providers.csv_provider import CSVMarketDataProvider
from app.providers.mock import MockMarketDataProvider


@lru_cache
def get_market_data_provider(provider: str | None = None) -> MarketDataProvider:
    name = (provider or get_settings().MARKET_DATA_PROVIDER).lower()
    if name == "csv":
        return CSVMarketDataProvider()
    return MockMarketDataProvider()


@lru_cache
def get_broker_provider(provider: str | None = None) -> BrokerProvider:
    return SimulatedBroker()

"""Provider factory resolving configured market-data and broker providers."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.providers.base import MarketDataProvider
from app.providers.broker import BrokerProvider, SimulatedBroker
from app.providers.csv_provider import CSVMarketDataProvider
from app.providers.mock import MockMarketDataProvider

# Imported lazily so the modules' heavy deps (requests etc.) do not load unless
# the provider is actually selected.
_REAL_PROVIDERS = {
    "oanda": ("oanda", "OandaMarketDataProvider"),
    "twelvedata": ("twelvedata", "TwelveDataMarketDataProvider"),
}


@lru_cache
def get_market_data_provider(provider: str | None = None) -> MarketDataProvider:
    name = (provider or get_settings().MARKET_DATA_PROVIDER).lower()
    name = _alias(name)
    if name == "csv":
        return CSVMarketDataProvider()
    if name == "mock":
        return MockMarketDataProvider()
    if name in _REAL_PROVIDERS:
        return _load_real(name)
    raise ValueError(
        f"market data provider '{name}' is not implemented; "
        "available: mock, csv, oanda, twelvedata"
    )


def _alias(name: str) -> str:
    return {"simulated": "csv", "file": "csv"}.get(name, name)


def _load_real(name: str) -> MarketDataProvider:
    from importlib import import_module

    module_name, cls_name = _REAL_PROVIDERS[name]
    settings = get_settings()
    if name == "oanda" and not (settings.OANDA_API_KEY and settings.OANDA_ACCOUNT_ID):
        raise RuntimeError(
            "OANDA selected but OANDA_API_KEY / OANDA_ACCOUNT_ID are not configured"
        )
    if name == "twelvedata" and not settings.TWELVEDATA_API_KEY:
        raise RuntimeError("Twelve Data selected but TWELVEDATA_API_KEY is not configured")
    module = import_module(f"app.providers.{module_name}")
    provider_cls = getattr(module, cls_name)
    if name == "oanda":
        return provider_cls(api_key=settings.OANDA_API_KEY, account_id=settings.OANDA_ACCOUNT_ID, env=settings.OANDA_ENV)
    return provider_cls(api_key=settings.TWELVEDATA_API_KEY)


@lru_cache
def get_broker_provider(provider: str | None = None) -> BrokerProvider:
    name = (provider or get_settings().BROKER_PROVIDER).lower()
    if name == "simulated":
        return SimulatedBroker()
    if name == "oanda_practice":
        from app.providers.oanda_broker import OandaPracticeBroker

        settings = get_settings()
        return OandaPracticeBroker(
            api_key=settings.OANDA_API_KEY,
            account_id=settings.OANDA_ACCOUNT_ID,
            env=settings.OANDA_ENV,
        )
    raise ValueError(
        f"broker provider '{name}' is not implemented; available: simulated, oanda_practice"
    )


def clear_provider_caches() -> None:
    """Clear cached provider instances (used by tests)."""
    get_market_data_provider.cache_clear()
    get_broker_provider.cache_clear()
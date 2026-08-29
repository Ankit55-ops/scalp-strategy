"""Mock market data provider for offline development and tests.

Intentionally deterministic for candies and full-featured for quotes so the
downstream normalized models (bid/ask/mid/spread, staleness, provenance) are
exercised without network access. Never used as the production default.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from app.providers.base import MarketDataProvider
from app.providers.mock_data import gen_candles
from app.providers.models import (
    InstrumentMetadata,
    MarketStatus,
    ProviderHealth,
    build_candle,
    build_quote,
)
from app.services.market_math import pip_size, spread_in_pips

SUPPORTED_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "NZDUSD",
    "USDCAD",
]

# Default live-state used by latest quote / spread streaming.
_LIVE: dict[str, dict] = {}


def _avg_price(symbol: str) -> float:
    if symbol.upper().endswith("JPY"):
        return 150.0 + random.uniform(-1, 1)
    if symbol.upper() == "EURUSD":
        return 1.10 + random.uniform(-0.002, 0.002)
    return 1.20 + random.uniform(-0.002, 0.002)


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"
    bid_ask_basis = "mid"

    def __init__(self) -> None:
        self._spread_pips = {
            "EURUSD": 0.8,
            "GBPUSD": 1.0,
            "USDJPY": 0.9,
            "AUDUSD": 1.0,
            "NZDUSD": 1.2,
            "USDCAD": 1.1,
        }
        self._metadata = {
            s: self._instrument(s) for s in SUPPORTED_SYMBOLS
        }

    @staticmethod
    def _instrument(symbol: str) -> InstrumentMetadata:
        canonical = symbol.upper()
        quote = canonical[3:] if len(canonical) > 3 else "USD"
        return InstrumentMetadata(
            canonical_symbol=canonical,
            display_symbol=f"{canonical[:3]}/{canonical[3:]}",
            provider_symbol=canonical,
            base_currency=canonical[:3],
            quote_currency=quote,
            pip_size=pip_size(quote),
            price_precision=3 if quote == "JPY" else 5,
            data_provider="mock",
            data_delay_status="realtime",
            bid_ask_basis="mid",
        )

    def list_instruments(self) -> list[InstrumentMetadata]:
        return list(self._metadata.values())

    def get_instrument_metadata(self, symbol: str) -> InstrumentMetadata | None:
        return self._metadata.get(symbol.upper().replace("/", ""))

    def _metadata_for(self, symbol: str) -> InstrumentMetadata:
        meta = self._metadata.get(symbol.upper().replace("/", ""))
        if meta is not None:
            return meta
        return self._instrument(symbol.upper().replace("/", ""))

    def list_symbols(self) -> list[str]:
        return list(SUPPORTED_SYMBOLS)

    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        symbol = symbol.upper().replace("/", "")
        tf_sec = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}[timeframe]
        span = (end - start).total_seconds()
        n = max(1, int(span // tf_sec))
        seed = hash(f"{symbol}:{timeframe}:{start.isoformat()}") % (2**31)
        candles = gen_candles(symbol, timeframe, start, n, seed=seed)
        out = []
        for i, c in enumerate(candles):
            out.append(build_candle(symbol, timeframe, c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"], source=self.name, is_complete=True, bid_ask_basis=self.bid_ask_basis))
        return out

    def get_latest_quote(self, symbol: str) -> dict:
        symbol = symbol.upper().replace("/", "")
        mid = _avg_price(symbol)
        spread = self.get_spread(symbol)
        meta = self._metadata_for(symbol)
        half = spread * meta.pip_size / 2
        ts = datetime.now(timezone.utc).timestamp()
        bid = round(mid - half, 8)
        ask = round(mid + half, 8)
        return build_quote(
            symbol,
            bid,
            ask,
            ts=ts - 0.02,
            provider_symbol=symbol,
            source=self.name,
            market_status="open",
            instrument=meta,
        )

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        symbol = symbol.upper().replace("/", "")
        return self._spread_pips.get(symbol, 1.0)

    def get_market_status(self, symbol: str) -> MarketStatus:
        return MarketStatus(
            symbol=symbol.upper(),
            market_status="open",
            reason="mock feed is always open",
            provider_symbol=symbol.upper(),
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, status="ok", latency_ms=0.0, detail="simulated feed")

    def get_economic_calendar(
        self, currency: str | None = None, impact: str | None = None
    ) -> list[dict]:
        return []
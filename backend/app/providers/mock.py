"""Mock market data provider for offline development and tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.providers.base import MarketDataProvider
from app.providers.mock_data import gen_candles
from app.services.market_math import pip_size

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
    import random

    if symbol.upper().endswith("JPY"):
        return 150.0 + random.uniform(-1, 1)
    if symbol.upper() == "EURUSD":
        return 1.10 + random.uniform(-0.002, 0.002)
    return 1.20 + random.uniform(-0.002, 0.002)


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"

    def __init__(self) -> None:
        self._spread_pips = {
            "EURUSD": 0.8,
            "GBPUSD": 1.0,
            "USDJPY": 0.9,
            "AUDUSD": 1.0,
            "NZDUSD": 1.2,
            "USDCAD": 1.1,
        }

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
        return candles

    def get_latest_quote(self, symbol: str) -> dict:
        symbol = symbol.upper().replace("/", "")
        mid = _avg_price(symbol)
        spread = self.get_spread(symbol)
        half = spread * pip_size("JPY" if symbol.endswith("JPY") else "USD") / 2
        return {
            "symbol": symbol,
            "bid": round(mid - half, 5),
            "ask": round(mid + half, 5),
            "ts": datetime.now(timezone.utc).timestamp(),
        }

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        symbol = symbol.upper().replace("/", "")
        return self._spread_pips.get(symbol, 1.0)

    def get_economic_calendar(
        self, currency: str | None = None, impact: str | None = None
    ) -> list[dict]:
        return []

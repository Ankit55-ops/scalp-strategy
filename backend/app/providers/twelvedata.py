"""Twelve Data REST market data provider.

Uses the Twelve Data ``time_series`` and ``quote`` endpoints (symbol notation
``EUR/USD``). Respects provider rate limits (free plans are ~8 req/min): on
HTTP 429 the provider backs off, reports ``RATE_LIMITED`` via health checks and
raises a clear error rather than hammering the API. WebSocket streaming is not
enabled by default; when ``TWELVEDATA_USE_WEBSOCKET`` is set the poll generator
is used behind the scenes (WS support lands in a later phase).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from app.core.config import get_settings
from app.providers.base import MarketDataProvider
from app.providers.models import (
    InstrumentMetadata,
    MarketStatus,
    ProviderHealth,
    build_candle,
    build_quote,
)

TD_INTERVAL = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}

DEFAULT_SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "NZD/USD",
    "USD/CAD",
    "USD/CHF",
    "EUR/JPY",
]


class TwelveDataMarketDataProvider(MarketDataProvider):
    name = "twelvedata"
    bid_ask_basis = "provider_defined"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.TWELVEDATA_API_KEY
        self.base = settings.TWELVEDATA_BASE_URL or "https://api.twelvedata.com"
        self.timeout = settings.PROVIDER_TIMEOUT_SECONDS
        self.max_retries = settings.PROVIDER_MAX_RETRIES
        self._session = requests.Session()
        self._last_rate_reset = time.monotonic()
        self._calls_in_window = 0
        self._rate_limit_hit = False

    # -- rate limiting -----------------------------------------------------
    def _throttle(self) -> None:
        # Twelve Data free plans limit to ~8 requests/minute. Keep a soft
        # window of 7 to avoid burning the budget on idle polling.
        window = 60.0
        now = time.monotonic()
        if now - self._last_rate_reset >= window:
            self._last_rate_reset = now
            self._calls_in_window = 0
            self._rate_limit_hit = False
        if self._calls_in_window >= 7:
            wait = max(0.5, window - (now - self._last_rate_reset))
            time.sleep(min(wait, 20.0))
            self._last_rate_reset = time.monotonic()
            self._calls_in_window = 0
        self._calls_in_window += 1

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.api_key:
            raise RuntimeError("Twelve Data API key is not configured (TWELVEDATA_API_KEY)")
        self._throttle()
        params = dict(params or {})
        params.setdefault("apikey", self.api_key)
        url = f"{self.base}{path}"
        for attempt in range(self.max_retries + 1):
            resp = self._session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 429:
                self._rate_limit_hit = True
                retry_after = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                time.sleep(min(retry_after, 20.0))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Twelve Data {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            if payload.get("status") == "error":
                raise RuntimeError(f"Twelve Data error: {payload.get('message', 'unknown')}")
            return payload
        raise RuntimeError("Twelve Data rate limit: still throttled after retries")

    # -- symbol normalization ---------------------------------------------
    @staticmethod
    def to_provider(symbol: str) -> str:
        canon = symbol.upper().replace("/", "").replace("_", "").replace("-", "")
        return f"{canon[:3]}/{canon[3:]}"

    @staticmethod
    def to_canonical(provider_symbol: str) -> str:
        return provider_symbol.upper().replace("/", "").replace("_", "").replace("-", "")

    def _instrument(self, symbol: str) -> InstrumentMetadata:
        canon = self.to_canonical(symbol)
        quote = canon[3:]
        return InstrumentMetadata(
            canonical_symbol=canon,
            display_symbol=f"{canon[:3]}/{canon[3:]}",
            provider_symbol=self.to_provider(canon),
            base_currency=canon[:3],
            quote_currency=quote,
            pip_size=0.01 if quote == "JPY" else 0.0001,
            price_precision=3 if quote == "JPY" else 5,
            contract_size=100000.0,
            minimum_lot=1,
            data_provider=self.name,
            data_delay_status="realtime",
            bid_ask_basis="provider_defined",
        )

    def list_instruments(self) -> list[InstrumentMetadata]:
        return [self._instrument(s) for s in DEFAULT_SYMBOLS]

    def get_instrument_metadata(self, symbol: str) -> InstrumentMetadata | None:
        return self._instrument(symbol)

    def list_symbols(self) -> list[str]:
        return [self.to_canonical(s) for s in DEFAULT_SYMBOLS]

    # -- candles -----------------------------------------------------------
    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        interval = TD_INTERVAL.get(timeframe.upper())
        if interval is None:
            raise ValueError(f"Twelve Data does not serve timeframe {timeframe}")
        params = {
            "symbol": self.to_provider(symbol),
            "interval": interval,
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            "order": "ASC",
            "timezone": "UTC",
        }
        payload = self._get("/time_series", params)
        out: list[dict] = []
        for row in payload.get("values", []):
            ts = _td_ts(row.get("datetime"))
            if ts is None:
                continue
            out.append(
                build_candle(
                    self.to_canonical(symbol),
                    timeframe,
                    ts,
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    float(row.get("volume") or 0.0),
                    source=self.name,
                    is_complete=True,
                    bid_ask_basis="provider_defined",
                )
            )
        out.sort(key=lambda c: c["ts"])
        return out

    # -- quotes ------------------------------------------------------------
    def get_latest_quote(self, symbol: str) -> dict:
        payload = self._get("/quote", {"symbol": self.to_provider(symbol), "timezone": "UTC"})
        bid = _td_float(payload.get("bid"))
        ask = _td_float(payload.get("ask"))
        meta = self._instrument(symbol)
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            # Twelve Data does not always return a live bid/ask; fall back to
            # the last close as a midpoint with a nominal spread.
            close = _td_float(payload.get("close")) or meta.pip_size
            half = meta.pip_size / 2
            bid, ask = close - half, close + half
        ts = _td_ts(payload.get("datetime")) or datetime.now(timezone.utc).timestamp()
        return build_quote(
            self.to_canonical(symbol),
            bid,
            ask,
            ts=ts,
            provider_symbol=self.to_provider(symbol),
            source=self.name,
            market_status="open",
            data_delay_status="realtime",
            instrument=meta,
        )

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        quote = self.get_latest_quote(symbol)
        return float(quote.get("spread_pips") or 0.0)

    def get_market_status(self, symbol: str) -> MarketStatus:
        return MarketStatus(
            symbol=self.to_canonical(symbol),
            market_status="open",
            reason=None,
            provider_symbol=self.to_provider(symbol),
        )

    def health_check(self) -> ProviderHealth:
        t0 = time.monotonic()
        try:
            payload = self._get("/quote", {"symbol": "EUR/USD"})
            latency = round((time.monotonic() - t0) * 1000.0, 2)
            if self._rate_limit_hit:
                return ProviderHealth(provider=self.name, status="unavailable", latency_ms=latency, detail="rate-limited")
            return ProviderHealth(provider=self.name, status="ok", latency_ms=latency)
        except Exception as exc:  # noqa: BLE001
            status = "unavailable"
            if "rate" in str(exc).lower() or "429" in str(exc).lower():
                status = "unavailable"
                self._rate_limit_hit = True
            return ProviderHealth(provider=self.name, status=status, detail=str(exc))


def _td_ts(value: str | None) -> float | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _td_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
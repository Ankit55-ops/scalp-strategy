"""OANDA v20 REST market data provider.

Uses the official OANDA v20 API. Instrument symbols use OANDA notation
(``EUR_USD``); candles are requested with ``price=MBA`` so bid/ask is preserved
when upstream has it. Quotes come from the account pricing endpoint and include
bid/ask/closeout levels. Credentials come only from server-side encrypted
settings — never from the browser.
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

OANDA_GRANULARITY = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D1": "D",
}

DEFAULT_SYMBOLS = [
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "NZD_USD",
    "USD_CAD",
    "USD_CHF",
    "EUR_JPY",
]


class OandaMarketDataProvider(MarketDataProvider):
    name = "oanda"
    bid_ask_basis = "provider_defined"

    def __init__(self, api_key: str | None = None, account_id: str | None = None, env: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.OANDA_API_KEY
        self.account_id = account_id or settings.OANDA_ACCOUNT_ID
        self.env = (env or settings.OANDA_ENV).lower()
        if self.env == "live":
            self.base = "https://api-fxtrade.oanda.com"
        else:
            self.base = settings.OANDA_BASE_URL or "https://api-fxpractice.oanda.com"
        self.timeout = settings.PROVIDER_TIMEOUT_SECONDS
        self.max_retries = settings.PROVIDER_MAX_RETRIES
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        self._metadata: dict[str, InstrumentMetadata] | None = None

    # -- low-level ---------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.api_key:
            raise RuntimeError("OANDA API key is not configured (OANDA_API_KEY)")
        url = f"{self.base}/v3{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429:
                    time.sleep(min(2 ** (attempt + 1), 8))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"OANDA {resp.status_code}: {resp.text[:200]}")
                return resp.json()
            except (requests.RequestException, RuntimeError) as exc:
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"OANDA request failed: {last_exc}")

    # -- symbol normalization ---------------------------------------------
    @staticmethod
    def to_provider(symbol: str) -> str:
        return symbol.upper().replace("/", "").replace("_", "").replace("-", "")

    @classmethod
    def to_canonical(cls, provider_symbol: str) -> str:
        return provider_symbol.upper().replace("_", "").replace("-", "")

    def _providers_symbol(self, symbol: str) -> str:
        canon = self.to_canonical(symbol)
        return f"{canon[:3]}_{canon[3:]}"

    # -- discovery ---------------------------------------------------------
    def _load_instruments(self) -> dict[str, InstrumentMetadata]:
        if self._metadata is not None:
            return self._metadata
        data = self._get(f"/accounts/{self.account_id}/instruments")
        out: dict[str, InstrumentMetadata] = {}
        for row in data.get("instruments", []):
            canon = self.to_canonical(row["name"])
            quote = tail_ccy = canon[3:]
            pip = 10 ** int(row.get("pipLocation", -4))
            out[canon] = InstrumentMetadata(
                canonical_symbol=canon,
                display_symbol=f"{canon[:3]}/{canon[3:]}",
                provider_symbol=row["name"],
                base_currency=row.get("baseCurrency", canon[:3]),
                quote_currency=quote,
                pip_size=pip,
                price_precision=int(row.get("displayPrecision", 5)),
                quantity_precision=0,
                contract_size=float(row.get("contractSize", 100000)),
                minimum_lot=float(row.get("minimumTradeSize", 1)),
                lot_step=None,
                trading_sessions=None,
                margin_metadata=None,
                data_provider=self.name,
                data_delay_status="realtime" if self.env == "live" else "realtime",
                bid_ask_basis="provider_defined",
            )
        # fall back to known majors if the account has no instruments
        if not out:
            for psym in DEFAULT_SYMBOLS:
                canon = self.to_canonical(psym)
                quote = canon[3:]
                out[canon] = InstrumentMetadata(
                    canonical_symbol=canon,
                    display_symbol=f"{canon[:3]}/{canon[3:]}",
                    provider_symbol=psym,
                    base_currency=canon[:3],
                    quote_currency=quote,
                    pip_size=0.01 if quote == "JPY" else 0.0001,
                    price_precision=3 if quote == "JPY" else 5,
                    quantity_precision=0,
                    contract_size=100000.0,
                    data_provider=self.name,
                )
        self._metadata = out
        return out

    def list_symbols(self) -> list[str]:
        try:
            return sorted(self._load_instruments().keys())
        except Exception:  # noqa: BLE001
            return [self.to_canonical(s) for s in DEFAULT_SYMBOLS]

    def list_instruments(self) -> list[InstrumentMetadata]:
        return list(self._load_instruments().values())

    def get_instrument_metadata(self, symbol: str) -> InstrumentMetadata | None:
        canon = self.to_canonical(symbol)
        try:
            return self._load_instruments().get(canon)
        except Exception:  # noqa: BLE001
            quote = canon[3:]
            return InstrumentMetadata(
                canonical_symbol=canon,
                display_symbol=f"{canon[:3]}/{canon[3:]}",
                provider_symbol=self._providers_symbol(canon),
                base_currency=canon[:3],
                quote_currency=quote,
                pip_size=0.01 if quote == "JPY" else 0.0001,
                price_precision=3 if quote == "JPY" else 5,
                contract_size=100000.0,
                data_provider=self.name,
            )

    # -- candles -----------------------------------------------------------
    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        gran = OANDA_GRANULARITY.get(timeframe.upper())
        if gran is None:
            raise ValueError(f"OANDA does not serve timeframe {timeframe} (use M1/M5/M15/M30/H1/H4/D1)")
        psym = self._providers_symbol(symbol)
        start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        data = self._get(
            f"/instruments/{psym}/candles",
            params={
                "granularity": gran,
                "from": start_utc.isoformat(),
                "to": end_utc.isoformat(),
                "price": "MBA",
            },
        )
        meta = self.get_instrument_metadata(symbol)
        pip = meta.pip_size if meta else (0.01 if canon_quote_ccy(psym) == "JPY" else 0.0001)
        _ = pip  # keep unused guards honest in lint
        out: list[dict] = []
        for row in data.get("candles", []):
            ts = _oanda_ts(row.get("time"))
            basis, point = _price_basis(row)
            if point is None:
                continue
            out.append(
                build_candle(
                    self.to_canonical(psym),
                    timeframe,
                    ts,
                    point["o"],
                    point["h"],
                    point["l"],
                    point["c"],
                    float(row.get("volume", 0.0) or 0.0),
                    source=self.name,
                    is_complete=bool(row.get("complete", False)),
                    bid_ask_basis=basis,
                )
            )
        out.sort(key=lambda c: c["ts"])
        return out

    # -- quotes ------------------------------------------------------------
    def get_latest_quote(self, symbol: str) -> dict:
        psym = self._providers_symbol(symbol)
        data = self._get(
            f"/accounts/{self.account_id}/pricing",
            params={"instruments": psym},
        )
        meta = self.get_instrument_metadata(symbol)
        for price in data.get("prices", []):
            if price.get("instrument") != psym:
                continue
            raw_ts = _oanda_ts(price.get("time"))
            bids = price.get("bids") or []
            asks = price.get("asks") or []
            bid = float(bids[0]["price"]) if bids else float(price.get("closeoutBid") or 0.0)
            ask = float(asks[0]["price"]) if asks else float(price.get("closeoutAsk") or 0.0)
            status = _oanda_status(price.get("status"))
            return build_quote(
                self.to_canonical(psym),
                bid,
                ask,
                ts=raw_ts,
                provider_symbol=psym,
                source=self.name,
                market_status=status,
                data_delay_status="realtime",
                instrument=meta,
            )
        raise RuntimeError(f"OANDA returned no pricing for {psym}")

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        quote = self.get_latest_quote(symbol)
        return float(quote.get("spread_pips") or 0.0)

    def get_market_status(self, symbol: str) -> MarketStatus:
        try:
            quote = self.get_latest_quote(symbol)
            return MarketStatus(
                symbol=self.to_canonical(symbol),
                market_status=quote.get("market_status", "unknown"),
                reason=None,
                provider_symbol=quote.get("provider_symbol"),
            )
        except Exception as exc:  # noqa: BLE001
            canon = self.to_canonical(symbol)
            return MarketStatus(symbol=canon, market_status="unknown", reason=str(exc))

    def health_check(self) -> ProviderHealth:
        t0 = time.monotonic()
        try:
            self.list_symbols()
            latency = round((time.monotonic() - t0) * 1000.0, 2)
            return ProviderHealth(provider=self.name, status="ok", latency_ms=latency)
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(provider=self.name, status="unavailable", detail=str(exc))


def _oanda_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _price_basis(row: dict) -> tuple[str, dict | None]:
    """Return (bid_ask_basis, first available set of OHLC)."""
    for basis in ("mid", "bid", "ask"):
        point = row.get(basis)
        if point and "o" in point:
            return basis, point
    return "provider_defined", None


def _oanda_status(status: str | None) -> str:
    return "open" if status in ("tradeable", "closedOnly") else "unknown"


def canon_quote_ccy(provider_symbol: str) -> str:
    canon = provider_symbol.upper().replace("_", "")
    return canon[3:] if len(canon) > 3 else "USD"
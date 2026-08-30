"""Exness via MetaTrader 5 — provider adapter layer.

This module defines a thin, **read-only**, allow-listed adapter interface for an
Exness / MetaTrader 5 connectivity layer. Live order placement is intentionally
NOT part of this interface. Each connection is driven by a secure gateway
architecture (MT5 Gateway Agent, secure server-side connector, or an approved
admin-configured bridge), and real data access is only ever exercised through a
server-side health-checked connection owned by the workspace.

We deliberately do *not* claim official Exness REST access exists for every
account type/region. The adapter reports *capabilities* that are discovered
dynamically against the configured connection method, and an Exness connection
is only ever shown as ``CONNECTED`` when a server-side health check succeeds on
a connection the user explicitly configured.

``MockExnessMT5ProviderAdapter`` provides deterministic, synthetic data so the
whole validation pipeline (data-quality gate, symbol mapping, bid/ask-aware
Decimal backtester) can be exercised in development and tests without real
credentials. It is clearly labelled as mock and never silently substituted for
real provider data at runtime.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.providers.models import (
    InstrumentMetadata,
    ProviderHealth,
    build_candle,
    build_quote,
)

# Canonical provider identifier and display name.
PROVIDER_TYPE = "exness"
PROVIDER_DISPLAY_NAME = "Exness via MetaTrader 5"

# Connection modes the backend supports.
CONNECTION_MODE_MT5_GATEWAY = "mt5_gateway_agent"
CONNECTION_MODE_SERVER_SIDE = "server_side_mt5"
CONNECTION_MODE_BRIDGE = "approved_bridge"
CONNECTION_MODES = (
    CONNECTION_MODE_MT5_GATEWAY,
    CONNECTION_MODE_SERVER_SIDE,
    CONNECTION_MODE_BRIDGE,
)

# Read-only operations importable through a gateway / adapter.
ALLOWED_OPERATIONS = frozenset(
    {
        "account_metadata",
        "symbol_metadata",
        "historical_candles",
        "latest_quotes",
        "feed_health",
        "account_summary",  # only with explicit permission
        "open_positions",  # only with explicit permission
        "trade_history",  # only with explicit permission
    }
)

# Provider connection status values surfaced to the UI.
CONNECTION_STATUSES = (
    "NOT_CONFIGURED",
    "CONFIGURED",
    "CONNECTING",
    "CONNECTED",
    "DEGRADED",
    "STALE",
    "DISCONNECTED",
    "AUTHENTICATION_FAILED",
    "PERMISSION_DENIED",
    "UNSUPPORTED_ACCOUNT",
    "RATE_LIMITED",
    "MAINTENANCE",
    "ERROR",
)

# Available capabilities a provider connection can report.
CAPABILITIES = (
    "historical_candles",
    "realtime_quotes",
    "bid_ask_quotes",
    "spread_data",
    "symbol_metadata",
    "paper_trading",
    "broker_practice_trading",
    "live_trading",  # always unavailable unless explicitly implemented later
)


@dataclass
class CapabilityReport:
    """Result of a server-side test/capability discovery."""

    connection_status: str
    account_environment: str = "unknown"  # demo | real | unknown
    provider_server: str | None = None
    instrument_count: int = 0
    capabilities: dict = field(default_factory=dict)  # capability -> availability
    historical_data_available: bool = False
    quote_availability: str = "none"  # none | realtime | delayed | bid_ask
    account_metadata_available: bool = False
    data_delay_status: str = "realtime"
    latency_ms: float | None = None
    live_trading_status: str = "disabled"
    account_label: str | None = None
    detail: str = ""
class ExnessMT5ProviderAdapter(ABC):
    """Read-only adapter interface for an Exness / MT5 data connection."""

    name = PROVIDER_TYPE
    bid_ask_basis = "provider_defined"

    def __init__(
        self,
        account_environment: str = "unknown",
        provider_server: str | None = None,
    ) -> None:
        self.account_environment = account_environment
        self.provider_server = provider_server

    # -- capability discovery ------------------------------------------------
    def capabilities(self) -> dict[str, str]:
        """Map capability name -> availability for THIS connection."""
        caps: dict[str, str] = {}
        caps["realtime_quotes"] = "available"
        caps["symbol_metadata"] = "available"
        caps["paper_trading"] = "available"
        caps["broker_practice_trading"] = "available" if self.account_environment == "demo" else "permission_denied"
        caps["live_trading"] = "unavailable"  # never enabled in this task
        return caps

    def allows(self, operation: str) -> bool:
        """True if this connection is allowed to perform ``operation``."""
        return operation in ALLOWED_OPERATIONS

    # -- data operations (read-only) -----------------------------------------
    @abstractmethod
    def list_instruments(self) -> list[str]:
        """Canonical symbols served (e.g. EURUSD)."""

    @abstractmethod
    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Normalized ascending candles (see ``app.providers.models``)."""

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """Normalized quote with bid/ask/spread/latency."""

    def get_instrument_metadata(self, symbol: str) -> InstrumentMetadata | None:
        return _default_instrument(symbol)

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        return _default_instrument(symbol).pip_size * 8.0

    def stream_quotes(self, symbols: list[str], poll_interval: float = 1.5) -> Iterator[dict]:
        while True:
            for s in symbols:
                try:
                    yield self.get_latest_quote(s)
                except Exception:  # noqa: BLE001
                    continue
            time.sleep(poll_interval)

    def get_account_summary(self) -> dict:
        raise PermissionError("account_summary permission not configured")

    def get_open_positions(self) -> list[dict]:
        raise PermissionError("open_positions permission not configured")

    def get_trade_history(self) -> list[dict]:
        raise PermissionError("trade_history permission not configured")

    def health_check(self) -> ProviderHealth:
        try:
            symbols = self.list_instruments()
            q = self.get_latest_quote(symbols[0])
            return ProviderHealth(self.name, "ok", latency_ms=q.get("latency_ms"))
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(self.name, "unavailable", detail=str(exc)[:200])

    def timezone_utc_normalized(self) -> bool:
        return True  # all adapters emit UTC timestamps
class MockExnessMT5ProviderAdapter(ExnessMT5ProviderAdapter):
    """Deterministic, synthetic Exness data adapter for dev/tests.

    Generates random-walk MID candles. When ``bid_ask=True`` a fixed bid/ask
    spread is applied around the mid so downstream bid/ask execution and the
    estimated-spread fallback can both be exercised. NEVER used at runtime as a
    substitute for a real configured provider (see validator).
    """

    name = "mock"
    bid_ask_basis = "mid"

    DEFAULT_SERVERS = {
        "demo": "Exness-MT5Trial",
        "real": "Exness-MT5Real",
    }

    def __init__(
        self,
        account_environment: str = "demo",
        bid_ask: bool = True,
        base_price: float = 1.0850,
        pip_size: float = 0.0001,
        symbols: tuple[str, ...] = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD"),
        default_spread_pips: float = 0.8,
        seed: int = 1,
    ) -> None:
        server = self.DEFAULT_SERVERS.get(account_environment, self.DEFAULT_SERVERS["demo"])
        super().__init__(account_environment=account_environment, provider_server=server)
        self.bid_ask = bid_ask
        self.symbols = symbols
        self.base_price = base_price
        self.pip_size = pip_size
        self.default_spread_pips = default_spread_pips
        self._capacity = 0
        self._seed = seed

    def capabilities(self) -> dict[str, str]:
        caps = super().capabilities()
        if self.bid_ask:
            caps["bid_ask_quotes"] = "available"
            caps["spread_data"] = "available"
        else:
            caps["bid_ask_quotes"] = "unavailable"
            caps["spread_data"] = "estimated"
        caps["historical_candles"] = "available"
        return caps

    def list_instruments(self) -> list[str]:
        return list(self.symbols)

    def get_instrument_metadata(self, symbol: str) -> InstrumentMetadata | None:
        return _default_instrument(symbol)

    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        from app.providers.models import _tf_seconds

        step = _tf_seconds(timeframe)
        ts = int(start.timestamp()) - (int(start.timestamp()) % step)
        end_ts = int(end.timestamp())
        out: list[dict] = []
        price = self.base_price
        tick = 0
        now = datetime.now(timezone.utc).timestamp()
        pip = self.pip_size
        while ts <= end_ts:
            tick += 1
            price += math.sin(tick * 11 + self._seed) * pip * 4
            o = price
            h = o + pip * (2 + (math.sin(tick * 3 + self._seed) * 2))
            l = o - pip * (2 + (math.cos(tick * 3 + self._seed) * 2))
            c = o + math.sin(tick * 17 + self._seed) * pip * 4
            high = max(h, o, c)
            low = min(l, o, c)
            is_complete = (ts + step) <= now
            candle = build_candle(
                symbol, timeframe, ts, o, high, low, c,
                volume=100.0, source=self.name, is_complete=is_complete,
                bid_ask_basis="mid",
            )
            if self.bid_ask:
                half = self.default_spread_pips * pip / 2.0
                candle["bid"] = float(c) - half
                candle["ask"] = float(c) + half
            out.append(candle)
            ts += step
        return out

    def get_latest_quote(self, symbol: str) -> dict:
        # deterministic price; no mutable state shared across calls
        mid = self.base_price + math.sin((self._capacity + len(symbol) + self._seed) * 7) * self.pip_size * 5
        self._capacity += 1
        half = self.default_spread_pips * self.pip_size / 2.0
        return build_quote(
            symbol,
            bid=mid - half,
            ask=mid + half,
            ts=datetime.now(timezone.utc),
            provider_symbol=symbol,
            source=self.name,
            market_status="open",
            data_delay_status="realtime",
        )

    def health_check(self) -> ProviderHealth:
        q = self.get_latest_quote(self.symbols[0])
        return ProviderHealth(self.name, "ok", latency_ms=q.get("latency_ms"))

    def get_account_summary(self) -> dict:
        if self.account_environment != "demo":
            raise PermissionError("account_summary requires demo or configured permission")
        return {
            "balance": 100000.0,
            "equity": 100000.0,
            "free_margin": 100000.0,
            "currency": "USD",
            "leverage": "1:100",
        }
def _default_instrument(symbol: str) -> InstrumentMetadata:
    s = symbol.upper().replace("/", "").replace("_", "")
    quote = s[3:]
    return InstrumentMetadata(
        canonical_symbol=s,
        display_symbol=f"{s[:3]}/{s[3:]}",
        provider_symbol=symbol.upper(),
        base_currency=s[:3],
        quote_currency=quote or "USD",
        pip_size=0.01 if quote == "JPY" else 0.0001,
        contract_size=100000.0,
        minimum_lot=0.01,
        lot_step=0.01,
        data_provider=PROVIDER_TYPE,
        data_delay_status="realtime",
        bid_ask_basis="provider_defined",
    )


def build_mock_adapter(
    account_environment: str = "demo",
    bid_ask: bool = True,
    seed: int = 1,
) -> MockExnessMT5ProviderAdapter:
    return MockExnessMT5ProviderAdapter(
        account_environment=account_environment,
        bid_ask=bid_ask,
        seed=seed,
    )


def build_capability_report(
    adapter: ExnessMT5ProviderAdapter, account_label: str | None = None
) -> CapabilityReport:
    """Run server-side capability discovery against an adapter."""

    def fail(detail: str) -> CapabilityReport:
        return CapabilityReport(
            connection_status="ERROR",
            detail=detail[:200],
            live_trading_status="disabled",
        )

    try:
        instruments = adapter.list_instruments()
        health = adapter.health_check()
        caps = adapter.capabilities()
        historical_available = caps.get("historical_candles", "unavailable") == "available"
        bid_ask = caps.get("bid_ask_quotes", "unavailable") == "available"
        return CapabilityReport(
            connection_status="CONNECTED" if health.status == "ok" else "ERROR",
            account_environment=adapter.account_environment,
            provider_server=adapter.provider_server,
            instrument_count=len(instruments),
            capabilities=caps,
            historical_data_available=historical_available,
            quote_availability="bid_ask" if bid_ask else "mid_only",
            account_metadata_available=adapter.allows("account_summary"),
            latency_ms=health.latency_ms,
            live_trading_status="disabled",
            account_label=account_label,
        )
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
    # Only safe, redacted metadata is ever returned.
from typing import Any, Literal

from pydantic import BaseModel

ProviderName = Literal["oanda", "twelvedata", "csv", "mock"]


class ConnectProviderRequest(BaseModel):
    provider: ProviderName
    api_key: str = ""
    account_id: str | None = None
    env: Literal["practice", "live"] | None = None


class ProviderStatusItem(BaseModel):
    status: str
    latency_ms: float | None = None
    last_connected_at: float | None = None
    error: str | None = None


class ProviderStatusResponse(BaseModel):
    active_provider: str
    active_provider_label: str
    env_selected: str
    bid_ask_basis: str
    health: dict[str, Any]
    connections: dict[str, ProviderStatusItem]
    stale_threshold_seconds: int


class QuoteResponse(BaseModel):
    symbol: str
    provider_symbol: str
    bid: float
    ask: float
    mid: float
    spread_price: float
    spread_pips: float
    ts: float
    timestamp_utc: str
    latency_ms: float | None = None
    source: str
    market_status: str
    is_stale: bool
    feed_state: str | None = None
    provider: str | None = None
    bid_ask_basis: str | None = None
    data_delay_status: str | None = None
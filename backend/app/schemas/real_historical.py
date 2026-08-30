"""Request/response schemas for Real Historical Data validation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

VALID_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class ExecutionModel(str, Enum):
    NEXT_CANDLE_OPEN = "NEXT_CANDLE_OPEN"
    SIGNAL_CANDLE_CLOSE_ESTIMATED = "SIGNAL_CANDLE_CLOSE_ESTIMATED"
    INTRABAR_OHLC_CONSERVATIVE = "INTRABAR_OHLC_CONSERVATIVE"
    BID_ASK_HISTORICAL_WHERE_AVAILABLE = "BID_ASK_HISTORICAL_WHERE_AVAILABLE"


class SpreadModel(str, Enum):
    PROVIDER_BID_ASK = "provider_bid_ask"
    FIXED_SPREAD_PIPS = "fixed_spread_pips"
    SESSION_AWARE_SPREAD = "session_aware_spread"
    PROVIDER_ESTIMATED_SPREAD = "provider_estimated_spread"


class SlippageModel(str, Enum):
    NONE = "none"
    FIXED_ADVERSE = "fixed_adverse"
    VOLATILITY_BASED = "volatility_based"
    SPREAD_BASED = "spread_based"


class CommissionModel(str, Enum):
    NONE = "none"
    FIXED_PER_LOT = "fixed_per_lot"
    FIXED_PER_TRADE = "fixed_per_trade"


class RealHistoricalCostParams(BaseModel):
    spread_model: SpreadModel = SpreadModel.FIXED_SPREAD_PIPS
    fixed_spread_pips: float = Field(default=0.8, ge=0)
    commission_model: CommissionModel = CommissionModel.FIXED_PER_LOT
    commission_per_lot: float = Field(default=3.0, ge=0)
    slippage_model: SlippageModel = SlippageModel.FIXED_ADVERSE
    fixed_slippage_pips: float = Field(default=0.3, ge=0)
    swap_enabled: bool = False
    swap_points_per_night: float = Field(default=0.0)
    account_currency: str = "USD"
    starting_balance: float = Field(default=100000.0, gt=0)
    execution_model: ExecutionModel = ExecutionModel.NEXT_CANDLE_OPEN


class RealHistoricalValidationRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=36)
    strategy_version_id: str | None = Field(default=None, max_length=36)
    connection_id: str | None = Field(default=None, max_length=36)
    provider: str = "exness"  # exness | oanda | twelvedata | csv | mock
    provider_symbol: str = Field(min_length=1, max_length=32)
    timeout: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"] = "M5"
    start_time_utc: str  # ISO 8601
    end_time_utc: str  # ISO 8601
    cost: RealHistoricalCostParams = Field(default_factory=RealHistoricalCostParams)
    risk_profile_version: str | None = Field(default=None, max_length=32)
    idempotency_key: str | None = Field(default=None, max_length=128)


class RealHistoricalValidationPreviewRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=36)
    strategy_version_id: str | None = Field(default=None, max_length=36)
    connection_id: str | None = Field(default=None, max_length=36)
    provider: str = "exness"
    provider_symbol: str = Field(min_length=1, max_length=32)
    timeout: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"] = "M5"
    start_time_utc: str
    end_time_utc: str


class ValidationRunOut(BaseModel):
    id: str
    status: str
    provider_name: str
    provider_symbol: str
    canonical_symbol: str
    timeout: str
    start_time_utc: float
    end_time_utc: float
    strategy_version: str | None = None
    execution_model: str = "NEXT_CANDLE_OPEN"
    source_data_type: str = "historical_candles"
    candle_count: int = 0
    data_quality_score: float | None = None
    error_safe: str | None = None
    created_at: str | None = None
    completed_at_utc: float | None = None
    warnings: list[str] = []
    result: dict[str, Any] | None = None
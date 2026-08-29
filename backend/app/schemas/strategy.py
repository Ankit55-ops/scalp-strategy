from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class StrategyFamily(str, Enum):
    trend_pullback = "trend_pullback"
    breakout = "breakout"
    mean_reversion = "mean_reversion"
    momentum = "momentum"
    range_fade = "range_fade"
    liquidity_sweep = "liquidity_sweep"
    ai_suggested = "ai_suggested"


class SessionWindow(BaseModel):
    name: str
    start: str = Field(pattern=r"^\d{2}:\d{2}$")  # HH:MM 24h UTC
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class MarketRegime(BaseModel):
    preferred: list[str] = []
    avoid: list[str] = []


class Indicator(BaseModel):
    name: str
    parameters: dict = Field(default_factory=dict)


class Rule(BaseModel):
    id: str
    description: str
    expression: str

    @field_validator("id")
    @classmethod
    def id_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("rule id must not be blank")
        return v


class RiskManagement(BaseModel):
    risk_per_trade_pct: float = Field(gt=0, le=5)
    max_daily_loss_pct: float = Field(gt=0, le=100)
    max_consecutive_losses: int = Field(ge=0)
    max_open_positions: int = Field(ge=1)
    max_trades_per_day: int = Field(ge=0)
    stop_loss_method: Literal["ATR", "FIXED", "STRUCTURE", "VOLATILITY"]
    stop_loss_parameters: dict = Field(default_factory=dict)
    take_profit_method: Literal["risk_reward", "ATR", "FIXED", "STRUCTURE"]
    take_profit_parameters: dict = Field(default_factory=dict)


class ExecutionFilters(BaseModel):
    max_spread_pips: float = Field(ge=0)
    max_slippage_pips: float = Field(ge=0)
    minimum_atr_pips: float = Field(ge=0)
    news_blackout_minutes_before: int = Field(ge=0)
    news_blackout_minutes_after: int = Field(ge=0)


class StrategySpec(BaseModel):
    name: str
    version: str
    strategy_family: StrategyFamily
    supported_pairs: list[str] = Field(min_length=1)
    supported_timeframes: list[str] = Field(min_length=1)
    sessions_utc: list[SessionWindow]
    market_regime: MarketRegime = Field(default_factory=MarketRegime)
    indicators: list[Indicator] = Field(default_factory=list)
    entry_rules: list[Rule] = Field(default_factory=list)
    exit_rules: list[Rule] = Field(default_factory=list)
    risk_management: RiskManagement
    execution_filters: ExecutionFilters = Field(default_factory=ExecutionFilters)
    assumptions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    plain_english_explanation: str = ""
    confidence_notes: str = (
        "This is a hypothesis requiring backtesting and paper-trading validation."
    )

    @model_validator(mode="after")
    def require_rules(self) -> "StrategySpec":
        if not self.entry_rules and not self.exit_rules:
            raise ValueError("strategy must define at least one entry or exit rule")
        return self

"""AI Strategy Analyzer: free-text strategy -> structured, testable analysis.

The analyzer converts a plain-English strategy description into the structured
JSON object defined by the ADD-ON spec, marks its testability, and (only for
fully testable output) converts it into the allow-listed, safe strategy DSL so
it can be persisted via the existing ``/strategies`` API and run by the
real-data backtest engine.

exec() / eval() are never used anywhere in this pipeline. All rule expressions
come from deterministic, allow-listed templates or are validated through the
DSL validator before being persisted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.strategy import (
    StrategyFamily,
    StrategySpec,
)

ALLOWED_TIMEFRAMES = frozenset({"M1", "M5", "M15", "M30", "H1", "H4", "D1"})

MAX_PROMPT_LENGTH = 4000
MIN_PROMPT_LENGTH = 10


class AIEntryRule(BaseModel):
    side: Literal["long", "short"]
    rule: str


class AIExitRule(BaseModel):
    rule: str


class AIRiskRules(BaseModel):
    risk_per_trade_pct: float = Field(default=0.25, gt=0, le=5)
    max_trades_per_day: int = Field(default=5, ge=0)
    max_daily_loss_pct: float = Field(default=1.0, gt=0, le=100)
    max_spread_pips: float = Field(default=1.2, ge=0)


class AIStopLoss(BaseModel):
    type: Literal["ATR", "FIXED", "STRUCTURE"] = "ATR"
    atr_period: int = Field(default=14, ge=1, le=100)
    multiplier: float = Field(default=1.2, gt=0, le=10)


class AITakeProfit(BaseModel):
    type: Literal["RISK_REWARD", "ATR", "FIXED"] = "RISK_REWARD"
    ratio: float = Field(default=1.5, gt=0, le=50)


class AIIndicator(BaseModel):
    name: str
    parameters: dict = Field(default_factory=dict)


class AISession(BaseModel):
    name: str
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class AIStrategyAnalysis(BaseModel):
    """The exact structure the AI analyzer must produce."""

    name: str = Field(min_length=1)
    description: str = ""
    strategy_family: StrategyFamily
    timeframe: str
    recommended_symbols: list[str] = Field(min_length=1)
    sessions_utc: list[AISession] = Field(min_length=1)
    indicators: list[AIIndicator] = Field(default_factory=list)
    entry_rules: list[AIEntryRule] = Field(min_length=1)
    exit_rules: list[AIExitRule] = Field(default_factory=list)
    risk_rules: AIRiskRules = Field(default_factory=AIRiskRules)
    stop_loss: AIStopLoss = Field(default_factory=AIStopLoss)
    take_profit: AITakeProfit = Field(default_factory=AITakeProfit)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    testability_status: Literal["VALID", "NEEDS_USER_INPUT", "INVALID"] = (
        "NEEDS_USER_INPUT"
    )

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        if v not in ALLOWED_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}")
        return v

    @field_validator("strategy_family", mode="before")
    @classmethod
    def _family(cls, v) -> StrategyFamily:
        if isinstance(v, StrategyFamily):
            return v
        try:
            return StrategyFamily(str(v).lower())
        except ValueError:
            return StrategyFamily.ai_suggested

    @field_validator("entry_rules", mode="before")
    @classmethod
    def _entry_rules_shape(cls, v):
        if isinstance(v, list):
            out = []
            for item in v:
                if isinstance(item, AIEntryRule) or isinstance(item, dict) and "side" in item:
                    out.append(item)
                elif isinstance(item, str):
                    out.append({"side": "long", "rule": item})
                else:
                    raise ValueError("entry_rules items must have a side")
            return out
        return v


class StrategyAnalyzeRequest(BaseModel):
    prompt_text: str = Field(
        min_length=MIN_PROMPT_LENGTH, max_length=MAX_PROMPT_LENGTH
    )
    provider: Literal["auto", "mock", "llm"] = "auto"


class StrategyAnalyzeResponse(BaseModel):
    analysis: AIStrategyAnalysis
    converted: bool = False
    strategy_spec: StrategySpec | None = None
    cache_hit: bool = False
    provider_used: str = "mock"
    text_sha256: str = ""
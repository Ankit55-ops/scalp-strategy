from pydantic import BaseModel, Field

from app.schemas.strategy import StrategyFamily, StrategySpec


class StrategyGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    pairs: list[str] = Field(min_length=1)
    timeframe: str = Field(pattern=r"^(M1|M5|M15|H1)$")
    session_name: str = "London"
    strategy_family: StrategyFamily = StrategyFamily.ai_suggested
    risk_profile_name: str = "default"
    max_spread_pips: float | None = None
    risk_per_trade_pct: float | None = None
    max_trades_per_day: int | None = None
    provider: str = "mock"


class StrategyGenerateResponseItem(BaseModel):
    candidate_id: str
    spec: StrategySpec


class StrategyGenerateResponse(BaseModel):
    candidates: list[StrategyGenerateResponseItem]


class StrategyCreate(BaseModel):
    spec: StrategySpec
    notes: str = ""


class StrategyVersionCreate(BaseModel):
    spec: StrategySpec
    notes: str = ""

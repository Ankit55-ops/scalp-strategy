from pydantic import BaseModel, Field


class KillSwitchRequest(BaseModel):
    scope: str = Field(pattern=r"^(global|strategy|pair)$")
    resource_id: str | None = None
    enabled: bool = True
    reason: str = Field(min_length=1)


class RiskProfileCreate(BaseModel):
    name: str
    risk_per_trade_pct: float = 0.25
    max_daily_loss_pct: float = 1.0
    max_weekly_loss_pct: float = 3.0
    max_drawdown_pct: float = 10.0
    max_consecutive_losses: int = 3
    max_open_positions: int = 1
    max_trades_per_day: int = 5
    max_correlated_exposure_pct: float = 2.0
    max_spread_pips: float = 1.2
    max_slippage_pips: float = 0.5
    news_blackout_minutes_before: int = 15
    news_blackout_minutes_after: int = 15
    correlated_currency_groups: list[list[str]] | None = None
    hard_stop_distance_pips: float = 0.0
    is_active: bool = True


class RiskProfileUpdate(BaseModel):
    name: str | None = None
    risk_per_trade_pct: float | None = Field(default=None, gt=0, le=5)
    max_daily_loss_pct: float | None = Field(default=None, gt=0, le=100)
    max_weekly_loss_pct: float | None = Field(default=None, gt=0, le=100)
    max_drawdown_pct: float | None = Field(default=None, gt=0, le=100)
    max_consecutive_losses: int | None = Field(default=None, ge=0)
    max_open_positions: int | None = Field(default=None, ge=1)
    max_trades_per_day: int | None = Field(default=None, ge=0)
    max_correlated_exposure_pct: float | None = Field(default=None, ge=0)
    max_spread_pips: float | None = Field(default=None, ge=0)
    max_slippage_pips: float | None = Field(default=None, ge=0)
    news_blackout_minutes_before: int | None = Field(default=None, ge=0)
    news_blackout_minutes_after: int | None = Field(default=None, ge=0)
    correlated_currency_groups: list[list[str]] | None = None
    hard_stop_distance_pips: float | None = Field(default=None, ge=0)
    is_active: bool | None = None


class RiskProfileActivate(BaseModel):
    confirm: bool = True


class RiskProfileOut(BaseModel):
    id: str
    name: str
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    max_open_positions: int
    max_trades_per_day: int
    max_correlated_exposure_pct: float
    max_spread_pips: float
    max_slippage_pips: float
    news_blackout_minutes_before: int
    news_blackout_minutes_after: int
    correlated_currency_groups: list[list[str]] | None = None
    hard_stop_distance_pips: float
    is_active: bool
    created_at: str
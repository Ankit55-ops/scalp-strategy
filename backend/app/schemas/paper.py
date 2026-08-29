import math

from pydantic import BaseModel, Field, field_validator


def _finite_positive(value: float) -> float:
    if value is None:
        return value
    if not math.isfinite(value):
        raise ValueError("value must be a finite number")
    return value


class PaperTradingStart(BaseModel):
    balance: float = 100000.0
    strategy_ids: list[str] = []

    @field_validator("balance")
    @classmethod
    def _validate_balance(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("balance must be a positive finite number")
        return round(v, 2)


class PaperTradingStop(BaseModel):
    close_positions: bool = True


class PaperStatus(BaseModel):
    is_active: bool
    balance: float
    equity: float
    open_positions: int
    closed_trades: int
    trading_state: str = "ACTIVE"
    state_reason: str | None = None
    pending_orders: int = 0


class PaperOrderRequest(BaseModel):
    strategy_id: str
    side: str = Field(pattern=r"^(long|short|buy|sell)$")
    size_units: float | None = Field(default=None, gt=0, le=1_000_000_000)

    @field_validator("size_units")
    @classmethod
    def _validate_size(cls, v: float | None) -> float | None:
        return _finite_positive(v)


class PaperOrderResult(BaseModel):
    approved: bool
    position_id: str | None = None
    order_id: str | None = None
    symbol: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str | None = None
    correlation_id: str | None = None


class PaperPositionOut(BaseModel):
    id: str
    order_id: str | None = None
    symbol: str
    side: str
    size_units: float
    entry_price: float
    mark_price: float
    stop_loss: float
    take_profit: float
    open_ts: float
    unrealized_pnl: float


class PaperCloseResult(BaseModel):
    id: str
    status: str
    exit_price: float
    net_pnl: float
    pips: float
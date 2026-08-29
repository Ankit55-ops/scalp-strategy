from pydantic import BaseModel


class PaperTradingStart(BaseModel):
    balance: float = 100000.0
    strategy_ids: list[str] = []


class PaperTradingStop(BaseModel):
    close_positions: bool = True


class PaperStatus(BaseModel):
    is_active: bool
    balance: float
    equity: float
    open_positions: int
    closed_trades: int

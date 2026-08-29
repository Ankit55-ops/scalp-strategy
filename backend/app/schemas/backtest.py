from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    strategy_id: str
    pairs: list[str] = Field(min_length=1)
    timeframe: str = Field(pattern=r"^(M1|M5|M15|H1)$")
    date_from: str
    date_to: str
    balance: float = 100000.0
    spread_pips: float | None = None
    commission_per_lot: float | None = None
    slippage_pips: float | None = None
    run_walk_forward: bool = False
    run_monte_carlo: bool = False
    mc_iterations: int = 500
    wf_window_bars: int | None = None
    wf_step_bars: int | None = None
    idempotency_key: str | None = None


class BacktestJobOut(BaseModel):
    id: str
    status: str
    progress: float
    error: str | None = None

    model_config = {"from_attributes": True}

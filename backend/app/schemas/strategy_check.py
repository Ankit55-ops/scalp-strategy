from typing import Literal

from pydantic import BaseModel


class StrategyCheckItem(BaseModel):
    check: str
    severity: Literal["pass", "info", "warn", "fail"]
    detail: str


class StrategyCheckReport(BaseModel):
    strategy_id: str
    version: str
    checked_at: str
    overall: str
    summary: str
    checks: list[StrategyCheckItem]
    intrabar: dict | None = None
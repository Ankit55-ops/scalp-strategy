from pydantic import BaseModel, Field


class ChartLayoutCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    symbol: str
    timeframe: str = "M5"
    layout: dict


class ChartLayoutOut(BaseModel):
    id: str
    name: str
    symbol: str
    timeframe: str
    layout: dict
    created_at: str
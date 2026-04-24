from datetime import datetime

from pydantic import BaseModel, Field


class Position(BaseModel):
    ticker: str
    quantity: float = Field(gt=0)
    exchange: str = ""
    category: str = ""


class PositionValue(BaseModel):
    ticker: str
    quantity: float
    price: float
    native_currency: str = "USD"
    value_brl: float
    change_pct: float | None = None
    change_pct_1w: float | None = None
    market_open: bool = True
    exchange: str = ""
    category: str = ""
    day_high: float | None = None
    day_low: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None


class PortfolioSnapshot(BaseModel):
    positions: list[PositionValue]
    total_value: float
    total_value_24h: float | None = None
    total_value_1w: float | None = None
    currency: str = "BRL"
    timestamp: datetime

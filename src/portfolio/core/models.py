# `datetime` is Python's standard library type for representing a point in time.
from datetime import datetime

# Pydantic is a library for data validation. By subclassing `BaseModel`, any class
# you create gets automatic type checking, parsing, and serialization for free.
# `Field` lets you add extra constraints (like "must be greater than 0").
from pydantic import BaseModel, Field


# A `Position` is what the user holds: a ticker symbol and how many shares/units.
# The `= Field(gt=0)` means Pydantic will reject any quantity that is not > 0.
class Position(BaseModel):
    ticker: str
    quantity: float = Field(gt=0)  # gt = "greater than"
    exchange: str = ""             # default empty string means this column is optional
    category: str = ""


# A `FixedIncomePosition` is a simple investment with a known BRL value —
# no ticker or price fetch needed; the amount is the value by definition.
class FixedIncomePosition(BaseModel):
    name: str
    amount_brl: float = Field(gt=0)


# A `PositionValue` enriches a Position with live market data: price, BRL value, etc.
# `float | None` is Python 3.10+ syntax for "this can be a float OR None (missing)".
# It is equivalent to `Optional[float]` from the `typing` module.
class PositionValue(BaseModel):
    ticker: str
    quantity: float
    price: float
    native_currency: str = "USD"   # the currency the asset trades in (e.g. USD for US stocks)
    value_brl: float               # quantity * price * fx_rate, always in BRL
    change_pct: float | None = None       # 24-hour price change as a percentage
    change_pct_1w: float | None = None    # 1-week price change as a percentage
    market_open: bool = True       # True while the exchange is in its regular session
    exchange: str = ""
    category: str = ""
    day_high: float | None = None
    day_low: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None


# A `PortfolioSnapshot` is a complete picture of the portfolio at a single point in
# time. The engine publishes one of these every refresh cycle. The UI only ever
# reads snapshots — it never talks to the fetcher directly (separation of concerns).
class PortfolioSnapshot(BaseModel):
    positions: list[PositionValue]
    total_value: float                        # sum of variable positions only
    total_value_24h: float | None = None
    total_value_1w: float | None = None
    fixed_income: list[FixedIncomePosition] = []
    fixed_income_total: float = 0.0           # sum of all fixed income amounts
    currency: str = "BRL"
    timestamp: datetime

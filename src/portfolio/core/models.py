# This module defines every data model used across the entire application.
#
# "Model" here means a Python class that describes the *shape* of data — what
# fields it has, what types they are, and what rules must hold before the data
# is considered valid.
#
# Why use Pydantic?
# -----------------
# Pydantic (https://docs.pydantic.dev) is a validation library. Every time you
# create a Pydantic model with `SomeModel(field=value)`, it automatically:
#   1. Checks that the value has the right type (or can be safely converted).
#   2. Enforces extra constraints declared with `Field(...)` (e.g. gt=0).
#   3. Raises a clear `ValidationError` — with the field name and problem — if
#      anything is wrong, instead of silently storing bad data.
#
# Why are the models here and not inside the files that use them?
# --------------------------------------------------------------
# Keeping models in one place (the "models" module) means every layer of the app
# imports from the same source of truth. If the shape of a `PortfolioSnapshot`
# changes, you only edit one file and all callers benefit automatically.

# `datetime` is Python's standard library type for representing a specific point
# in time (date + time + timezone). We use it to stamp every snapshot so the UI
# can show "last updated at HH:MM".
from datetime import datetime

# `BaseModel` is the base class every Pydantic model inherits from.
# `Field` is a helper that attaches extra metadata or constraints to a field,
# such as `Field(gt=0)` meaning "the value must be greater than 0".
from pydantic import BaseModel, Field


class Position(BaseModel):
    """Raw data for one holding, exactly as read from the Excel spreadsheet.

    This is the *input* model — it represents what the user typed and nothing
    more. No live prices, no BRL values, no P&L. Those are computed later and
    stored in `PositionValue`.

    Separating raw input from enriched output keeps the data flow clean:
      Excel → Position → (fetch prices) → PositionValue → PortfolioSnapshot
    """

    # The trading symbol used to look up the price.
    # Examples: "AAPL" (US stock), "MXRF11.SA" (B3 FII), "BTC-USD" (crypto).
    ticker: str

    # `Field(gt=0)` tells Pydantic to reject any quantity that is zero or negative.
    # `gt` stands for "greater than". This prevents nonsensical entries like -5 shares.
    quantity: float = Field(gt=0)

    # `= ""` is a default value. If the Excel row has no exchange column, the field
    # is just an empty string instead of raising an error. This makes the column optional.
    exchange: str = ""    # e.g. "NYSE", "B3", "Binance" — for display only

    category: str = ""    # e.g. "Stocks", "Crypto", "FII" — used for filtering and charts

    # `float | None` is Python 3.10+ syntax meaning "this field can be a float OR
    # the special value None". It is equivalent to `Optional[float]` from `typing`.
    # `= None` makes it optional: if the user left the avg-price column blank, we store None
    # and skip the P&L calculation rather than crashing.
    avg_price_native: float | None = None  # average purchase price in the asset's native currency


class FixedIncomePosition(BaseModel):
    """A fixed-income investment whose current value is entered directly by the user.

    Unlike stocks or crypto, fixed-income assets (e.g. a savings account, a CDB,
    Tesouro Direto bonds) don't trade on an exchange. Their value at any moment is
    simply whatever the user types into the spreadsheet — there is no "live price"
    to fetch. The amount *is* the value, so we store it directly in BRL.
    """

    name: str            # Descriptive label, e.g. "CDB Banco XYZ 120% CDI" or "Poupança"
    amount_brl: float = Field(gt=0)  # Current BRL value; must be positive


class PositionValue(BaseModel):
    """A `Position` enriched with live market data: price, BRL conversion, and performance.

    The engine creates one `PositionValue` per `Position` after fetching prices.
    The UI only ever reads `PositionValue` objects — it never talks to the fetcher
    or reads from Excel directly (that is what "separation of concerns" means in
    this project).

    Understanding `float | None = None`
    ------------------------------------
    Many fields use this pattern. It means:
      - The field CAN hold a float or the value `None` (absence of data).
      - `= None` is the default, so if the API didn't return that piece of data
        (e.g. no historical prices available), the field is simply None and Pydantic
        won't complain. The UI then shows "N/A" instead of crashing.
    """

    ticker: str
    quantity: float
    price: float                   # Last traded price in the asset's native currency
    native_currency: str = "USD"   # The currency the asset trades in, e.g. "USD", "EUR", "BRL"
    value_brl: float               # quantity × price × FX-rate — always expressed in BRL

    change_pct: float | None = None       # 24-hour price change, e.g. -1.5 means the price fell 1.5%
    change_pct_1w: float | None = None    # Price change over the last 5 trading days
    change_pct_6m: float | None = None    # Price change over roughly the last 6 months
    change_pct_12m: float | None = None   # Price change over the last 12 months (1 year)

    avg_price_native: float | None = None  # User-supplied average purchase price (from Excel)
    pnl_pct: float | None = None          # Unrealised P&L: (current − avg) / avg × 100

    # `bool` is Python's boolean type. Its only two values are `True` and `False`.
    # We default to `True` so that assets without market-state data (e.g. crypto,
    # which never "closes") are always shown as open.
    market_open: bool = True

    exchange: str = ""    # Copied from Position for display in the UI
    category: str = ""    # Copied from Position for filtering in the UI

    # Intraday and 52-week price range — shown in the expandable detail row.
    day_high: float | None = None         # Highest price reached today (intraday high)
    day_low: float | None = None          # Lowest price reached today (intraday low)
    week_52_high: float | None = None     # Highest price over the past 52 weeks
    week_52_low: float | None = None      # Lowest price over the past 52 weeks


class BitcoinMetrics(BaseModel):
    """On-chain and sentiment metrics for Bitcoin, fetched from free public APIs.

    The bitcoin engine publishes one of these every 5 minutes. All fields are
    optional because any individual API can fail independently — a partial snapshot
    is still displayed, with "N/A" for missing metrics.

    Each metric comes from a different API endpoint (see bitcoin_fetcher.py):
      - fear_greed_*      → alternative.me
      - halving_*         → mempool.space
      - funding_rate      → Binance perpetuals
      - mvrv_ratio        → CoinMetrics Community API
      - mayers_*, btc_price_usd → Binance spot klines (daily candles)
    """

    # The Alternative.me Fear & Greed Index (0 = Extreme Fear, 100 = Extreme Greed).
    # Historically, extreme fear has been a buying signal; extreme greed a caution signal.
    fear_greed_value: int | None = None
    fear_greed_label: str | None = None   # Text label, e.g. "Fear", "Neutral", "Extreme Greed"

    # Bitcoin halves its block reward every 210,000 blocks (roughly every 4 years).
    # Tracking the countdown helps anticipate the supply shock that historically
    # precedes a bull run.
    halving_blocks_remaining: int | None = None
    halving_estimated_date: datetime | None = None  # Rough calendar estimate (±weeks, not exact)

    # Binance perpetual funding rate: the % paid every 8 hours between longs and shorts.
    # Positive rate → longs pay shorts (market is positioned bullish).
    # Negative rate → shorts pay longs (market is positioned bearish).
    funding_rate: float | None = None     # Expressed as a percentage, e.g. 0.01 means 0.01%

    # MVRV = Market Value / Realized Value (on-chain metric from CoinMetrics).
    # < 1 → coins are worth less than their aggregate cost basis (deep undervalue).
    # 1–2.4 → fair value zone.
    # 2.4–3.5 → overvalued zone.
    # > 3.5 → historically coincides with cycle tops.
    mvrv_ratio: float | None = None

    # Mayer's Multiple = current price / 200-day moving average.
    # The 200-day MA is a widely watched trend indicator.
    # Below 0.8: Mayer's suggested accumulation zone.
    # Above 2.4: Mayer's suggested caution threshold.
    mayers_multiple: float | None = None
    mayers_ma200: float | None = None     # The 200-day MA price itself in USD (shown as subtitle)

    # Current BTC spot price in USD, taken from the last daily close on Binance.
    # Updated every 5 minutes along with the other metrics (no extra API call —
    # it is extracted from the same klines data used to compute Mayer's Multiple).
    btc_price_usd: float | None = None

    timestamp: datetime   # When this snapshot was created (UTC)


class PortfolioSnapshot(BaseModel):
    """A complete, point-in-time picture of the entire portfolio.

    The engine publishes one snapshot per refresh cycle (default: every 30 seconds)
    by placing it on an `asyncio.Queue`. The UI polls that queue and re-renders
    every time it picks up a new snapshot.

    Why an immutable snapshot instead of live shared state?
    -------------------------------------------------------
    If the engine and UI shared a mutable object, one could modify it while the
    other is reading it — a classic concurrency bug called a "race condition".
    By creating a new, immutable snapshot each cycle and handing it to the UI via a
    queue, the two sides never share mutable state. Each snapshot is a frozen
    photograph of the portfolio at one moment in time.

    `model_copy(update={...})` (used in app.py) creates a modified *copy* of a
    snapshot without mutating the original — another Pydantic feature that
    supports this immutable style.
    """

    positions: list[PositionValue]   # All variable-asset positions with live market data

    # Sum of value_brl for all variable positions (excludes fixed income).
    total_value: float

    # Back-calculated historical totals — used to compute the period delta shown
    # in the status bar, e.g. "24h: +R$1,234 (+2.5%)".
    # Formula: value_at_time_T = value_today / (1 + change_pct / 100)
    total_value_24h: float | None = None   # Estimated portfolio value 24 hours ago
    total_value_1w: float | None = None    # Estimated portfolio value 1 week ago
    total_value_6m: float | None = None    # Estimated portfolio value 6 months ago
    total_value_12m: float | None = None   # Estimated portfolio value 12 months ago

    # `list[FixedIncomePosition] = []` — default is an empty list, not None.
    # An empty list is safer than None because you can always iterate over it
    # without a None-check: `for fi in snapshot.fixed_income` always works.
    fixed_income: list[FixedIncomePosition] = []
    fixed_income_total: float = 0.0        # Sum of all fixed-income BRL amounts

    currency: str = "BRL"                  # Reporting currency for value_brl fields (always BRL)
    timestamp: datetime                    # UTC timestamp of when the engine built this snapshot

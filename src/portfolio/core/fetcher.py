# This module fetches live market prices and currency exchange rates.
#
# It sits between the reader (which gives us raw positions from Excel) and the
# engine (which assembles everything into a `PortfolioSnapshot`).
#
# Data pipeline position:
#   reader.py → [list[Position]] → fetcher.py → [prices + FX rates] → engine.py
#
# Design decisions
# ----------------
# 1. Blocking I/O in sync helpers: yfinance is not async-native, so price fetching
#    is done in regular (synchronous) functions, then handed to `asyncio.to_thread`
#    to avoid freezing the event loop.
# 2. "Safe" variants: every public-facing fetch has a `_*_safe` counterpart that
#    catches errors and returns None/fallback values. This means one failed ticker
#    never aborts the entire portfolio refresh.
# 3. Concurrent fetching: all tickers are fetched at the same time using
#    `asyncio.TaskGroup`, so 20 tickers takes roughly as long as 1.

import asyncio
from datetime import timedelta

import structlog

# yfinance is a third-party library that wraps the Yahoo Finance API.
# It provides easy access to historical prices, real-time quotes, and metadata.
import yfinance as yf

from portfolio.core.exceptions import PriceFetchError

log = structlog.get_logger()

# A type alias: instead of repeating this long tuple type everywhere, we give it
# a short name. `tuple[A, B, C]` is a fixed-length sequence whose element types
# are known at each position — unlike a `list`, which can be any length.
#
# The tuple holds, in order:
#   (price, change_pct_24h, change_pct_1w, change_pct_6m, change_pct_12m,
#    market_open, currency, day_high, day_low, week_52_high, week_52_low)
_PriceData = tuple[
    float, float | None, float | None, float | None, float | None, bool, str,
    float | None, float | None, float | None, float | None,
]


def _safe_float(val: object) -> float | None:
    """Return a float, or None for None/NaN/non-numeric values.

    `object` is the base type of everything in Python — using it here says
    "I accept any value and will handle it carefully".

    NaN (Not a Number) is a special float value that compares unequal to itself:
        float('nan') != float('nan')  # True!
    So `f != f` is a concise NaN check that works without importing `math.isnan`.
    """
    if val is None:
        return None
    try:
        f = float(val)  # type: ignore[arg-type]
        return None if f != f else f  # NaN → None
    except (TypeError, ValueError):
        # `except (TypeA, TypeB)` catches either exception type in one clause.
        return None


def _fetch_price_sync(ticker: str) -> _PriceData:
    """Fetch all price data for one ticker from Yahoo Finance (blocking).

    This is a regular (synchronous) function — it blocks the calling thread
    while waiting for the network response. It is never called directly from
    async code; instead, `fetch_price` wraps it with `asyncio.to_thread` so
    it runs in a background thread without freezing the event loop.

    Returns:
        A `_PriceData` tuple with price, percentage changes, market state,
        currency, and intraday/52-week price ranges.

    Raises:
        PriceFetchError: if no valid price is returned or the API call fails.
    """
    try:
        data = yf.Ticker(ticker)

        # `fast_info` is a lightweight cache of the most common fields.
        # It makes one small HTTP request and avoids the heavier `data.info` call.
        info = data.fast_info
        price = info.last_price

        # Two different "missing value" checks in one condition:
        #   `price is None`   — the field was simply not returned by the API
        #   `price != price`  — the field came back as NaN (see _safe_float above)
        if price is None or price != price:
            raise PriceFetchError(f"No price returned for {ticker}")
        price = float(price)

        # 24-hour percentage change: (current − previous_close) / previous_close × 100
        change_pct: float | None = None
        prev_close = info.previous_close
        if prev_close is not None and prev_close == prev_close and prev_close != 0:
            change_pct = (price - float(prev_close)) / float(prev_close) * 100.0

        # Fetch 1 year of daily history so we can compute 1W, 6M, and 12M changes
        # in a single API call rather than three separate calls.
        change_pct_1w: float | None = None
        change_pct_6m: float | None = None
        change_pct_12m: float | None = None

        # `auto_adjust=False` keeps raw (unadjusted) closing prices, on the same
        # basis as `fast_info.last_price`. The default (True) adjusts historical
        # prices downward for past dividends, which would overstate gains for
        # dividend-paying assets like Brazilian FIIs.
        hist = data.history(period="1y", auto_adjust=False)  # returns a pandas DataFrame
        if not hist.empty:
            closes = hist["Close"]   # a pandas Series of daily closing prices

            # 1W: price 5 trading days ago (index -5 counts 5 from the end).
            # `iloc` is pandas integer-location indexing — it ignores the date
            # index and just counts positions from the beginning (or end with -N).
            if len(closes) >= 5:
                week_open = float(closes.iloc[-5])
                if week_open != 0:
                    change_pct_1w = (price - week_open) / week_open * 100.0

            # 6M: find the row whose date is closest to 6 months ago.
            # Using a date lookup instead of a fixed row count handles assets that
            # trade every day (e.g. crypto) vs. only on weekdays (stocks/FIIs).
            six_months_ago = closes.index[-1] - timedelta(days=182)
            # `get_indexer([date], method="nearest")` returns the index position
            # of the row closest to the given date (within the available data).
            idx_6m = closes.index.get_indexer([six_months_ago], method="nearest")[0]
            price_6m = float(closes.iloc[idx_6m])
            if price_6m != 0:
                change_pct_6m = (price - price_6m) / price_6m * 100.0

            # 12M: price at the very start of the 1-year window (first row).
            price_12m = float(closes.iloc[0])
            if price_12m != 0:
                change_pct_12m = (price - price_12m) / price_12m * 100.0

        # `getattr(obj, "attr", default)` safely reads an attribute that might not
        # exist on the object, returning `default` instead of raising AttributeError.
        # yfinance's `fast_info` doesn't always populate every field.
        day_high = _safe_float(getattr(info, "day_high", None))
        day_low = _safe_float(getattr(info, "day_low", None))
        week_52_high = _safe_float(getattr(info, "fifty_two_week_high", None))
        week_52_low = _safe_float(getattr(info, "fifty_two_week_low", None))

        # `data.info` is a heavier call that fetches additional metadata like
        # `marketState` and `currency`, which are absent from `fast_info`.
        ticker_info = data.info
        market_state: str | None = ticker_info.get("marketState")  # dict.get returns None if key missing
        # `== "REGULAR"` means the exchange is in its normal trading session.
        # Crypto always reports "REGULAR" because it never closes.
        market_open = market_state == "REGULAR" if market_state is not None else True
        # `or "USD"` handles both None (key missing) and "" (empty string) → fall back to USD.
        native_currency: str = ticker_info.get("currency") or "USD"

        log.debug(
            "price_fetched",
            ticker=ticker,
            price=price,
            change_pct=change_pct,
            change_pct_1w=change_pct_1w,
            market_state=market_state,
            market_open=market_open,
            native_currency=native_currency,
        )
        return price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency, day_high, day_low, week_52_high, week_52_low
    except PriceFetchError:
        raise   # re-raise our own error unchanged — don't wrap it below
    except Exception as exc:
        raise PriceFetchError(f"Failed to fetch price for {ticker}: {exc}") from exc


async def fetch_price(ticker: str) -> _PriceData:
    """Fetch price and related data for one ticker (async wrapper).

    `asyncio.to_thread(fn, arg)` is the standard pattern for running a blocking
    function inside an async context without blocking the event loop. It submits
    the function to a thread pool and `await`s the result.
    """
    return await asyncio.to_thread(_fetch_price_sync, ticker)


async def _fetch_price_safe(
    ticker: str,
) -> tuple[str, float | None, float | None, float | None, float | None, float | None, bool, str, float | None, float | None, float | None, float | None]:
    """Fetch price for one ticker, returning None values on failure instead of raising.

    "Safe" variants catch exceptions internally and return a sentinel (None here)
    so the caller can continue processing the remaining tickers even when one
    fails. The error is logged as a warning — it is not silently swallowed.

    The ticker string is included in the return value so the caller can reconstruct
    a mapping of `{ticker: data}` after all concurrent tasks finish.
    """
    try:
        price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = await fetch_price(ticker)
        return ticker, price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency, day_high, day_low, week_52_high, week_52_low
    except PriceFetchError as exc:
        log.warning("price_fetch_skipped", ticker=ticker, error=str(exc))
        # Return a full-length tuple of Nones so the caller always gets the same shape back.
        return ticker, None, None, None, None, None, True, "USD", None, None, None, None


async def fetch_all_prices(tickers: list[str]) -> dict[str, _PriceData]:
    """Fetch prices for all tickers concurrently and return a dict of results.

    `asyncio.TaskGroup` (Python 3.11+) is structured concurrency: all tasks
    start at the same time, and the `async with` block exits only when every task
    has finished (or one raises an unhandled exception). This is safer than the
    older `asyncio.gather` because exceptions propagate immediately and remaining
    tasks are cancelled automatically.

    Result: `{ticker: _PriceData}` — tickers whose fetch failed are omitted.

    Example:
        prices = await fetch_all_prices(["AAPL", "BTC-USD", "MXRF11.SA"])
        # → {"AAPL": (173.5, 1.2, ...), "BTC-USD": (67000.0, -0.3, ...), ...}
    """
    async with asyncio.TaskGroup() as tg:
        # Dictionary comprehension: build `{ticker: task}` for every ticker at once.
        # All tasks are launched concurrently — they don't wait for each other.
        # This means 20 tickers take roughly as long as 1.
        tasks = {t: tg.create_task(_fetch_price_safe(t)) for t in tickers}

    # After the TaskGroup exits, all tasks are guaranteed to be done.
    result: dict[str, _PriceData] = {}
    for ticker, task in tasks.items():
        _ticker, price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = task.result()
        if price is not None:  # omit tickers whose fetch failed (price will be None)
            result[ticker] = (price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency, day_high, day_low, week_52_high, week_52_low)
    return result


def _fetch_fx_rate_sync(currency: str) -> float:
    """Fetch the {currency}→BRL exchange rate from Yahoo Finance (blocking).

    Yahoo Finance exposes currency pairs with the `=X` suffix.
    For example, the USD/BRL rate is available as the ticker "USDBRL=X".
    """
    pair = f"{currency}BRL=X"   # f-string: embed `currency` directly into the string literal
    rate = yf.Ticker(pair).fast_info.last_price
    if rate is None or rate != rate:
        raise PriceFetchError(f"No FX rate returned for {pair}")
    return float(rate)


async def _fetch_fx_rate_safe(currency: str) -> tuple[str, float]:
    """Fetch the {currency}→BRL rate, falling back to 1.0 on any failure.

    Returning 1.0 on failure is a "graceful degradation": the portfolio still
    renders (just with a potentially wrong conversion for that one currency)
    rather than crashing the entire refresh cycle.

    The currency code is returned alongside the rate so the caller can build
    a `{currency: rate}` dictionary from the concurrent task results.
    """
    try:
        rate = await asyncio.to_thread(_fetch_fx_rate_sync, currency)
        log.debug("fx_rate_fetched", currency=currency, rate=rate)
        return currency, rate
    except Exception as exc:
        log.warning("fx_rate_fetch_failed", currency=currency, error=str(exc))
        return currency, 1.0   # treat as already BRL if the FX fetch fails


async def fetch_fx_rates(currencies: list[str]) -> dict[str, float]:
    """Fetch BRL conversion rates for all given currencies concurrently.

    BRL→BRL is always 1.0 and is included in the result without a network call.
    Failed fetches fall back to 1.0 and are logged as warnings.

    Example:
        rates = await fetch_fx_rates(["USD", "EUR", "BRL"])
        # → {"BRL": 1.0, "USD": 5.12, "EUR": 5.63}
    """
    # Start with BRL=1.0 already in the result — no network call needed.
    result: dict[str, float] = {"BRL": 1.0}

    # List comprehension: build a new list containing only non-BRL currencies.
    # The `if c != "BRL"` part is the filter condition — only items where it's
    # True end up in the resulting list.
    non_brl = [c for c in currencies if c != "BRL"]
    if not non_brl:
        return result   # early return avoids creating an empty TaskGroup

    async with asyncio.TaskGroup() as tg:
        tasks = {c: tg.create_task(_fetch_fx_rate_safe(c)) for c in non_brl}
    for task in tasks.values():
        currency, rate = task.result()
        result[currency] = rate
    return result

import asyncio

import structlog

# yfinance is a third-party library that wraps the Yahoo Finance API.
# It provides easy access to historical prices, real-time quotes, and metadata.
import yfinance as yf

from portfolio.core.exceptions import PriceFetchError

log = structlog.get_logger()

# A type alias: instead of repeating this long tuple type everywhere, we give it
# a short name. `tuple[A, B, C]` is a fixed-length sequence whose element types
# are known at each position — unlike a list, which can be any length.
# (price, change_pct_24h, change_pct_1w, market_open, currency,
#  day_high, day_low, week_52_high, week_52_low)
_PriceData = tuple[
    float, float | None, float | None, bool, str,
    float | None, float | None, float | None, float | None,
]


def _safe_float(val: object) -> float | None:
    """Return float, or None for None/NaN/non-numeric values.

    `object` is the base type of everything in Python — using it here says
    "I accept any value and will handle it carefully".
    NaN (Not a Number) is a special float value that compares unequal to itself:
      float('nan') != float('nan')  # True!
    So `f != f` is a concise NaN check.
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
    """Return price data including intraday and 52-week ranges.

    This is a regular (synchronous / blocking) function. It is never called
    directly from async code — instead it is handed to `asyncio.to_thread`
    so it runs in a background thread without freezing the event loop.

    change_pct_24h is None when previous_close is unavailable.
    change_pct_1w is None when insufficient history is available.
    market_open is True only when market_state == "REGULAR"; crypto markets
    always report "REGULAR" so they are never shown as closed.
    Falls back to True when market_state is unavailable.
    """
    try:
        data = yf.Ticker(ticker)

        # `fast_info` is a lightweight cache of the most common fields.
        # It avoids a heavier network request compared to `data.info`.
        info = data.fast_info
        price = info.last_price

        # Two different "missing value" checks in one condition:
        #   `price is None`    — the field was simply not returned
        #   `price != price`   — the field was returned as NaN (see _safe_float above)
        if price is None or price != price:
            raise PriceFetchError(f"No price returned for {ticker}")
        price = float(price)

        # Calculate 24-hour percentage change:  (current - previous) / previous * 100
        change_pct: float | None = None
        prev_close = info.previous_close
        if prev_close is not None and prev_close == prev_close and prev_close != 0:
            change_pct = (price - float(prev_close)) / float(prev_close) * 100.0

        # 1-week change: compare today's price to the earliest close in the last 5 days.
        change_pct_1w: float | None = None
        hist = data.history(period="5d")  # returns a pandas DataFrame
        if not hist.empty and len(hist) >= 2:
            week_open = float(hist["Close"].iloc[0])  # `.iloc[0]` = first row by position
            if week_open != 0:
                change_pct_1w = (price - week_open) / week_open * 100.0

        # `getattr(obj, "attr", default)` safely reads an attribute that might not
        # exist, returning `default` instead of raising AttributeError.
        day_high = _safe_float(getattr(info, "day_high", None))
        day_low = _safe_float(getattr(info, "day_low", None))
        week_52_high = _safe_float(getattr(info, "fifty_two_week_high", None))
        week_52_low = _safe_float(getattr(info, "fifty_two_week_low", None))

        # `data.info` is the heavier call — it fetches additional metadata like
        # `marketState` and `currency` that are absent from `fast_info`.
        ticker_info = data.info
        market_state: str | None = ticker_info.get("marketState")   # dict.get returns None if key missing
        market_open = market_state == "REGULAR" if market_state is not None else True
        native_currency: str = ticker_info.get("currency") or "USD"  # `or "USD"` handles None and ""

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
        return price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low
    except PriceFetchError:
        raise
    except Exception as exc:
        raise PriceFetchError(f"Failed to fetch price for {ticker}: {exc}") from exc


async def fetch_price(ticker: str) -> _PriceData:
    """Fetch price, 24h %, 1W %, market state, currency, and price ranges for a ticker.

    `await asyncio.to_thread(fn, arg)` is the standard pattern for running a
    blocking function inside an async context without blocking the event loop.
    """
    return await asyncio.to_thread(_fetch_price_sync, ticker)


async def _fetch_price_safe(
    ticker: str,
) -> tuple[str, float | None, float | None, float | None, bool, str, float | None, float | None, float | None, float | None]:
    """Fetch price for a single ticker; return Nones on failure instead of raising.

    "Safe" variants catch exceptions internally and return a sentinel value
    (None here) so the caller can continue processing remaining tickers even
    when one fails. The error is logged as a warning, not silently swallowed.
    """
    try:
        price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = await fetch_price(ticker)
        return ticker, price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low
    except PriceFetchError as exc:
        log.warning("price_fetch_skipped", ticker=ticker, error=str(exc))
        # Return a full tuple of Nones so the caller always gets the same shape back.
        return ticker, None, None, None, True, "USD", None, None, None, None


async def fetch_all_prices(tickers: list[str]) -> dict[str, _PriceData]:
    """Fetch prices for all tickers concurrently.

    `asyncio.TaskGroup` (Python 3.11+) is structured concurrency: all tasks
    start together, and the `async with` block exits only when every task has
    finished (or one raises an unhandled exception). This is safer than the
    older `asyncio.gather` because exceptions are propagated immediately.

    Tickers that fail are logged and omitted from the result rather than
    aborting the entire fetch.
    Returns a dict mapping ticker -> _PriceData.
    """
    async with asyncio.TaskGroup() as tg:
        # Dictionary comprehension: build {ticker: task} for every ticker at once.
        # All tasks are launched concurrently — they don't wait for each other.
        tasks = {t: tg.create_task(_fetch_price_safe(t)) for t in tickers}

    # After the TaskGroup exits, all tasks are done. We collect results here.
    result: dict[str, _PriceData] = {}
    for ticker, task in tasks.items():
        _ticker, price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = task.result()
        if price is not None:  # omit tickers whose fetch failed
            result[ticker] = (price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low)
    return result


def _fetch_fx_rate_sync(currency: str) -> float:
    """Fetch the current {currency}->BRL exchange rate.

    Yahoo Finance exposes FX pairs with the `=X` suffix, e.g. "USDBRL=X".
    """
    pair = f"{currency}BRL=X"   # f-string: embed `currency` directly into the string
    rate = yf.Ticker(pair).fast_info.last_price
    if rate is None or rate != rate:
        raise PriceFetchError(f"No FX rate returned for {pair}")
    return float(rate)


async def _fetch_fx_rate_safe(currency: str) -> tuple[str, float]:
    try:
        rate = await asyncio.to_thread(_fetch_fx_rate_sync, currency)
        log.debug("fx_rate_fetched", currency=currency, rate=rate)
        return currency, rate
    except Exception as exc:
        # If the FX fetch fails, fall back to 1.0 (i.e., treat it as already BRL).
        # This is a graceful degradation: the portfolio still renders, just with a
        # potentially wrong conversion rate for that currency.
        log.warning("fx_rate_fetch_failed", currency=currency, error=str(exc))
        return currency, 1.0


async def fetch_fx_rates(currencies: list[str]) -> dict[str, float]:
    """Fetch BRL conversion rates for all given currencies concurrently.

    BRL maps to 1.0 without a network call. Failed fetches fall back to 1.0
    and are logged as warnings.
    """
    # Start with BRL=1.0 already in the result — no network call needed.
    result: dict[str, float] = {"BRL": 1.0}

    # List comprehension: filter out "BRL" from the list.
    non_brl = [c for c in currencies if c != "BRL"]
    if not non_brl:
        return result   # early return avoids an empty TaskGroup

    async with asyncio.TaskGroup() as tg:
        tasks = {c: tg.create_task(_fetch_fx_rate_safe(c)) for c in non_brl}
    for task in tasks.values():
        currency, rate = task.result()
        result[currency] = rate
    return result

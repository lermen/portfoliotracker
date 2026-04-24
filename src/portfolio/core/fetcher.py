import asyncio

import structlog
import yfinance as yf

from portfolio.core.exceptions import PriceFetchError

log = structlog.get_logger()

# (price, change_pct_24h, change_pct_1w, market_open, currency,
#  day_high, day_low, week_52_high, week_52_low)
_PriceData = tuple[
    float, float | None, float | None, bool, str,
    float | None, float | None, float | None, float | None,
]


def _safe_float(val: object) -> float | None:
    """Return float, or None for None/NaN/non-numeric values."""
    if val is None:
        return None
    try:
        f = float(val)  # type: ignore[arg-type]
        return None if f != f else f  # NaN → None
    except (TypeError, ValueError):
        return None


def _fetch_price_sync(ticker: str) -> _PriceData:
    """Return price data including intraday and 52-week ranges.

    change_pct_24h is None when previous_close is unavailable.
    change_pct_1w is None when insufficient history is available.
    market_open is True only when market_state == "REGULAR"; crypto markets
    always report "REGULAR" so they are never shown as closed.
    Falls back to True when market_state is unavailable.
    """
    try:
        data = yf.Ticker(ticker)
        info = data.fast_info
        price = info.last_price
        if price is None or price != price:  # catches None and NaN
            raise PriceFetchError(f"No price returned for {ticker}")
        price = float(price)

        change_pct: float | None = None
        prev_close = info.previous_close
        if prev_close is not None and prev_close == prev_close and prev_close != 0:
            change_pct = (price - float(prev_close)) / float(prev_close) * 100.0

        change_pct_1w: float | None = None
        hist = data.history(period="5d")
        if not hist.empty and len(hist) >= 2:
            week_open = float(hist["Close"].iloc[0])
            if week_open != 0:
                change_pct_1w = (price - week_open) / week_open * 100.0

        day_high = _safe_float(getattr(info, "day_high", None))
        day_low = _safe_float(getattr(info, "day_low", None))
        week_52_high = _safe_float(getattr(info, "fifty_two_week_high", None))
        week_52_low = _safe_float(getattr(info, "fifty_two_week_low", None))

        ticker_info = data.info
        market_state: str | None = ticker_info.get("marketState")
        market_open = market_state == "REGULAR" if market_state is not None else True
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
        return price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low
    except PriceFetchError:
        raise
    except Exception as exc:
        raise PriceFetchError(f"Failed to fetch price for {ticker}: {exc}") from exc


async def fetch_price(ticker: str) -> _PriceData:
    """Fetch price, 24h %, 1W %, market state, currency, and price ranges for a ticker."""
    return await asyncio.to_thread(_fetch_price_sync, ticker)


async def _fetch_price_safe(
    ticker: str,
) -> tuple[str, float | None, float | None, float | None, bool, str, float | None, float | None, float | None, float | None]:
    """Fetch price for a single ticker; return Nones on failure instead of raising."""
    try:
        price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = await fetch_price(ticker)
        return ticker, price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low
    except PriceFetchError as exc:
        log.warning("price_fetch_skipped", ticker=ticker, error=str(exc))
        return ticker, None, None, None, True, "USD", None, None, None, None


async def fetch_all_prices(tickers: list[str]) -> dict[str, _PriceData]:
    """Fetch prices for all tickers concurrently.

    Tickers that fail are logged and omitted from the result rather than
    aborting the entire fetch.
    Returns a dict mapping ticker -> _PriceData.
    """
    async with asyncio.TaskGroup() as tg:
        tasks = {t: tg.create_task(_fetch_price_safe(t)) for t in tickers}

    result: dict[str, _PriceData] = {}
    for ticker, task in tasks.items():
        _ticker, price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = task.result()
        if price is not None:
            result[ticker] = (price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low)
    return result


def _fetch_fx_rate_sync(currency: str) -> float:
    """Fetch the current {currency}->BRL exchange rate."""
    pair = f"{currency}BRL=X"
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
        log.warning("fx_rate_fetch_failed", currency=currency, error=str(exc))
        return currency, 1.0


async def fetch_fx_rates(currencies: list[str]) -> dict[str, float]:
    """Fetch BRL conversion rates for all given currencies concurrently.

    BRL maps to 1.0 without a network call. Failed fetches fall back to 1.0
    and are logged as warnings.
    """
    result: dict[str, float] = {"BRL": 1.0}
    non_brl = [c for c in currencies if c != "BRL"]
    if not non_brl:
        return result
    async with asyncio.TaskGroup() as tg:
        tasks = {c: tg.create_task(_fetch_fx_rate_safe(c)) for c in non_brl}
    for task in tasks.values():
        currency, rate = task.result()
        result[currency] = rate
    return result

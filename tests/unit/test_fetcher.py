from unittest.mock import patch

import pytest

from portfolio.core.exceptions import PriceFetchError
from portfolio.core.fetcher import _PriceData, fetch_all_prices, fetch_price


def _price_data(price: float, currency: str = "USD") -> _PriceData:
    """Build a realistic 11-field price tuple, the shape `fetch_price` returns.

    Order: (price, change 24h / 1w / 6m / 12m, market_open, currency,
    day_high, day_low, week_52_high, week_52_low).
    """
    return (
        price, 1.5, -2.0, 10.0, 25.0, True, currency,
        price * 1.01, price * 0.99, price * 1.3, price * 0.7,
    )


async def test_fetch_price_success() -> None:
    data = _price_data(150.0)
    with patch("portfolio.core.fetcher._fetch_price_sync", return_value=data):
        result = await fetch_price("AAPL")
    assert result == data
    assert result[0] == 150.0  # price is the first field


async def test_fetch_price_raises_on_failure() -> None:
    with patch(
        "portfolio.core.fetcher._fetch_price_sync",
        side_effect=PriceFetchError("no price"),
    ), pytest.raises(PriceFetchError):
        await fetch_price("INVALID")


async def test_fetch_all_prices() -> None:
    prices = {"AAPL": _price_data(150.0), "PETR4.SA": _price_data(38.2, "BRL")}

    async def fake_fetch(ticker: str) -> _PriceData:
        return prices[ticker]

    with patch("portfolio.core.fetcher.fetch_price", side_effect=fake_fetch):
        result = await fetch_all_prices(["AAPL", "PETR4.SA"])

    assert result == prices


async def test_fetch_all_prices_skips_failed_ticker() -> None:
    # One bad ticker must not sink the whole refresh: it is logged and left
    # out of the result, while the others still come back.
    async def fake_fetch(ticker: str) -> _PriceData:
        if ticker == "NOPE":
            raise PriceFetchError("no price")
        return _price_data(150.0)

    with patch("portfolio.core.fetcher.fetch_price", side_effect=fake_fetch):
        result = await fetch_all_prices(["AAPL", "NOPE"])

    assert result == {"AAPL": _price_data(150.0)}

from unittest.mock import patch

import pytest

from portfolio.core.exceptions import PriceFetchError
from portfolio.core.fetcher import fetch_all_prices, fetch_price


async def test_fetch_price_success() -> None:
    with patch("portfolio.core.fetcher._fetch_price_sync", return_value=150.0):
        price = await fetch_price("AAPL")
    assert price == 150.0


async def test_fetch_price_raises_on_failure() -> None:
    with patch(
        "portfolio.core.fetcher._fetch_price_sync",
        side_effect=PriceFetchError("no price"),
    ), pytest.raises(PriceFetchError):
        await fetch_price("INVALID")


async def test_fetch_all_prices() -> None:
    prices = {"AAPL": 150.0, "GOOG": 2800.0}

    async def fake_fetch(ticker: str) -> float:
        return prices[ticker]

    with patch("portfolio.core.fetcher.fetch_price", side_effect=fake_fetch):
        result = await fetch_all_prices(["AAPL", "GOOG"])

    assert result == prices

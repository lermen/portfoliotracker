from datetime import UTC

import pytest
from pydantic import ValidationError

from portfolio.core.models import PortfolioSnapshot, Position, PositionValue


def test_position_valid() -> None:
    pos = Position(ticker="AAPL", quantity=10.0)
    assert pos.ticker == "AAPL"
    assert pos.quantity == 10.0
    assert pos.exchange == ""
    assert pos.category == ""


def test_position_with_exchange_and_category() -> None:
    pos = Position(ticker="PETR4.SA", quantity=100.0, exchange="B3", category="Stock")
    assert pos.exchange == "B3"
    assert pos.category == "Stock"


def test_position_quantity_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Position(ticker="AAPL", quantity=0)


def test_portfolio_snapshot_total() -> None:
    from datetime import datetime

    pv = PositionValue(
        ticker="AAPL", quantity=10, price=150.0, value=1500.0,
        value_brl=8250.0,  # 1500 USD at an FX rate of 5.5
        exchange="NASDAQ", category="Stock",
    )
    snap = PortfolioSnapshot(
        positions=[pv],
        total_value=8250.0,  # totals are summed in BRL (value_brl)
        timestamp=datetime.now(UTC),
    )
    assert snap.total_value == 8250.0
    assert snap.currency == "BRL"  # snapshots always report in BRL
    assert snap.fixed_income == []  # defaults to an empty list, not None

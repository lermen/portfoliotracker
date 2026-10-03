# Unit tests for core/totals.py — the pure aggregation math shared by the
# status bar and the Summary tab. No Textual app needed: plain data in, numbers out.

import pytest

from portfolio.core.models import PositionValue
from portfolio.core.totals import cost_and_value, total_pnl_pct, value_before


def _pv(ticker: str, value_brl: float, pnl_pct: float | None) -> PositionValue:
    return PositionValue(
        ticker=ticker, quantity=1, price=value_brl, value_brl=value_brl, pnl_pct=pnl_pct
    )


def test_value_before_reverses_a_change() -> None:
    # 1000 after a +25% move was 800 before it.
    assert value_before(1000.0, 25.0) == pytest.approx(800.0)


def test_value_before_missing_change_returns_value() -> None:
    assert value_before(1000.0, None) == 1000.0


def test_value_before_minus_100_does_not_divide_by_zero() -> None:
    assert value_before(0.0, -100.0) == 0.0


def test_cost_and_value_sums_positions_with_known_cost() -> None:
    positions = [_pv("A", 1000.0, 25.0), _pv("B", 500.0, -50.0)]
    cost, value = cost_and_value(positions)
    assert cost == pytest.approx(800.0 + 1000.0)
    assert value == pytest.approx(1500.0)


def test_cost_and_value_skips_unknown_and_zero_price_positions() -> None:
    positions = [
        _pv("A", 1000.0, 25.0),
        _pv("NOAVG", 700.0, None),   # no avg price → skipped
        _pv("ZERO", 0.0, -100.0),    # zero live price → skipped
    ]
    assert cost_and_value(positions) == pytest.approx((800.0, 1000.0))


def test_total_pnl_pct() -> None:
    positions = [_pv("A", 1000.0, 25.0), _pv("B", 500.0, -50.0)]
    # cost 1800, value 1500 → -16.67%
    assert total_pnl_pct(positions) == pytest.approx(-300.0 / 1800.0 * 100.0)


def test_total_pnl_pct_none_without_cost_basis() -> None:
    assert total_pnl_pct([_pv("NOAVG", 700.0, None)]) is None
    assert total_pnl_pct([]) is None

# Tests for the P&L bar chart in the Chart tab.
#
# `_fit_rows` is a pure function (no Textual involved), so it is tested
# directly. The widget itself is tested through the real app with Textual's
# `run_test()` Pilot, the same way test_render_zero_price.py does it.

from datetime import UTC, datetime

from textual.widgets import TabbedContent

from portfolio.core.models import PortfolioSnapshot, PositionValue
from portfolio.ui.tui.app import PortfolioApp
from portfolio.ui.tui.widgets import PnLBarChart, _fit_rows


def test_fit_rows_returns_everything_when_it_fits() -> None:
    bars = [("A", 10.0), ("B", -5.0)]
    assert _fit_rows(bars, 5) == bars


def test_fit_rows_keeps_both_ends_when_too_many() -> None:
    bars = [(str(i), float(10 - i)) for i in range(10)]   # "0" best … "9" worst
    rows = _fit_rows(bars, 5)
    # 5 rows = 2 winners + gap marker + 2 losers; 6 bars skipped in the middle.
    assert rows == [("0", 10.0), ("1", 9.0), 6, ("8", 2.0), ("9", 1.0)]


def _position(ticker: str, pnl: float | None, category: str) -> PositionValue:
    return PositionValue(
        ticker=ticker, quantity=1, price=10.0, value_brl=10.0,
        pnl_pct=pnl, exchange="B3", category=category,
    )


async def test_chart_follows_category_filter_and_counts_missing() -> None:
    snapshot = PortfolioSnapshot(
        positions=[
            _position("WIN", 25.0, "Stock"),
            _position("LOSE", -10.0, "Stock"),
            _position("NOAVG", None, "Stock"),
            _position("BTC-USD", 50.0, "Crypto"),
        ],
        total_value=40.0,
        timestamp=datetime.now(UTC),
    )
    app = PortfolioApp()
    async with app.run_test(size=(160, 40)) as pilot:
        app.snapshot = snapshot
        await pilot.pause()
        chart = app.query_one(PnLBarChart)

        # Sorted best first; the position without an avg price is left out
        # and counted in the border subtitle instead.
        assert [t for t, _ in chart._bars] == ["BTC-USD", "WIN", "LOSE"]
        assert chart.border_subtitle == "1 without avg price"

        # Filtering the table to Stock filters the chart too.
        app.filter_category = "Stock"
        await pilot.pause()
        assert [t for t, _ in chart._bars] == ["WIN", "LOSE"]

        # The chart only has a size once its tab is on screen.
        app.query_one(TabbedContent).active = "tab-chart"
        await pilot.pause()

        # Rendering a gain row and a loss row doesn't crash and shows the value.
        assert "+25.00%" in chart.render_line(0).text
        assert "-10.00%" in chart.render_line(1).text

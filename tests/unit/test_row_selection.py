# Pilot tests for multi-row selection in the Portfolio tab.
#
# Space toggles the row under the cursor, X clears the selection, and the status
# bar totals only the selected rows. The key rule: with NO rows selected, the
# status bar must read exactly as it did before the feature existed.

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from portfolio.core.models import FixedIncomePosition, PortfolioSnapshot, PositionValue
from portfolio.ui.tui import app as app_module
from portfolio.ui.tui.app import PortfolioApp
from portfolio.ui.tui.widgets import PortfolioTable

# A has the higher P&L, so with the default "pnl" sort it is row 0 and B is row 1.
_A = PositionValue(
    ticker="AAA.SA", quantity=10, price=100.0, value_brl=1000.0,
    avg_price_native=80.0, pnl_pct=25.0, change_pct=25.0,
    exchange="B3", category="Stock",
)
_B = PositionValue(
    ticker="BBB.SA", quantity=10, price=50.0, value_brl=500.0,
    avg_price_native=100.0, pnl_pct=-50.0,
    exchange="B3", category="Fund",
)

# Status bar with nothing selected: variable 1500 + fixed income 2000 = 3500;
# P&L = (1500 − 1800) / 1800; 24h prev = 800 + 500 = 1300 → +200 (+15.38%).
_UNSELECTED_BAR = "Total: R$3,500.00  |  P&L: -16.67%  |  24h: +R$200.00 (+15.38%)"


def _snapshot(*positions: PositionValue) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=list(positions),
        total_value=sum(pv.value_brl for pv in positions),
        fixed_income=[FixedIncomePosition(name="CDB", amount_brl=2000.0)],
        fixed_income_total=2000.0,
        timestamp=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _no_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the real engines (Excel + network) from overwriting our test snapshot."""

    async def _idle(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()   # never set → waits until the app exits

    monkeypatch.setattr(app_module, "run_engine", _idle)
    monkeypatch.setattr(app_module, "run_bitcoin_engine", _idle)


def _bar(app: PortfolioApp) -> str:
    return str(app.query_one("#total").render())


async def test_no_selection_keeps_original_status_bar() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        app.hide_values = False
        app.snapshot = _snapshot(_A, _B)
        await pilot.pause()
        assert _bar(app).startswith(_UNSELECTED_BAR)
        assert not app.query_one("#total").has_class("selection")
        # Ticker cells are plain strings, unchanged from before the feature.
        table = app.query_one(PortfolioTable)
        assert table.get_cell_at((0, 0)) == "AAA.SA"


async def test_space_selects_and_totals_only_selected_rows() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        app.hide_values = False
        app.snapshot = _snapshot(_A, _B)
        await pilot.pause()

        await pilot.press("space")   # cursor starts on row 0 → AAA.SA
        await pilot.pause()
        assert app.selected_tickers == {"AAA.SA"}
        assert _bar(app).startswith(
            "Selected 1/2  |  Total: R$1,000.00  |  P&L: +25.00%"
            "  |  24h: +R$200.00 (+25.00%)"
        )
        assert app.query_one("#total").has_class("selection")
        table = app.query_one(PortfolioTable)
        assert str(table.get_cell_at((0, 0))) == "● AAA.SA"
        assert table.get_cell_at((1, 0)) == "  BBB.SA"

        # Add B as well: both rows selected → variable total only (no fixed income).
        table.move_cursor(row=1)
        await pilot.press("space")
        await pilot.pause()
        assert _bar(app).startswith(
            "Selected 2/2  |  Total: R$1,500.00  |  P&L: -16.67%"
        )

        # Space again on B deselects it.
        await pilot.press("space")
        await pilot.pause()
        assert app.selected_tickers == {"AAA.SA"}


async def test_clearing_selection_restores_original_status_bar() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        app.hide_values = False
        app.snapshot = _snapshot(_A, _B)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert app.selected_tickers == frozenset()
        assert _bar(app).startswith(_UNSELECTED_BAR)
        assert not app.query_one("#total").has_class("selection")
        assert app.query_one(PortfolioTable).get_cell_at((0, 0)) == "AAA.SA"


async def test_selection_hidden_by_filter_is_kept_but_not_totalled() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        app.hide_values = False
        app.snapshot = _snapshot(_A, _B)
        await pilot.pause()
        await pilot.press("space")   # select AAA.SA (category Stock)
        app.filter_category = "Fund"  # hides AAA.SA
        await pilot.pause()
        # Nothing visible is selected → normal filtered bar (B only, no fixed income).
        assert _bar(app).startswith("Total: R$500.00")
        assert app.selected_tickers == {"AAA.SA"}   # remembered for later

        app.filter_category = "ALL"
        await pilot.pause()
        assert _bar(app).startswith("Selected 1/2  |  Total: R$1,000.00")


async def test_removed_position_is_dropped_from_selection() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        app.hide_values = False
        app.snapshot = _snapshot(_A, _B)
        await pilot.pause()
        await pilot.press("space")   # select AAA.SA
        await pilot.pause()
        app.snapshot = _snapshot(_B)   # AAA.SA deleted from the spreadsheet
        await pilot.pause()
        assert app.selected_tickers == frozenset()
        assert _bar(app).startswith("Total: R$2,500.00")


async def test_expanding_a_row_keeps_sort_and_selection() -> None:
    # C has the best P&L, so a P&L sort would put it first; a name sort puts it last.
    c = _A.model_copy(update={"ticker": "CCC.SA", "pnl_pct": 90.0})
    app = PortfolioApp()
    async with app.run_test() as pilot:
        app.hide_values = False
        app.sort_key = "name"
        app.snapshot = _snapshot(_A, _B, c)
        await pilot.pause()
        table = app.query_one(PortfolioTable)
        table.focus()
        await pilot.press("space")   # select AAA.SA (row 0 in name order)
        await pilot.press("enter")   # expand AAA.SA
        await pilot.pause()

        def tickers() -> list[str]:
            return [str(table.get_cell_at((r, 0))) for r in range(table.row_count)]

        # Still in name order, with the two detail rows under AAA.SA.
        assert tickers()[0] == "● AAA.SA"
        assert "Day range" in tickers()[1]
        assert tickers()[3:] == ["  BBB.SA", "  CCC.SA"]
        assert table.cursor_row == 0
        assert app.selected_tickers == {"AAA.SA"}

        await pilot.press("escape")  # collapse — order must still be by name
        await pilot.pause()
        assert tickers() == ["● AAA.SA", "  BBB.SA", "  CCC.SA"]
        assert table.cursor_row == 0

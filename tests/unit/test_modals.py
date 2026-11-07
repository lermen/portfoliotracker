# Tests for the edit dialogs in ui/tui/modals.py.
#
# Textual ships a headless test driver: `App.run_test()` starts the app without a
# real terminal and returns a `Pilot` you can use to press keys and pump events.
# We mount each modal inside a tiny host `App`, drive it, and capture the value it
# passes to `dismiss()` via the `push_screen` callback.

from textual.app import App, ComposeResult
from textual.widgets import Input, Label

from portfolio.core.models import FixedIncomePosition, Position
from portfolio.ui.tui.modals import (
    AddPositionModal,
    AvgPriceModal,
    ConfirmModal,
    FixedIncomeModal,
    QuantityModal,
)


class _Host(App[None]):
    """A minimal app whose only job is to host one modal during a test."""

    def compose(self) -> ComposeResult:
        # A modal needs a base screen to sit on top of; a Label is enough.
        yield Label("host")


async def test_quantity_modal_returns_entered_value() -> None:
    result: list[float | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(QuantityModal("ITSA4.SA", 100.0), result.append)
        await pilot.pause()
        # Replace the pre-filled value, then Enter to submit.
        pilot.app.screen.query_one("#qty-input", Input).value = "9105"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [9105.0]


async def test_quantity_modal_escape_cancels() -> None:
    result: list[float | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(QuantityModal("ITSA4.SA", 100.0), result.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]


async def test_quantity_modal_rejects_non_positive() -> None:
    result: list[float | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(QuantityModal("ITSA4.SA", 100.0), result.append)
        await pilot.pause()
        pilot.app.screen.query_one("#qty-input", Input).value = "0"
        await pilot.press("enter")
        await pilot.pause()
        # Still open (nothing dismissed) because 0 is invalid.
        assert result == []
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]


async def test_avg_price_modal_returns_entered_value() -> None:
    result: list[float | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(AvgPriceModal("ITSA4.SA", 7.84), result.append)
        await pilot.pause()
        pilot.app.screen.query_one("#avg-input", Input).value = "6.50"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [6.50]


async def test_avg_price_modal_handles_no_current_price() -> None:
    # A position with no average price yet: the field starts empty and the user
    # can type a value.
    result: list[float | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(AvgPriceModal("MGLU3.SA", None), result.append)
        await pilot.pause()
        assert pilot.app.screen.query_one("#avg-input", Input).value == ""
        pilot.app.screen.query_one("#avg-input", Input).value = "12.30"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [12.30]


async def test_add_position_modal_builds_position() -> None:
    result: list[Position | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(AddPositionModal(), result.append)
        await pilot.pause()
        pilot.app.screen.query_one("#in-ticker", Input).value = "PETR4.SA"
        pilot.app.screen.query_one("#in-quantity", Input).value = "50"
        pilot.app.screen.query_one("#in-category", Input).value = "Stock"
        # Click the Add button.
        await pilot.click("#add")
        await pilot.pause()
    assert len(result) == 1
    pos = result[0]
    assert pos is not None
    assert pos.ticker == "PETR4.SA"
    assert pos.quantity == 50
    assert pos.category == "Stock"
    assert pos.avg_price_native is None


async def test_confirm_modal_yes_no() -> None:
    yes: list[bool] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(ConfirmModal("Delete X?"), yes.append)
        await pilot.pause()
        await pilot.click("#confirm")
        await pilot.pause()
    assert yes == [True]

    no: list[bool] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(ConfirmModal("Delete X?"), no.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert no == [False]


async def test_fixed_income_modal_add() -> None:
    result: list[FixedIncomePosition | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(FixedIncomeModal(), result.append)
        await pilot.pause()
        assert pilot.app.screen.query_one("#fi-name", Input).value == ""
        pilot.app.screen.query_one("#fi-name", Input).value = "  LCI Banco X  "
        pilot.app.screen.query_one("#fi-amount", Input).value = "15000"
        await pilot.click("#save")
        await pilot.pause()
    assert result == [FixedIncomePosition(name="LCI Banco X", amount_brl=15000)]


async def test_fixed_income_modal_edit_prefills() -> None:
    result: list[FixedIncomePosition | None] = []
    current = FixedIncomePosition(name="CDB Itaú", amount_brl=604000)
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(FixedIncomeModal(current), result.append)
        await pilot.pause()
        assert pilot.app.screen.query_one("#fi-name", Input).value == "CDB Itaú"
        assert pilot.app.screen.query_one("#fi-amount", Input).value == "604000"
        pilot.app.screen.query_one("#fi-amount", Input).value = "610000"
        await pilot.press("enter")
        await pilot.pause()
    assert result == [FixedIncomePosition(name="CDB Itaú", amount_brl=610000)]


async def test_fixed_income_modal_rejects_blank_name_and_bad_amount() -> None:
    result: list[FixedIncomePosition | None] = []
    async with _Host().run_test() as pilot:
        pilot.app.push_screen(FixedIncomeModal(), result.append)
        await pilot.pause()
        pilot.app.screen.query_one("#fi-amount", Input).value = "100"
        await pilot.click("#save")  # blank name
        await pilot.pause()
        assert result == []
        pilot.app.screen.query_one("#fi-name", Input).value = "LCI"
        pilot.app.screen.query_one("#fi-amount", Input).value = "0"
        await pilot.click("#save")  # amount must be > 0
        await pilot.pause()
        assert result == []
        await pilot.press("escape")
        await pilot.pause()
    assert result == [None]

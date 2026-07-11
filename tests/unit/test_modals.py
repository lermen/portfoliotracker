# Tests for the edit dialogs in ui/tui/modals.py.
#
# Textual ships a headless test driver: `App.run_test()` starts the app without a
# real terminal and returns a `Pilot` you can use to press keys and pump events.
# We mount each modal inside a tiny host `App`, drive it, and capture the value it
# passes to `dismiss()` via the `push_screen` callback.

from textual.app import App, ComposeResult
from textual.widgets import Input, Label

from portfolio.core.models import Position
from portfolio.ui.tui.modals import AddPositionModal, ConfirmModal, QuantityModal


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

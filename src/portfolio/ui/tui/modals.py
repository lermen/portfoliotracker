# This module holds the pop-up dialogs (modal screens) used for editing the
# portfolio directly inside the TUI.
#
# What is a "modal screen"?
# -------------------------
# A Textual `Screen` is a full-screen layer of widgets. A `ModalScreen` is a
# screen that floats *on top* of the current one and blocks interaction with
# everything below it until it is dismissed — exactly like a dialog box in a
# desktop app. The app pushes one with `self.push_screen(SomeModal(), callback)`
# and gets the user's result back through the callback.
#
# Typed results
# -------------
# `ModalScreen[T]` declares that this dialog returns a value of type `T` when it
# closes. We call `self.dismiss(value)` to close the dialog and hand `value` back
# to the app. Dismissing with `None` conventionally means "the user cancelled".
#
# These widgets import only from `portfolio.core.models` (for the `Position`
# type) — never from the engine or fetcher — which keeps the UI/backend
# separation rule intact.

from pydantic import ValidationError
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from portfolio.core.models import Position


class QuantityModal(ModalScreen[float | None]):
    """Ask the user for a new quantity for one existing ticker.

    Returns the new quantity as a float, or None if cancelled.
    """

    # `BINDINGS` local to a screen only apply while that screen is on top. Here we
    # let Escape cancel the dialog, which feels natural for a pop-up.
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, ticker: str, current_quantity: float) -> None:
        super().__init__()
        self.ticker = ticker
        self.current_quantity = current_quantity

    def compose(self) -> ComposeResult:
        # Everything inside `#dialog` is the visible box; the surrounding modal
        # area (dimmed background) is styled in portfolio.tcss.
        with Vertical(id="dialog"):
            yield Label(f"Edit quantity — {self.ticker}", id="dialog-title")
            # `type="number"` makes the Input reject non-numeric keystrokes, so we
            # don't have to defend against letters. We pre-fill the current value.
            # `:g` formats the float without trailing zeros (e.g. 9105 not 9105.0).
            yield Input(
                value=f"{self.current_quantity:g}",
                placeholder="e.g. 1500",
                type="number",
                id="qty-input",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        # Put the cursor in the input immediately so the user can just type.
        self.query_one("#qty-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Fired when the user presses Enter inside the Input — treat it as "Save".
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def _save(self) -> None:
        """Validate the typed value and close the dialog with the result."""
        raw = self.query_one("#qty-input", Input).value.strip()
        try:
            quantity = float(raw)
        except ValueError:
            self.notify("Enter a valid number", severity="error")
            return
        if quantity <= 0:
            self.notify("Quantity must be greater than 0", severity="error")
            return
        self.dismiss(quantity)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddPositionModal(ModalScreen[Position | None]):
    """Collect the fields for a brand-new position.

    Returns a validated `Position`, or None if cancelled. Building the `Position`
    here means Pydantic validates the input (e.g. quantity > 0) before it ever
    reaches the writer — the same model the reader produces from the spreadsheet.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Add position", id="dialog-title")
            # One labelled input per Position field. Ticker and quantity are
            # required; exchange, category and avg price are optional.
            yield Label("Ticker (e.g. PETR4.SA)")
            yield Input(placeholder="TICKER.SA", id="in-ticker")
            yield Label("Quantity")
            yield Input(placeholder="e.g. 100", type="number", id="in-quantity")
            yield Label("Exchange (optional)")
            yield Input(placeholder="e.g. B3", id="in-exchange")
            yield Label("Category (optional)")
            yield Input(placeholder="e.g. Stock", id="in-category")
            yield Label("Avg price (optional)")
            yield Input(placeholder="e.g. 32.50", type="number", id="in-avgprice")
            with Horizontal(id="dialog-buttons"):
                yield Button("Add", variant="primary", id="add")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#in-ticker", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            self._add()
        else:
            self.dismiss(None)

    def _value(self, widget_id: str) -> str:
        """Small helper: read and trim the text of one input by id."""
        return self.query_one(f"#{widget_id}", Input).value.strip()

    def _add(self) -> None:
        ticker = self._value("in-ticker")
        if not ticker:
            self.notify("Ticker is required", severity="error")
            return

        avg_raw = self._value("in-avgprice")
        try:
            # Let Pydantic do the heavy validation. `float(...) if ... else None`
            # keeps avg price optional. If quantity is blank or invalid, or the
            # quantity is <= 0, Pydantic raises ValidationError which we catch.
            position = Position(
                ticker=ticker,
                quantity=float(self._value("in-quantity")),
                exchange=self._value("in-exchange"),
                category=self._value("in-category"),
                avg_price_native=float(avg_raw) if avg_raw else None,
            )
        except (ValidationError, ValueError):
            self.notify("Check the ticker and quantity (quantity must be > 0)", severity="error")
            return
        self.dismiss(position)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """A yes/no confirmation dialog. Returns True if confirmed, False otherwise."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.message, id="dialog-title")
            with Horizontal(id="dialog-buttons"):
                # `variant="error"` colours the destructive action red so it reads
                # as a warning, and we leave "Cancel" as the safe default focus.
                yield Button("Delete", variant="error", id="confirm")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)

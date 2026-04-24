import asyncio
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo  # standard library timezone support (Python 3.9+)

# Textual is a framework for building terminal UIs in Python.
# `App` is the base class for every Textual application.
# `ComposeResult` is the return type of the `compose` method.
from textual.app import App, ComposeResult

# `reactive` is a Textual concept: a special attribute that automatically
# triggers a `watch_<name>` method whenever its value changes.
# This is the "reactive / observer" pattern — no need to call `refresh()` manually.
from textual.reactive import reactive

# Built-in Textual widgets for common UI elements.
from textual.widgets import Footer, Header, Label, LoadingIndicator

from portfolio.core.engine import run_engine
from portfolio.core.models import PortfolioSnapshot, PositionValue
from portfolio.ui.tui.widgets import PieChart, PortfolioTable


class PortfolioApp(App[None]):
    # Point Textual at the CSS file that styles this app.
    # `Path(__file__).parent` gives the directory containing this file,
    # so the path is correct regardless of where you run the app from.
    CSS_PATH = Path(__file__).parent / "portfolio.tcss"
    TITLE = "Portfolio Tracker"

    # `BINDINGS` declares keyboard shortcuts. Each entry is (key, action, description).
    # `action_<name>` methods on the class are called when the key is pressed.
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("e", "cycle_exchange", "Exchange"),
        ("c", "cycle_category", "Category"),
        ("h", "toggle_hide", "Hide values"),
        ("p", "toggle_pie", "Pie chart"),
        ("escape", "collapse_row", "Collapse"),
    ]

    # Reactive attributes: Textual watches these for changes and calls
    # `watch_snapshot`, `watch_filter_exchange`, etc. automatically.
    # The type annotation `reactive[T]` tells mypy what type the value holds.
    snapshot: reactive[PortfolioSnapshot | None] = reactive(None)
    filter_exchange: reactive[str] = reactive("ALL")
    filter_category: reactive[str] = reactive("ALL")
    hide_values: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        # Always call `super().__init__()` when overriding `__init__` on a
        # subclass — it lets the parent class (App) set itself up properly.
        super().__init__()
        # The queue is the communication channel between the engine (producer)
        # and this UI (consumer). `asyncio.Queue[T]` is a generic type annotation.
        self.queue: asyncio.Queue[PortfolioSnapshot] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        """Declare the widget tree for this app.

        `compose` is called once at startup. It is a generator function:
        `yield` hands each widget to Textual, which mounts them in order.
        """
        yield Header()
        yield LoadingIndicator(id="loading")  # shown until the first snapshot arrives
        yield PortfolioTable(id="table")
        yield PieChart(id="pie")
        yield Label("Total: —", id="total")
        yield Footer()

    def on_mount(self) -> None:
        """Called by Textual once all widgets are mounted and ready.

        `run_worker` runs a coroutine as a background Textual worker.
        `exclusive=True` means only one instance of this worker runs at a time.
        This is where the engine loop starts — it runs concurrently with the UI.
        """
        self.query_one(PortfolioTable).display = False  # hidden until data arrives
        self.run_worker(run_engine(self.queue), exclusive=True)
        # `set_interval(1.0, fn)` calls `fn` every 1 second using Textual's scheduler.
        self.set_interval(1.0, self.poll_queue)

    async def poll_queue(self) -> None:
        """Check the queue for a new snapshot every second.

        `queue.empty()` is a non-blocking check. If there is data, we take one
        snapshot with `await queue.get()` and assign it to the reactive attribute.
        Assigning to `self.snapshot` automatically triggers `watch_snapshot`.
        """
        if not self.queue.empty():
            self.snapshot = await self.queue.get()

    # --- filter helpers ---

    def _exchanges(self) -> list[str]:
        """Build the list of available exchange filter options from the current snapshot."""
        if self.snapshot is None:
            return ["ALL"]
        # Set comprehension `{...}` deduplicates; `sorted()` puts them in order.
        values = sorted({pv.exchange for pv in self.snapshot.positions if pv.exchange})
        return ["ALL"] + values

    def _categories(self) -> list[str]:
        if self.snapshot is None:
            return ["ALL"]
        values = sorted({pv.category for pv in self.snapshot.positions if pv.category})
        return ["ALL"] + values

    def _filtered_positions(self) -> list[PositionValue]:
        """Return positions after applying the active exchange and category filters."""
        if self.snapshot is None:
            return []
        positions: list[PositionValue] = self.snapshot.positions
        # List comprehensions used as filters: keep only positions matching the active filter.
        if self.filter_exchange != "ALL":
            positions = [pv for pv in positions if pv.exchange == self.filter_exchange]
        if self.filter_category != "ALL":
            positions = [pv for pv in positions if pv.category == self.filter_category]
        return positions

    # --- actions ---
    # Action methods are named `action_<binding_name>`. Textual calls them when
    # the corresponding key is pressed (declared in BINDINGS above).

    def action_cycle_exchange(self) -> None:
        """Advance the exchange filter to the next option, wrapping around."""
        exchanges = self._exchanges()
        # `list.index(x)` returns the position of x in the list.
        idx = exchanges.index(self.filter_exchange) if self.filter_exchange in exchanges else 0
        # `% len(exchanges)` wraps around to 0 when we reach the end — modulo arithmetic.
        self.filter_exchange = exchanges[(idx + 1) % len(exchanges)]

    def action_cycle_category(self) -> None:
        categories = self._categories()
        idx = categories.index(self.filter_category) if self.filter_category in categories else 0
        self.filter_category = categories[(idx + 1) % len(categories)]

    def action_toggle_hide(self) -> None:
        # `not x` flips a boolean: True → False, False → True.
        self.hide_values = not self.hide_values

    def action_collapse_row(self) -> None:
        self.query_one(PortfolioTable).collapse()

    def action_toggle_pie(self) -> None:
        """Switch between the table view and the pie chart view."""
        table = self.query_one(PortfolioTable)
        pie = self.query_one(PieChart)
        if table.display:
            table.display = False
            pie.display = True
        else:
            pie.display = False
            table.display = True

    # --- rendering ---

    def _render(self) -> None:
        """Refresh all widgets to reflect the current snapshot and filter state."""
        if self.snapshot is None:
            return

        # Hide the loading spinner and show the table on the first render.
        loading = self.query_one("#loading", LoadingIndicator)
        if loading.display:
            loading.display = False
            self.query_one(PortfolioTable).display = True

        positions = self._filtered_positions()

        def _value_before(value_brl: float, change_pct: float | None) -> float:
            """Reverse-calculate the previous value from current value + % change."""
            if change_pct is None:
                return value_brl
            return value_brl / (1.0 + change_pct / 100.0)

        # Generator expressions inside `sum()` are memory-efficient — they don't
        # build a temporary list; they compute each value on the fly.
        total = sum(pv.value_brl for pv in positions)
        total_24h = sum(_value_before(pv.value_brl, pv.change_pct) for pv in positions)
        total_1w = sum(_value_before(pv.value_brl, pv.change_pct_1w) for pv in positions)

        # `model_copy(update={...})` creates a modified copy of a Pydantic model.
        # This is the immutable-update pattern: never mutate the original snapshot.
        filtered_snapshot = self.snapshot.model_copy(
            update={"positions": positions, "total_value": total}
        )
        self.query_one(PortfolioTable).update(filtered_snapshot, hide_values=self.hide_values)

        # `defaultdict(float)` is like a regular dict but returns 0.0 for missing keys,
        # which makes accumulation (+=) safe without an explicit `if key in dict` check.
        category_totals: dict[str, float] = defaultdict(float)
        for pv in positions:
            category_totals[pv.category or "Unknown"] += pv.value_brl
        self.query_one(PieChart).set_data(list(category_totals.items()), hide_values=self.hide_values)

        # Convert the UTC timestamp to the user's local timezone (BRT) for display.
        _BRT = ZoneInfo("America/Sao_Paulo")
        ts = self.snapshot.timestamp.astimezone(_BRT).strftime("%H:%M")  # e.g. "14:32"

        def _fmt_delta(current: float, previous: float) -> str:
            """Format the absolute and relative change between two portfolio values."""
            delta = current - previous
            sign = "+" if delta >= 0 else ""
            pct = (delta / previous * 100) if previous != 0 else 0.0
            if self.hide_values:
                return f"{sign}{pct:.2f}%"
            # f-string number formatting: `{val:,.2f}` → comma thousands separator, 2 decimal places.
            return f"{sign}R${delta:,.2f} ({sign}{pct:.2f}%)"

        # `sub_title` is a built-in Textual App attribute that appears in the header.
        self.sub_title = f"Exchange: {self.filter_exchange}  |  Category: {self.filter_category}"

        if self.hide_values:
            self.query_one("#total", Label).update(
                f"24h: {_fmt_delta(total, total_24h)}"
                f"  |  1W: {_fmt_delta(total, total_1w)}"
                f"  |  {ts} BRT"
            )
        else:
            self.query_one("#total", Label).update(
                f"Total: R${total:,.2f}"
                f"  |  24h: {_fmt_delta(total, total_24h)}"
                f"  |  1W: {_fmt_delta(total, total_1w)}"
                f"  |  {ts} BRT"
            )

    # --- reactive watchers ---
    # These methods follow the naming convention `watch_<reactive_attr_name>`.
    # Textual calls them automatically when the corresponding reactive changes.

    def watch_snapshot(self, snapshot: PortfolioSnapshot | None) -> None:
        if snapshot is None:
            return
        self._render()

    def watch_filter_exchange(self, _: str) -> None:
        # The `_` parameter name is a Python convention for "I'm not using this value".
        self._render()

    def watch_filter_category(self, _: str) -> None:
        self._render()

    def watch_hide_values(self, _: bool) -> None:
        self._render()

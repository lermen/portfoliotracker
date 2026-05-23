# This module is the entry point for the Textual TUI (Terminal User Interface).
#
# It defines `PortfolioApp`, the top-level Textual `App` subclass. A Textual `App`
# manages the entire terminal screen — its layout, event handling, keyboard bindings,
# and background tasks.
#
# Textual uses an event-driven, reactive programming model:
#   - "Reactive" variables automatically trigger re-renders when their value changes.
#   - "Workers" are background coroutines (async tasks) managed by Textual.
#   - "Actions" are methods triggered by keyboard shortcuts, defined in BINDINGS.
#   - "Watchers" (`watch_*` methods) are called automatically whenever a reactive
#     variable changes — they are the bridge between data and display.
#
# Data flow in this file:
#   [engine.py worker] → queue → poll_queue() → snapshot reactive → watch_snapshot() → _render()
#   [bitcoin_fetcher worker] → btc_queue → poll_btc_queue() → btc_snapshot reactive → update_metrics()

import asyncio
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo   # standard library for IANA timezone support (Python 3.9+)

from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Label,
    LoadingIndicator,
    TabbedContent,
    TabPane,
)

from portfolio.core.bitcoin_fetcher import run_bitcoin_engine
from portfolio.core.engine import run_engine
from portfolio.core.models import BitcoinMetrics, PortfolioSnapshot, PositionValue
from portfolio.ui.tui.widgets import (
    BitcoinMetricsPanel,
    FixedIncomeTable,
    PieChart,
    PortfolioTable,
    SummaryPanel,
)


class PortfolioApp(App[None]):
    """The root Textual application — owns the screen layout, workers, and state.

    `App[None]` means the app returns no result when it exits (the type parameter
    is the return type of `app.run()`).

    Key Textual concepts used here:
    --------------------------------
    CSS_PATH: Textual loads this .tcss file and applies the styles to the widget tree,
              just like a browser loading a CSS file for a web page.

    TITLE: shown in the Textual header bar at the top of the screen.

    BINDINGS: a list of (key, action, description) tuples. Textual listens for the
              key, calls `action_{action}()`, and shows the description in the Footer.
              For example, `("q", "quit", "Quit")` means pressing 'q' calls
              `action_quit()`, which is built into Textual's App base class.

    `reactive`: a descriptor that makes a variable "observable". When you write
                `self.snapshot = new_value`, Textual automatically calls any
                `watch_snapshot()` method in the class, which lets you update the
                UI in response to data changes without manually wiring callbacks.
    """

    CSS_PATH = Path(__file__).parent / "portfolio.tcss"
    TITLE = "Portfolio Tracker"

    # BINDINGS maps keyboard keys to action methods.
    # Format: (key, action_name, display_label)
    # The action_name must match an `action_*` method in this class, or a built-in
    # Textual action like "quit". The label appears in the Footer bar.
    # `"sort('pnl')"` calls `action_sort("pnl")` — Textual parses the argument automatically.
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("e", "cycle_exchange", "Exchange"),
        ("c", "cycle_category", "Category"),
        ("h", "toggle_hide", "Hide values"),
        ("escape", "collapse_row", "Collapse"),
        ("p", "sort('pnl')", "Sort P&L"),
        ("d", "sort('24h')", "Sort 24h"),
        ("w", "sort('1w')", "Sort 1W"),
        ("n", "sort('name')", "Sort Name"),
        ("v", "sort('value')", "Sort Value"),
    ]

    # `reactive[T]` declares a reactive variable of type T.
    # When the value changes, Textual automatically calls `watch_<name>(new_value)`.
    # The initial value (`None`) is used until the first snapshot arrives from the queue.
    snapshot: reactive[PortfolioSnapshot | None] = reactive(None)
    btc_snapshot: reactive[BitcoinMetrics | None] = reactive(None)

    # These control what is shown in the portfolio table. Changing them triggers
    # `watch_filter_exchange` and `watch_filter_category`, which call `_render()`.
    filter_exchange: reactive[str] = reactive("ALL")
    filter_category: reactive[str] = reactive("ALL")

    # `reactive[bool]` with initial value `True` — the app starts in privacy
    # mode so opening the TUI in a shared screen never reveals balances. The
    # user toggles it off with 'h' once they're ready to see the numbers.
    hide_values: reactive[bool] = reactive(True)

    sort_key: reactive[str] = reactive("pnl")   # which column to sort the portfolio table by

    def __init__(self) -> None:
        """Create the two asyncio queues used to receive data from backend workers.

        `super().__init__()` calls the parent class (`App`) constructor — always
        required when overriding `__init__` in a subclass, so the parent sets up
        its own internal state before we add ours.

        `asyncio.Queue[T]` is a type-annotated first-in-first-out (FIFO) channel.
        The engine puts items in; our polling methods take them out.
        """
        super().__init__()
        self.queue: asyncio.Queue[PortfolioSnapshot] = asyncio.Queue()
        self.btc_queue: asyncio.Queue[BitcoinMetrics] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        """Declare the widget tree — what the screen is made of and how it nests.

        `compose()` is called once by Textual when the app starts. It should
        `yield` widgets in the order they appear on screen (top to bottom).

        `with SomeContainer():` is a context manager that makes subsequently
        yielded widgets children of that container. This is how Textual builds
        its widget hierarchy — similar to nesting HTML elements.

        `ComposeResult` is just a type alias for `Iterable[Widget]`. The `yield`
        keyword makes `compose()` a *generator function* — it produces values
        lazily instead of building a list and returning it all at once.
        """
        yield Header()
        yield LoadingIndicator(id="loading")   # shown while waiting for the first snapshot

        # `TabbedContent` renders a tab bar and shows one `TabPane` at a time.
        # Each `TabPane` gets a label (shown in the tab) and an id (used to
        # query or activate it programmatically).
        with TabbedContent(id="tabs"):
            with TabPane("Summary", id="tab-summary"):
                yield SummaryPanel(id="summary")
            with TabPane("Portfolio", id="tab-portfolio"):
                yield PortfolioTable(id="table")
            with TabPane("Fixed Income", id="tab-fixed-income"):
                yield FixedIncomeTable(id="fi-table")
            with TabPane("Chart", id="tab-chart"):
                yield PieChart(id="pie")
            with TabPane("Bitcoin", id="tab-bitcoin"):
                yield BitcoinMetricsPanel(id="btc-panel")

        yield Label("Total: —", id="total")   # status bar showing the portfolio total
        yield Footer()                         # shows keyboard bindings from BINDINGS

    def on_mount(self) -> None:
        """Called once by Textual immediately after the widget tree is built.

        This is where we start background workers and set up polling timers.

        `self.run_worker(coroutine)` tells Textual to run the coroutine as a
        background task alongside the UI. The UI stays responsive while the
        worker fetches data. `exclusive=True` means only one instance of this
        worker can run at a time (useful to prevent duplicate workers).

        `self.set_interval(1.0, fn)` calls `fn()` every 1 second. This is how
        we "poll" the queue — we check if new data has arrived and consume it.
        """
        # Hide the tab container until the first snapshot arrives (shows spinner instead).
        self.query_one("#tabs", TabbedContent).display = False

        # Start the portfolio engine as a background worker.
        self.run_worker(run_engine(self.queue), exclusive=True)

        # Start the Bitcoin metrics engine as a separate background worker.
        # `exclusive=False` allows it to coexist with the portfolio engine worker.
        self.run_worker(run_bitcoin_engine(self.btc_queue), exclusive=False)

        # Poll both queues every second. Each call is cheap — it only does work
        # when data is actually available in the queue.
        self.set_interval(1.0, self.poll_queue)
        self.set_interval(1.0, self.poll_btc_queue)

    async def poll_queue(self) -> None:
        """Check the portfolio queue and consume a snapshot if one is waiting.

        `queue.empty()` is a non-blocking check. We only call `await queue.get()`
        (which would block if the queue were empty) after confirming data exists.
        Assigning to `self.snapshot` triggers `watch_snapshot()` automatically.
        """
        if not self.queue.empty():
            self.snapshot = await self.queue.get()

    async def poll_btc_queue(self) -> None:
        """Check the Bitcoin metrics queue and consume a snapshot if one is waiting."""
        if not self.btc_queue.empty():
            self.btc_snapshot = await self.btc_queue.get()

    # --- filter helpers ---

    def _exchanges(self) -> list[str]:
        """Return all unique exchange names in the current snapshot, plus 'ALL'."""
        if self.snapshot is None:
            return ["ALL"]
        # Set comprehension `{...}`: like a list comprehension but produces a set,
        # which automatically deduplicates values. Wrapped in `sorted()` for stable order.
        values = sorted({pv.exchange for pv in self.snapshot.positions if pv.exchange})
        return ["ALL"] + values   # "ALL" always comes first

    def _categories(self) -> list[str]:
        """Return all unique category names in the current snapshot, plus 'ALL'."""
        if self.snapshot is None:
            return ["ALL"]
        values = sorted({pv.category for pv in self.snapshot.positions if pv.category})
        return ["ALL"] + values

    def _filtered_positions(self) -> list[PositionValue]:
        """Return positions filtered by the currently active exchange and category.

        Filtering is applied to the display only — the underlying snapshot is
        never modified. `model_copy(update={...})` in `_render()` creates a
        filtered copy for widgets that need the full snapshot type.
        """
        if self.snapshot is None:
            return []
        positions: list[PositionValue] = self.snapshot.positions
        if self.filter_exchange != "ALL":
            # List comprehension with a condition: keep only positions where the
            # exchange matches the active filter.
            positions = [pv for pv in positions if pv.exchange == self.filter_exchange]
        if self.filter_category != "ALL":
            positions = [pv for pv in positions if pv.category == self.filter_category]
        return positions

    # --- actions ---
    # These methods are called by Textual when the matching key is pressed
    # (see BINDINGS above). The naming convention is `action_<name>`.

    def action_cycle_exchange(self) -> None:
        """Cycle the exchange filter to the next value in the list."""
        exchanges = self._exchanges()
        # Find the current filter's index, or 0 if it's no longer in the list
        # (can happen if the snapshot changes and the exchange disappears).
        idx = exchanges.index(self.filter_exchange) if self.filter_exchange in exchanges else 0
        # `% len(exchanges)` wraps around: after the last item, go back to index 0.
        self.filter_exchange = exchanges[(idx + 1) % len(exchanges)]

    def action_cycle_category(self) -> None:
        """Cycle the category filter to the next value in the list."""
        categories = self._categories()
        idx = categories.index(self.filter_category) if self.filter_category in categories else 0
        self.filter_category = categories[(idx + 1) % len(categories)]

    def action_toggle_hide(self) -> None:
        """Toggle privacy mode — replaces values with '-----' when enabled."""
        # `not self.hide_values` flips a boolean: True → False, False → True.
        self.hide_values = not self.hide_values

    def action_collapse_row(self) -> None:
        """Collapse any expanded detail row in the portfolio table."""
        self.query_one(PortfolioTable).collapse()

    def action_sort(self, key: str) -> None:
        """Change the sort order of the portfolio table."""
        self.sort_key = key

    # --- rendering ---

    def _render(self) -> None:
        """Push the current snapshot into every widget that needs it.

        This is called by all reactive watchers (`watch_snapshot`, `watch_filter_*`,
        etc.). It acts as a single "refresh all" function so we never have to
        remember which widgets depend on which reactive variables.
        """
        if self.snapshot is None:
            return

        # On the first snapshot, swap the loading spinner for the tab container.
        loading = self.query_one("#loading", LoadingIndicator)
        if loading.display:
            loading.display = False
            self.query_one("#tabs", TabbedContent).display = True

        positions = self._filtered_positions()

        def _value_before(value_brl: float, change_pct: float | None) -> float:
            """Back-calculate portfolio value before a given percentage change.

            If today's value = V and the change was p%, then:
                V_before = V / (1 + p/100)
            """
            if change_pct is None:
                return value_brl
            return value_brl / (1.0 + change_pct / 100.0)

        # Sum the BRL values of all currently visible (filtered) positions.
        var_total = sum(pv.value_brl for pv in positions)
        var_total_24h = sum(_value_before(pv.value_brl, pv.change_pct) for pv in positions)
        var_total_1w = sum(_value_before(pv.value_brl, pv.change_pct_1w) for pv in positions)
        var_total_6m = sum(_value_before(pv.value_brl, pv.change_pct_6m) for pv in positions)
        var_total_12m = sum(_value_before(pv.value_brl, pv.change_pct_12m) for pv in positions)

        fi_total = self.snapshot.fixed_income_total

        # `grand_total` is the full portfolio value (variable + fixed income) and is
        # used by the Fixed Income tab so each row's percentage is computed against
        # the whole portfolio.
        grand_total = var_total + fi_total

        # `displayed_total` is what the status bar shows. When a filter is active
        # (exchange or category != "ALL") fixed-income positions are excluded
        # because they have no exchange/category and therefore can't match the
        # filter — including them would contradict the user's selection.
        filter_active = self.filter_exchange != "ALL" or self.filter_category != "ALL"
        displayed_total = var_total if filter_active else grand_total

        # --- Summary tab ---
        # The summary always shows the unfiltered portfolio: it's the
        # "everything at a glance" view, so applying exchange/category filters
        # here would confuse the totals it displays. The Portfolio table below
        # is the only widget that respects the active filters.
        self.query_one(SummaryPanel).update(self.snapshot, hide_values=self.hide_values)

        # --- Portfolio tab ---
        # `model_copy(update={...})` creates a shallow copy of the snapshot with
        # specific fields replaced. This is a Pydantic feature that keeps the
        # original immutable while giving the widget a filtered view of the data.
        filtered_snapshot = self.snapshot.model_copy(
            update={"positions": positions}
        )
        self.query_one(PortfolioTable).update(filtered_snapshot, hide_values=self.hide_values, sort_key=self.sort_key)

        # --- Fixed Income tab ---
        self.query_one(FixedIncomeTable).update(
            self.snapshot.fixed_income,
            grand_total=grand_total,
            hide_values=self.hide_values,
        )

        # --- Chart tab: aggregate positions by category, add fixed income as one slice ---
        # `defaultdict(float)` is like a regular dict but automatically creates a
        # 0.0 entry for any key that doesn't exist yet, so `+= value` always works
        # without a KeyError even on first access.
        category_totals: dict[str, float] = defaultdict(float)
        for pv in positions:
            category_totals[pv.category or "Unknown"] += pv.value_brl
        if fi_total > 0:
            # Fixed income is always shown as one slice regardless of the active filter.
            category_totals["Fixed Income"] += fi_total
        self.query_one(PieChart).set_data(
            list(category_totals.items()), hide_values=self.hide_values
        )

        # --- Status bar ---
        # Convert the UTC timestamp to the BRT timezone for a local-friendly display.
        _BRT = ZoneInfo("America/Sao_Paulo")
        ts = self.snapshot.timestamp.astimezone(_BRT).strftime("%H:%M")

        _sort_labels = {"pnl": "P&L %", "24h": "24h %", "1w": "1W %", "name": "Name", "value": "Value"}
        self.sub_title = (
            f"Exchange: {self.filter_exchange}  |  Category: {self.filter_category}"
            f"  |  Sort: {_sort_labels.get(self.sort_key, self.sort_key)}"
        )

        def _fmt_delta(current: float, previous: float) -> str:
            """Format the absolute and percentage change between two values."""
            delta = current - previous
            sign = "+" if delta >= 0 else ""   # explicit '+' for gains, '-' is already in the number
            pct = (delta / previous * 100) if previous != 0 else 0.0
            if self.hide_values:
                return f"{sign}{pct:.2f}%"      # only show percentage in privacy mode
            return f"{sign}R${delta:,.2f} ({sign}{pct:.2f}%)"

        # Total unrealised P&L % across the currently visible positions.
        # We reconstruct each position's BRL cost basis from `value_brl` and `pnl_pct`:
        #   pnl_pct/100 = (value_brl − cost_brl) / cost_brl  ⇒  cost_brl = value_brl / (1 + pnl_pct/100)
        # This identity holds in both currency conventions used by the engine
        # (native-currency avg for stocks/ETFs, BRL avg for crypto), because the
        # engine always computes pnl_pct against a same-currency reference price.
        # Positions with no avg price (pnl_pct is None) are skipped — they don't
        # contribute to either side of the ratio.
        total_cost_brl = 0.0
        total_value_with_cost = 0.0
        for pv in positions:
            if pv.pnl_pct is None:
                continue
            cost = pv.value_brl / (1.0 + pv.pnl_pct / 100.0)
            total_cost_brl += cost
            total_value_with_cost += pv.value_brl
        total_pnl_pct: float | None = (
            (total_value_with_cost - total_cost_brl) / total_cost_brl * 100.0
            if total_cost_brl > 0
            else None
        )

        def _fmt_total_pnl(pnl: float | None) -> str:
            if pnl is None:
                return "P&L: N/A"
            sign = "+" if pnl >= 0 else ""
            return f"P&L: {sign}{pnl:.2f}%"

        if self.hide_values:
            self.query_one("#total", Label).update(
                f"{_fmt_total_pnl(total_pnl_pct)}"
                f"  |  24h: {_fmt_delta(var_total, var_total_24h)}"
                f"  |  1W: {_fmt_delta(var_total, var_total_1w)}"
                f"  |  6M: {_fmt_delta(var_total, var_total_6m)}"
                f"  |  12M: {_fmt_delta(var_total, var_total_12m)}"
                f"  |  {ts} BRT"
            )
        else:
            self.query_one("#total", Label).update(
                f"Total: R${displayed_total:,.2f}"
                f"  |  {_fmt_total_pnl(total_pnl_pct)}"
                f"  |  24h: {_fmt_delta(var_total, var_total_24h)}"
                f"  |  1W: {_fmt_delta(var_total, var_total_1w)}"
                f"  |  6M: {_fmt_delta(var_total, var_total_6m)}"
                f"  |  12M: {_fmt_delta(var_total, var_total_12m)}"
                f"  |  {ts} BRT"
            )

    # --- reactive watchers ---
    # Textual automatically calls a `watch_<name>(new_value)` method whenever the
    # reactive variable named `<name>` changes. This is the observer pattern:
    # instead of manually calling `_render()` every time we update state, we just
    # change the reactive variable and the framework handles the rest.

    def watch_snapshot(self, snapshot: PortfolioSnapshot | None) -> None:
        """Called automatically when `self.snapshot` is assigned a new value."""
        if snapshot is None:
            return
        self._render()

    def watch_filter_exchange(self, _: str) -> None:
        """Called when the exchange filter changes. The `_` parameter is the new value,
        ignored here because `_render()` reads `self.filter_exchange` directly."""
        self._render()

    def watch_filter_category(self, _: str) -> None:
        """Called when the category filter changes."""
        self._render()

    def watch_hide_values(self, _: bool) -> None:
        """Called when privacy mode is toggled."""
        self._render()

    def watch_sort_key(self, _: str) -> None:
        """Called when the sort key changes."""
        self._render()

    def watch_btc_snapshot(self, metrics: BitcoinMetrics | None) -> None:
        """Called when a new Bitcoin metrics snapshot arrives from the BTC queue.

        We update the Bitcoin panel directly here rather than going through
        `_render()`, because the portfolio table and chart don't depend on
        Bitcoin metrics.
        """
        if metrics is None:
            return
        self.query_one(BitcoinMetricsPanel).update_metrics(metrics)
        # The Summary tab shows a condensed Bitcoin strip; refresh it on the
        # same cadence as the dedicated Bitcoin tab so both stay in sync.
        self.query_one(SummaryPanel).update_bitcoin(metrics)

import asyncio
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from textual.app import App, ComposeResult
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, LoadingIndicator, TabbedContent, TabPane

from portfolio.core.engine import run_engine
from portfolio.core.models import PortfolioSnapshot, PositionValue
from portfolio.ui.tui.widgets import FixedIncomeTable, PieChart, PortfolioTable


class PortfolioApp(App[None]):
    CSS_PATH = Path(__file__).parent / "portfolio.tcss"
    TITLE = "Portfolio Tracker"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("e", "cycle_exchange", "Exchange"),
        ("c", "cycle_category", "Category"),
        ("h", "toggle_hide", "Hide values"),
        ("escape", "collapse_row", "Collapse"),
    ]

    snapshot: reactive[PortfolioSnapshot | None] = reactive(None)
    filter_exchange: reactive[str] = reactive("ALL")
    filter_category: reactive[str] = reactive("ALL")
    hide_values: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self.queue: asyncio.Queue[PortfolioSnapshot] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="loading")
        # `TabbedContent` renders a tab bar and shows one `TabPane` at a time.
        # Each `TabPane` gets a label (shown in the tab) and a CSS id (used to
        # query or activate it programmatically).
        with TabbedContent(id="tabs"):
            with TabPane("Portfolio", id="tab-portfolio"):
                yield PortfolioTable(id="table")
            with TabPane("Fixed Income", id="tab-fixed-income"):
                yield FixedIncomeTable(id="fi-table")
            with TabPane("Chart", id="tab-chart"):
                yield PieChart(id="pie")
        yield Label("Total: —", id="total")
        yield Footer()

    def on_mount(self) -> None:
        # Hide the whole tab container until the first snapshot arrives.
        self.query_one("#tabs", TabbedContent).display = False
        self.run_worker(run_engine(self.queue), exclusive=True)
        self.set_interval(1.0, self.poll_queue)

    async def poll_queue(self) -> None:
        if not self.queue.empty():
            self.snapshot = await self.queue.get()

    # --- filter helpers ---

    def _exchanges(self) -> list[str]:
        if self.snapshot is None:
            return ["ALL"]
        values = sorted({pv.exchange for pv in self.snapshot.positions if pv.exchange})
        return ["ALL"] + values

    def _categories(self) -> list[str]:
        if self.snapshot is None:
            return ["ALL"]
        values = sorted({pv.category for pv in self.snapshot.positions if pv.category})
        return ["ALL"] + values

    def _filtered_positions(self) -> list[PositionValue]:
        if self.snapshot is None:
            return []
        positions: list[PositionValue] = self.snapshot.positions
        if self.filter_exchange != "ALL":
            positions = [pv for pv in positions if pv.exchange == self.filter_exchange]
        if self.filter_category != "ALL":
            positions = [pv for pv in positions if pv.category == self.filter_category]
        return positions

    # --- actions ---

    def action_cycle_exchange(self) -> None:
        exchanges = self._exchanges()
        idx = exchanges.index(self.filter_exchange) if self.filter_exchange in exchanges else 0
        self.filter_exchange = exchanges[(idx + 1) % len(exchanges)]

    def action_cycle_category(self) -> None:
        categories = self._categories()
        idx = categories.index(self.filter_category) if self.filter_category in categories else 0
        self.filter_category = categories[(idx + 1) % len(categories)]

    def action_toggle_hide(self) -> None:
        self.hide_values = not self.hide_values

    def action_collapse_row(self) -> None:
        self.query_one(PortfolioTable).collapse()

    # --- rendering ---

    def _render(self) -> None:
        if self.snapshot is None:
            return

        # On the first snapshot, swap loading spinner for the tab container.
        loading = self.query_one("#loading", LoadingIndicator)
        if loading.display:
            loading.display = False
            self.query_one("#tabs", TabbedContent).display = True

        positions = self._filtered_positions()

        def _value_before(value_brl: float, change_pct: float | None) -> float:
            if change_pct is None:
                return value_brl
            return value_brl / (1.0 + change_pct / 100.0)

        # Variable-position totals (respects the active filter).
        var_total = sum(pv.value_brl for pv in positions)
        var_total_24h = sum(_value_before(pv.value_brl, pv.change_pct) for pv in positions)
        var_total_1w = sum(_value_before(pv.value_brl, pv.change_pct_1w) for pv in positions)

        fi_total = self.snapshot.fixed_income_total

        # Grand total always includes all fixed income regardless of active filter.
        grand_total = var_total + fi_total

        # --- Portfolio tab ---
        filtered_snapshot = self.snapshot.model_copy(
            update={"positions": positions, "total_value": var_total}
        )
        self.query_one(PortfolioTable).update(filtered_snapshot, hide_values=self.hide_values)

        # --- Fixed Income tab ---
        self.query_one(FixedIncomeTable).update(
            self.snapshot.fixed_income,
            grand_total=grand_total,
            hide_values=self.hide_values,
        )

        # --- Chart tab: variable positions by category + fixed income as a group ---
        category_totals: dict[str, float] = defaultdict(float)
        for pv in positions:
            category_totals[pv.category or "Unknown"] += pv.value_brl
        if fi_total > 0:
            # Fixed income is always shown as a single slice regardless of filter.
            category_totals["Fixed Income"] += fi_total
        self.query_one(PieChart).set_data(
            list(category_totals.items()), hide_values=self.hide_values
        )

        # --- Status bar ---
        _BRT = ZoneInfo("America/Sao_Paulo")
        ts = self.snapshot.timestamp.astimezone(_BRT).strftime("%H:%M")

        self.sub_title = f"Exchange: {self.filter_exchange}  |  Category: {self.filter_category}"

        def _fmt_delta(current: float, previous: float) -> str:
            delta = current - previous
            sign = "+" if delta >= 0 else ""
            pct = (delta / previous * 100) if previous != 0 else 0.0
            if self.hide_values:
                return f"{sign}{pct:.2f}%"
            return f"{sign}R${delta:,.2f} ({sign}{pct:.2f}%)"

        if self.hide_values:
            self.query_one("#total", Label).update(
                f"24h: {_fmt_delta(var_total, var_total_24h)}"
                f"  |  1W: {_fmt_delta(var_total, var_total_1w)}"
                f"  |  {ts} BRT"
            )
        else:
            self.query_one("#total", Label).update(
                f"Total: R${grand_total:,.2f}"
                f"  |  24h: {_fmt_delta(var_total, var_total_24h)}"
                f"  |  1W: {_fmt_delta(var_total, var_total_1w)}"
                f"  |  {ts} BRT"
            )

    # --- reactive watchers ---

    def watch_snapshot(self, snapshot: PortfolioSnapshot | None) -> None:
        if snapshot is None:
            return
        self._render()

    def watch_filter_exchange(self, _: str) -> None:
        self._render()

    def watch_filter_category(self, _: str) -> None:
        self._render()

    def watch_hide_values(self, _: bool) -> None:
        self._render()

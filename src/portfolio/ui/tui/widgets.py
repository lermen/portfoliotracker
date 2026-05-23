# This module contains all custom Textual widgets used in the TUI.
#
# A "widget" in Textual is a reusable UI component — the building block of the
# terminal interface. Textual ships with many built-in widgets (DataTable, Static,
# Header, Footer, etc.) and lets you build custom ones by subclassing `Widget`.
#
# Widgets in this file:
#   PieChart          — a custom rendered circular chart using Unicode block characters
#   MetricCard        — a bordered card showing one metric (title + value + subtitle)
#   BitcoinMetricsPanel — a panel composing multiple MetricCards for the Bitcoin tab
#   PortfolioTable    — a DataTable subclass that knows how to render a PortfolioSnapshot
#   FixedIncomeTable  — a DataTable subclass for the fixed-income sheet
#
# None of these widgets import anything from `portfolio.core.engine` or any other
# backend module. They only consume model objects (`PortfolioSnapshot`, etc.) that
# are handed to them via method calls from `app.py`. This is the UI/backend
# separation rule enforced throughout the project.

import math
from collections import defaultdict
from typing import Any

# Rich library types for terminal styling.
from rich.segment import Segment  # a piece of text with an optional style
from rich.style import Style  # foreground/background color, bold, italic, etc.
from rich.text import Text  # a string that carries per-character styles

# `RenderableType` is Rich's union type for anything that can be rendered in a
# terminal: a plain string, a `Text` object, a table, etc.
from textual.app import ComposeResult, RenderableType

# `Horizontal` is a container that lays out its children side by side.
# `Vertical` stacks its children top-to-bottom, mirroring the default Screen layout.
from textual.containers import Horizontal, Vertical

# `Strip` is a low-level concept in Textual: a single rendered line of the terminal,
# made up of styled `Segment` objects.
from textual.strip import Strip

# `Widget` is the base class for all Textual widgets. You subclass it to build
# custom widgets with their own rendering logic.
from textual.widget import Widget

# `DataTable` is a built-in Textual widget that displays rows and columns with
# keyboard navigation (arrow keys, cursor highlighting, selection).
from textual.widgets import DataTable, Static

from portfolio.core.models import (
    BitcoinMetrics,
    FixedIncomePosition,
    PortfolioSnapshot,
    PositionValue,
)

# Tuple of column header strings — used both here and when rebuilding the table.
COLUMNS = ("Ticker", "Exchange", "Category", "Quantity", "Price", "Avg Price", "Value (R$)", "% Portfolio", "P&L %", "24h %", "1W %", "6M %", "12M %")

# A module-level constant dict mapping currency codes to display symbols.
# `dict[str, str]` is a generic type annotation: keys and values are both strings.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "BRL": "R$",
    "GBP": "£",
}


def _fmt_exchange(exchange: str, market_open: bool) -> RenderableType:
    """Return a colored Text for the exchange: green if open, red if closed."""
    if market_open:
        return Text(exchange, style="green")
    # "(c)" is short for "closed" — shown when the exchange is not in its regular session.
    return Text(f"{exchange} (c)", style="red")


def _fmt_price(price: float, currency: str) -> str:
    # `dict.get(key, default)` — use the currency symbol if we have it,
    # otherwise fall back to the currency code followed by a space (e.g. "CHF 1.23").
    symbol = _CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    # `:,.2f` format spec: comma as thousands separator, 2 decimal places.
    return f"{symbol}{price:,.2f}"


def _fmt_quantity(quantity: float, category: str) -> str:
    """Format quantity with more decimal places for crypto assets."""
    if category.lower() == "crypto":
        # Crypto amounts can be fractional, so show 8 decimals if not a whole number.
        # `quantity % 1` is the fractional part: 0 if it is a whole number.
        return f"{quantity:,.8f}" if quantity % 1 else f"{quantity:,.0f}"
    return f"{quantity:,.2f}" if quantity % 1 else f"{quantity:,.0f}"


def _fmt_change(change_pct: float | None) -> RenderableType:
    """Format a percentage change with color (green/red) and a + sign for gains."""
    if change_pct is None:
        return Text("N/A", style="dim")
    sign = "+" if change_pct >= 0 else ""
    color = "green" if change_pct >= 0 else "red"
    return Text(f"{sign}{change_pct:.2f}%", style=color)


def _fmt_pnl(pnl_pct: float | None) -> RenderableType:
    """Format unrealised P&L %; show '----' when no average price is available."""
    if pnl_pct is None:
        return Text("----", style="dim")
    sign = "+" if pnl_pct >= 0 else ""
    color = "green" if pnl_pct >= 0 else "red"
    return Text(f"{sign}{pnl_pct:.2f}%", style=color)


# Okabe-Ito palette — colorblind-safe for deuteranopia, protanopia, tritanopia.
# A module-level list of hex color strings used to color pie chart slices.
_PALETTE = [
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#009E73",  # bluish green
    "#F0E442",  # yellow
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#95D0FC",  # light blue (extra)
]

_MAX_SLICES = 12  # cap before grouping into "Other"


class PieChart(Widget):
    """Circular pie chart widget rendered with Unicode block characters.

    Textual renders the terminal line-by-line. This widget overrides
    `render_line(y)` to compute, for each row `y`, which terminal cells
    belong to the pie and which belong to the legend.
    """

    # `DEFAULT_CSS` is inlined TCSS (Textual CSS) applied to this widget type.
    # It can be overridden by the app's external .tcss file.
    DEFAULT_CSS = """
    PieChart {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._slices: list[tuple[str, float]] = []  # (label, value) pairs
        self._hide_values: bool = False
        self._max_label_len: int = 10  # updated in set_data to fit the longest label

    def set_data(self, slices: list[tuple[str, float]], hide_values: bool = False) -> None:
        """Set (label, value) pairs; groups tail items as 'Other'.

        `sorted(..., key=lambda x: x[1], reverse=True)` sorts by the second
        element of each tuple (the value) in descending order.
        `lambda` defines an anonymous one-line function inline.
        """
        sorted_slices = sorted(slices, key=lambda x: x[1], reverse=True)
        if len(sorted_slices) > _MAX_SLICES:
            top = sorted_slices[:_MAX_SLICES]          # slice: first N items
            other_val = sum(v for _, v in sorted_slices[_MAX_SLICES:])  # sum of the rest
            top.append(("Other", other_val))
            self._slices = top
        else:
            self._slices = sorted_slices
        self._hide_values = hide_values
        # Compute once so render_line can align all rows to the same column widths.
        self._max_label_len = max((len(label) for label, _ in self._slices), default=10)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        """Render a single terminal row `y` as a Strip of colored Segments.

        This is a low-level Textual API. The framework calls `render_line`
        for each visible row of the widget and assembles the results into the
        full widget display.
        """
        w = self.size.width
        h = self.size.height

        if not self._slices or h == 0 or w == 0:
            return Strip.blank(w)  # empty row

        total = sum(v for _, v in self._slices)
        if total == 0:
            return Strip.blank(w)

        # Split the widget width: left portion is the circle, right is the legend.
        chart_w = max(1, int(w * 0.55))
        legend_w = w - chart_w

        # Circle geometry — compensate for ~2:1 char height:width aspect ratio.
        # Terminal characters are roughly twice as tall as they are wide, so we
        # stretch the horizontal radius (`rx`) to make the circle look round.
        cx = chart_w / 2.0   # center x
        cy = h / 2.0          # center y
        ry = min(chart_w / 4.0, h / 2.0) * 0.88   # vertical radius
        rx = ry * 2.0                               # horizontal radius (2× to compensate)

        # Precompute sector boundaries (start from top, go clockwise).
        # Each sector is (start_angle, end_angle, color_index).
        sectors: list[tuple[float, float, int]] = []
        angle = -math.pi / 2   # start at the top of the circle (12 o'clock)
        for i, (_, value) in enumerate(self._slices):
            start = angle
            # Arc length proportional to this slice's share of the total.
            angle += 2.0 * math.pi * value / total
            sectors.append((start, angle, i))

        segments: list[Segment] = []

        # --- Pie chart area: iterate across the columns of this row ---
        for col in range(chart_w):
            # Normalize coordinates to the ellipse's unit circle.
            dx = (col - cx) / rx
            dy = (y - cy) / ry
            if dx * dx + dy * dy <= 1.0:
                # This cell is inside the ellipse — determine which sector it belongs to.
                a = math.atan2(dy, dx)  # angle of this point relative to center

                # `atan2` returns values in [-π, π]. Shift so our sector ranges work.
                if a < -math.pi / 2:
                    a += 2.0 * math.pi

                # Find which sector this angle falls in (default to last sector).
                color_idx = len(sectors) - 1
                for start, end, idx in sectors:
                    if start <= a < end:
                        color_idx = idx
                        break

                # `% len(_PALETTE)` wraps the index so we cycle through colors.
                color = _PALETTE[color_idx % len(_PALETTE)]
                segments.append(Segment("█", Style(color=color, bold=True)))
            else:
                segments.append(Segment(" "))  # outside the circle — blank

        # --- Legend area: one row per slice, vertically centered ---
        legend_top = max(0, int(cy) - len(self._slices) // 2)
        li = y - legend_top  # which legend item this row corresponds to

        if legend_w > 0:
            if 0 <= li < len(self._slices):
                label, value = self._slices[li]
                pct = value / total * 100
                color = _PALETTE[li % len(_PALETTE)]
                bullet = Segment(" ■ ", Style(color=color, bold=True))
                lw = self._max_label_len
                if self._hide_values:
                    text = f"{label:<{lw}} {pct:5.1f}%"
                else:
                    text = f"{label:<{lw}} {pct:5.1f}%  R${value:>12,.2f}"
                text = text[: legend_w - 3].ljust(legend_w - 3)
                segments.append(bullet)
                segments.append(Segment(text))
            elif li == len(self._slices) + 1:
                # Total line — two rows below the last item (one blank gap).
                lw = self._max_label_len
                if self._hide_values:
                    text = f"   {'Total':<{lw}}"
                else:
                    text = f"   {'Total':<{lw}} {'':6}  R${total:>12,.2f}"
                text = text[:legend_w].ljust(legend_w)
                segments.append(Segment(text, Style(bold=True)))
            else:
                segments.append(Segment(" " * legend_w))  # blank legend row

        # `adjust_cell_length(w)` pads/trims the strip to exactly `w` cells wide.
        return Strip(segments).adjust_cell_length(w)


_CARD_DESCRIPTIONS: dict[str, str] = {
    "card-fear-greed": (
        "Measures market sentiment on a 0–100 scale.\n"
        "Low = fear (historically a buy signal).\n"
        "High = greed (market may be overheated)."
    ),
    "card-halving": (
        "Block reward halves every ~210,000 blocks\n"
        "(~4 years), permanently cutting new supply.\n"
        "Historically precedes major bull runs."
    ),
    "card-funding": (
        "Cost paid between longs & shorts on Binance\n"
        "perp futures every 8 h. Positive = bullish\n"
        "crowding. Negative = bearish crowding."
    ),
    "card-btc-price": (
        "Current price sourced from Binance daily\n"
        "candles. Reference price for Mayer's\n"
        "Multiple and 200-day MA calculations."
    ),
    "card-mvrv": (
        "Market cap ÷ realized cap (aggregate cost\n"
        "basis of all holders). Below 1 = undervalued.\n"
        "Above 3.5 = historically near cycle tops."
    ),
    "card-mayers": (
        "Price ÷ 200-day moving average. Below 0.8 =\n"
        "Mayer's accumulation zone. Above 2.4 =\n"
        "historically overheated territory."
    ),
}


def _fear_greed_style(value: int) -> str:
    """Map a 0–100 Fear & Greed score to a Rich color style string."""
    if value <= 24:
        return "bold red"
    if value <= 44:
        return "bold dark_orange"
    if value <= 55:
        return "bold yellow"
    if value <= 74:
        return "bold green"
    return "bold bright_green"


def _funding_rate_style(rate: float) -> str:
    """Map a funding rate percentage to a Rich color style.

    Positive = longs paying (crowded/bullish bias) → orange/red.
    Negative = shorts paying (bearish bias) → green.
    Near-zero = neutral → yellow.
    """
    if rate > 0.05:
        return "bold red"
    if rate < -0.01:
        return "bold green"
    return "bold yellow"


def _mvrv_style(ratio: float) -> str:
    """Map MVRV ratio to a Rich color style.

    Historically: < 1 = undervalued, 1–2.4 = fair, 2.4–3.5 = overvalued, > 3.5 = top zone.
    """
    if ratio < 1.0:
        return "bold bright_green"
    if ratio < 2.4:
        return "bold yellow"
    if ratio < 3.5:
        return "bold dark_orange"
    return "bold red"


def _mvrv_label(ratio: float) -> str:
    """Return a human-readable zone label for the current MVRV ratio."""
    if ratio < 1.0:
        return "Undervalued"
    if ratio < 2.4:
        return "Fair Value"
    if ratio < 3.5:
        return "Overvalued"
    return "Extreme"


def _mayers_style(multiple: float) -> str:
    """Map Mayer's Multiple to a Rich color style.

    Mayer's original thresholds: accumulate below 0.8, caution above 2.4.
    """
    if multiple < 0.8:
        return "bold bright_green"
    if multiple < 1.0:
        return "bold green"
    if multiple < 2.4:
        return "bold yellow"
    return "bold red"


class MetricCard(Static):
    """A bordered card displaying one metric: a title, a main value, and a subtitle.

    `Static` is a built-in Textual widget that renders a Rich-markup string.
    We subclass it and add a helper method so callers never need to build
    the markup string themselves.
    """

    DEFAULT_CSS = """
    MetricCard {
        width: 1fr;
        height: 100%;
        border: solid $primary;
        padding: 2 3;
        margin: 0 1;
        text-align: center;
        content-align: center middle;
    }
    """

    def set_metric(
        self,
        title: str,
        value: str,
        subtitle: str = "",
        value_style: str = "bold",
        description: str = "",
    ) -> None:
        """Re-render the card with new content.

        Rich markup uses `[style]text[/style]` tags, like HTML for the terminal.
        `[dim]` makes text appear faded; color names like `[green]` set the color.
        `\\n` is a newline character — it adds blank lines for visual breathing room.
        """
        lines = [f"[dim]{title}[/dim]", "", f"[{value_style}]{value}[/{value_style}]"]
        if subtitle:
            lines += ["", f"[dim]{subtitle}[/dim]"]
        if description:
            lines += ["", f"[dim]{description}[/dim]"]
        self.update("\n".join(lines))


class BitcoinMetricsPanel(Widget):
    """Tab panel showing 5 Bitcoin metric cards in two rows (3 + 2)."""

    DEFAULT_CSS = """
    BitcoinMetricsPanel {
        height: 1fr;
        layout: vertical;
        padding: 1 2;
    }

    #btc-row1, #btc-row2 {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        """Two `Horizontal` rows of cards.

        `with Horizontal(...)` is a context manager: the widgets yielded inside
        the `with` block become children of that Horizontal container. This is
        how Textual builds widget trees inside `compose()`.
        """
        with Horizontal(id="btc-row1"):
            yield MetricCard(id="card-fear-greed")
            yield MetricCard(id="card-halving")
            yield MetricCard(id="card-funding")
        with Horizontal(id="btc-row2"):
            yield MetricCard(id="card-btc-price")
            yield MetricCard(id="card-mvrv")
            yield MetricCard(id="card-mayers")

    def on_mount(self) -> None:
        """Show placeholder text while waiting for the first data fetch."""
        self.query_one("#card-fear-greed", MetricCard).set_metric(
            "FEAR & GREED INDEX", "Loading…",
            description=_CARD_DESCRIPTIONS["card-fear-greed"],
        )
        self.query_one("#card-halving", MetricCard).set_metric(
            "NEXT HALVING", "Loading…",
            description=_CARD_DESCRIPTIONS["card-halving"],
        )
        self.query_one("#card-funding", MetricCard).set_metric(
            "FUNDING RATE (8H)", "Loading…", "Binance BTCUSDT Perp",
            description=_CARD_DESCRIPTIONS["card-funding"],
        )
        self.query_one("#card-btc-price", MetricCard).set_metric(
            "BTC PRICE", "Loading…", "USD",
            description=_CARD_DESCRIPTIONS["card-btc-price"],
        )
        self.query_one("#card-mvrv", MetricCard).set_metric(
            "MVRV RATIO", "Loading…", "Market Value / Realized Value",
            description=_CARD_DESCRIPTIONS["card-mvrv"],
        )
        self.query_one("#card-mayers", MetricCard).set_metric(
            "MAYER'S MULTIPLE", "Loading…", "Price / 200-Day MA",
            description=_CARD_DESCRIPTIONS["card-mayers"],
        )

    def update_metrics(self, metrics: BitcoinMetrics) -> None:
        """Push a fresh BitcoinMetrics snapshot into each card."""
        if metrics.fear_greed_value is not None:
            value = metrics.fear_greed_value
            label = metrics.fear_greed_label or ""
            self.query_one("#card-fear-greed", MetricCard).set_metric(
                "FEAR & GREED INDEX",
                str(value),
                label,
                _fear_greed_style(value),
                description=_CARD_DESCRIPTIONS["card-fear-greed"],
            )

        if metrics.halving_blocks_remaining is not None:
            est = metrics.halving_estimated_date
            subtitle = est.strftime("~%b %Y") if est is not None else ""
            self.query_one("#card-halving", MetricCard).set_metric(
                "NEXT HALVING",
                f"{metrics.halving_blocks_remaining:,} blocks",
                subtitle,
                "bold cyan",
                description=_CARD_DESCRIPTIONS["card-halving"],
            )

        if metrics.funding_rate is not None:
            rate = metrics.funding_rate
            sign = "+" if rate >= 0 else ""
            self.query_one("#card-funding", MetricCard).set_metric(
                "FUNDING RATE (8H)",
                f"{sign}{rate:.4f}%",
                "Binance BTCUSDT Perp",
                _funding_rate_style(rate),
                description=_CARD_DESCRIPTIONS["card-funding"],
            )

        if metrics.btc_price_usd is not None:
            self.query_one("#card-btc-price", MetricCard).set_metric(
                "BTC PRICE",
                f"${metrics.btc_price_usd:,.0f}",
                "USD",
                "bold yellow",
                description=_CARD_DESCRIPTIONS["card-btc-price"],
            )

        if metrics.mvrv_ratio is not None:
            ratio = metrics.mvrv_ratio
            self.query_one("#card-mvrv", MetricCard).set_metric(
                "MVRV RATIO",
                f"{ratio:.2f}",
                _mvrv_label(ratio),
                _mvrv_style(ratio),
                description=_CARD_DESCRIPTIONS["card-mvrv"],
            )

        if metrics.mayers_multiple is not None:
            multiple = metrics.mayers_multiple
            # Show the 200DMA as subtitle so readers can see the reference price.
            if metrics.mayers_ma200 is not None:
                subtitle = f"200D MA: ${metrics.mayers_ma200:,.0f}"
            else:
                subtitle = "Price / 200-Day MA"
            self.query_one("#card-mayers", MetricCard).set_metric(
                "MAYER'S MULTIPLE",
                f"{multiple:.2f}×",
                subtitle,
                _mayers_style(multiple),
                description=_CARD_DESCRIPTIONS["card-mayers"],
            )


def _fmt_range(low: float | None, high: float | None, currency: str) -> tuple[RenderableType, RenderableType]:
    """Return (low_text, high_text) formatted in native currency, dim styled."""
    dim = "dim"
    low_str = _fmt_price(low, currency) if low is not None else "N/A"
    high_str = _fmt_price(high, currency) if high is not None else "N/A"
    return Text(low_str, style=dim), Text(high_str, style=dim)


class PortfolioTable(DataTable):  # type: ignore[type-arg]
    """A DataTable subclass that knows how to render a PortfolioSnapshot.

    Subclassing `DataTable` (instead of composing it) lets us extend its
    behavior — e.g. handling row-click events — while reusing all of its
    built-in keyboard navigation, scrolling, and column management.
    """

    def on_mount(self) -> None:
        """Called once after the widget is mounted. Set up columns and state."""
        # `*COLUMNS` unpacks the tuple as positional arguments.
        self.add_columns(*COLUMNS)
        self.cursor_type = "row"           # highlight the entire row, not a single cell
        self._expanded_ticker: str | None = None   # which row (if any) is expanded
        self._last_snapshot: PortfolioSnapshot | None = None
        self._hide_values: bool = False
        self._sort_key: str = "pnl"

    def _sorted(self, positions: list[PositionValue]) -> list[PositionValue]:
        """Sort positions according to the active sort key."""
        if self._sort_key == "name":
            return sorted(positions, key=lambda pv: pv.ticker)
        if self._sort_key == "value":
            return sorted(positions, key=lambda pv: pv.value_brl, reverse=True)
        if self._sort_key == "24h":
            return sorted(positions, key=lambda pv: pv.change_pct if pv.change_pct is not None else float("-inf"), reverse=True)
        if self._sort_key == "1w":
            return sorted(positions, key=lambda pv: pv.change_pct_1w if pv.change_pct_1w is not None else float("-inf"), reverse=True)
        # default: "pnl"
        return sorted(positions, key=lambda pv: pv.pnl_pct if pv.pnl_pct is not None else float("-inf"), reverse=True)

    def _row_index_for(self, ticker: str) -> int:
        """Return the row index of a ticker after the table has been rebuilt.

        Needed to restore the cursor position after we clear + re-add all rows.
        """
        if self._last_snapshot is None:
            return 0
        idx = 0
        for pv in self._sorted(self._last_snapshot.positions):
            if pv.ticker == ticker:
                return idx
            idx += 1
            if pv.ticker == self._expanded_ticker:
                idx += 2  # account for the two detail sub-rows inserted after this ticker
        return 0

    def collapse(self) -> None:
        """Collapse any currently expanded row and keep the cursor on it."""
        if self._expanded_ticker is not None:
            key = self._expanded_ticker
            self._expanded_ticker = None
            if self._last_snapshot is not None:
                self.update(self._last_snapshot, hide_values=self._hide_values)
                self.move_cursor(row=self._row_index_for(key))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Toggle the detail sub-rows when the user clicks or presses Enter on a row.

        Textual routes events by name: `on_<widget_type>_<event_name>`.
        `DataTable.RowSelected` is fired whenever a row is selected.
        """
        # `event.row_key.value` is the string key we assigned with `key=` in `add_row`.
        key = str(event.row_key.value) if event.row_key.value is not None else ""
        if "::" in key:
            return  # detail sub-row clicked — ignore, don't toggle again

        # Toggle: if this ticker is already expanded, collapse it; otherwise expand it.
        self._expanded_ticker = None if self._expanded_ticker == key else key
        if self._last_snapshot is not None:
            self.update(self._last_snapshot, hide_values=self._hide_values)
        self.move_cursor(row=self._row_index_for(key))

    def update(  # type: ignore[override]
        self, snapshot: PortfolioSnapshot, hide_values: bool = False, sort_key: str = "pnl"
    ) -> None:
        """Rebuild the table from a new snapshot.

        We clear and re-add all rows on every update. This is simpler than
        diffing individual cells, and Textual handles the screen redraw efficiently.
        """
        self._last_snapshot = snapshot
        self._hide_values = hide_values
        self._sort_key = sort_key

        # Remember the cursor position so we can restore it after rebuilding.
        saved_row = self.cursor_row if self.row_count > 0 else None
        self.clear()

        sorted_positions = self._sorted(snapshot.positions)

        masked = "-----"   # shown in place of sensitive numbers when hide_values is True
        dim = "dim"
        empty = Text("", style=dim)

        # `snapshot.total_value` is the unfiltered portfolio total — app.py
        # deliberately does not override it in the filtered snapshot so that
        # "% Portfolio" always shows each position's share of the whole portfolio.
        grand_total = snapshot.total_value + snapshot.fixed_income_total

        for pv in sorted_positions:
            pct_portfolio = pv.value_brl / grand_total * 100 if grand_total > 0 else 0.0
            self.add_row(
                pv.ticker,
                _fmt_exchange(pv.exchange, pv.market_open),
                pv.category,
                masked if hide_values else _fmt_quantity(pv.quantity, pv.category),
                _fmt_price(pv.value_brl / pv.quantity, "BRL") if pv.category.lower() == "crypto" else _fmt_price(pv.price, pv.native_currency),
                Text(_fmt_price(pv.avg_price_native, "BRL" if pv.category.lower() == "crypto" else pv.native_currency), style="dim") if pv.avg_price_native is not None else Text("----", style="dim"),
                masked if hide_values else f"R${pv.value_brl:,.2f}",
                f"{pct_portfolio:.1f}%",
                _fmt_pnl(pv.pnl_pct),
                _fmt_change(pv.change_pct),
                _fmt_change(pv.change_pct_1w),
                _fmt_change(pv.change_pct_6m),
                _fmt_change(pv.change_pct_12m),
                key=pv.ticker,   # unique key used for cursor restoration and expand/collapse
            )

            if pv.ticker == self._expanded_ticker:
                # Insert two detail rows immediately below the expanded ticker.
                # Their keys use "::" so `on_data_table_row_selected` can ignore them.
                low_day, high_day = _fmt_range(pv.day_low, pv.day_high, pv.native_currency)
                low_52w, high_52w = _fmt_range(pv.week_52_low, pv.week_52_high, pv.native_currency)
                self.add_row(
                    Text("  └ Day range", style=dim),
                    empty, empty,
                    Text("Low", style=dim), low_day,
                    Text("High", style=dim), high_day,
                    empty, empty, empty, empty, empty, empty,
                    key=f"{pv.ticker}::day",
                )
                self.add_row(
                    Text("  └ 52W range", style=dim),
                    empty, empty,
                    Text("Low", style=dim), low_52w,
                    Text("High", style=dim), high_52w,
                    empty, empty, empty, empty, empty, empty,
                    key=f"{pv.ticker}::52w",
                )

        if saved_row is not None:
            # `min(saved_row, self.row_count - 1)` prevents the cursor from going
            # past the last row if rows were removed since the last update.
            self.move_cursor(row=min(saved_row, self.row_count - 1))


_FI_COLUMNS = ("Name", "Amount (R$)", "% of Portfolio")


class FixedIncomeTable(DataTable):  # type: ignore[type-arg]
    """A DataTable that displays fixed income positions.

    Each row shows the investment name, its BRL amount, and its share of the
    total portfolio (variable positions + fixed income combined).
    """

    def on_mount(self) -> None:
        self.add_columns(*_FI_COLUMNS)
        self.cursor_type = "row"

    def update(
        self,
        positions: list[FixedIncomePosition],
        grand_total: float,
        hide_values: bool = False,
    ) -> None:
        """Rebuild the table from the current fixed income list.

        `grand_total` is the full portfolio value (variable + fixed income) so
        that each row's percentage reflects its share of the whole pie.
        """
        saved_row = self.cursor_row if self.row_count > 0 else None
        self.clear()

        # Sort alphabetically so the order is stable across refreshes.
        for fi in sorted(positions, key=lambda x: x.name):
            pct = fi.amount_brl / grand_total * 100 if grand_total > 0 else 0.0
            self.add_row(
                fi.name,
                "-----" if hide_values else f"R${fi.amount_brl:,.2f}",
                f"{pct:.1f}%",
                key=fi.name,
            )

        if saved_row is not None:
            self.move_cursor(row=min(saved_row, self.row_count - 1))


# ---------------------------------------------------------------------------
# Summary tab
# ---------------------------------------------------------------------------
#
# The Summary panel is a composite widget — a single Textual widget made of
# many smaller widgets arranged in rows. It gives the user an "at a glance"
# view of the whole portfolio without needing to scan the full table.
#
# Layout (top to bottom):
#   1. Hero row     — three large cards: total value, total P&L, day change
#   2. Performance  — four compact cards: 24h / 1W / 6M / 12M deltas
#   3. Middle row   — allocation bars (left)  +  top gainers/losers (right)
#   4. Largest band — top positions by value, one row
#   5. Bitcoin strip— compact Bitcoin context (F&G, price, MVRV, Mayer's)


# `_compact_brl`: helper that formats a BRL amount in a space-saving way for
# subtitles where the full "R$123,456.78" would be too long. Numbers ≥ 1 million
# are shown in millions ("R$1.2M"); numbers ≥ 1 thousand are shown in thousands
# ("R$328k"); smaller numbers are shown with no decimals ("R$540").
def _compact_brl(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"R${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"R${value / 1_000:.0f}k"
    return f"R${value:.0f}"


def _delta_style(delta: float) -> str:
    """Rich style string for a positive (green) or negative (red) delta."""
    return "bold green" if delta >= 0 else "bold red"


def _bar(pct: float, width: int = 12) -> str:
    """Render a horizontal bar of `width` characters representing a 0–100% value.

    `█` is a filled cell; `░` is an empty cell. `int()` truncates toward zero,
    so 8.7% × 12 → 1 filled cell. We clamp so the bar never overflows.
    """
    filled = max(0, min(width, int(round(pct / 100 * width))))
    return "█" * filled + "░" * (width - filled)


class SummaryPanel(Widget):
    """Compose all summary sections into a single tab-fillable widget.

    The panel owns the *layout*; the data rendering happens in `update()`
    (portfolio data) and `update_bitcoin()` (BTC metrics). Each method writes
    directly into the matching sub-widget via `query_one`. The panel keeps no
    derived state of its own beyond what the children already display.
    """

    # `DEFAULT_CSS` here only sets the panel-level layout. The actual sizing of
    # each section (hero / perf / mid / etc.) is defined in `portfolio.tcss`
    # alongside the rest of the project's styles so the whole layout is
    # configurable in one place.
    DEFAULT_CSS = """
    SummaryPanel {
        height: 1fr;
        layout: vertical;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the widget tree for the summary tab.

        Each `Horizontal` / `Vertical` block becomes a row/column container.
        Cards (`MetricCard`) and panels (`Static`) inside are children of the
        surrounding container, so they share its width/height budget.
        """
        # --- Row 1: hero cards (total / P&L / day) ---
        with Horizontal(id="summary-hero"):
            yield MetricCard(id="sum-total", classes="summary-hero-card")
            yield MetricCard(id="sum-pnl", classes="summary-hero-card")
            yield MetricCard(id="sum-day", classes="summary-hero-card")

        # --- Row 2: performance strip (24h / 1W / 6M / 12M) ---
        with Horizontal(id="summary-perf"):
            yield MetricCard(id="sum-24h", classes="summary-metric-card")
            yield MetricCard(id="sum-1w", classes="summary-metric-card")
            yield MetricCard(id="sum-6m", classes="summary-metric-card")
            yield MetricCard(id="sum-12m", classes="summary-metric-card")

        # --- Row 3: allocation + top movers ---
        with Horizontal(id="summary-mid"):
            yield Static(id="sum-alloc", classes="summary-block")
            # `Vertical` stacks the gainers panel above the losers panel.
            with Vertical(id="summary-movers"):
                yield Static(id="sum-gainers", classes="summary-block")
                yield Static(id="sum-losers", classes="summary-block")

        # --- Row 4: largest positions band ---
        yield Static(id="sum-largest", classes="summary-block")

        # --- Row 5: Bitcoin context strip ---
        yield Static(id="sum-btc", classes="summary-block")

    def on_mount(self) -> None:
        """Show placeholders before the first snapshot arrives.

        Without this, the cards render as empty bordered boxes which can look
        broken on first display. The placeholders match what the user sees on
        the Bitcoin tab during loading.
        """
        for card_id, title, subtitle in (
            ("sum-total", "TOTAL PORTFOLIO", ""),
            ("sum-pnl", "TOTAL P&L", ""),
            ("sum-day", "DAY CHANGE", ""),
        ):
            card = self.query_one(f"#{card_id}", MetricCard)
            card.set_metric(title, "Loading…", subtitle)

        for card_id, title in (
            ("sum-24h", "24H"),
            ("sum-1w", "1W"),
            ("sum-6m", "6M"),
            ("sum-12m", "12M"),
        ):
            self.query_one(f"#{card_id}", MetricCard).set_metric(title, "—")

        self.query_one("#sum-alloc", Static).update("Allocation: loading…")
        self.query_one("#sum-gainers", Static).update("Top gainers: loading…")
        self.query_one("#sum-losers", Static).update("Top losers: loading…")
        self.query_one("#sum-largest", Static).update("Largest positions: loading…")
        self.query_one("#sum-btc", Static).update("Bitcoin context: loading…")

    # ----- Portfolio data update ---------------------------------------------

    def update(  # type: ignore[override]
        self,
        snapshot: PortfolioSnapshot,
        hide_values: bool = False,
    ) -> None:
        """Refresh all portfolio-derived sections from a new snapshot.

        The Bitcoin strip is NOT touched here — it is owned by `update_bitcoin`
        because BTC metrics arrive on their own cadence (every ~5 minutes) and
        we don't want to overwrite a fresh BTC reading with stale data from a
        portfolio refresh.
        """
        positions = snapshot.positions
        var_total = snapshot.total_value
        fi_total = snapshot.fixed_income_total
        grand_total = var_total + fi_total

        # `masked` is what we show in place of sensitive numbers when the user
        # has toggled "hide values". Percentages are kept visible because they
        # don't leak the actual size of the portfolio.
        masked = "-----"

        # --- Hero: total portfolio value -------------------------------------
        var_pct = (var_total / grand_total * 100) if grand_total > 0 else 0.0
        fi_pct = (fi_total / grand_total * 100) if grand_total > 0 else 0.0
        total_value_str = masked if hide_values else f"R$ {grand_total:,.2f}"
        if hide_values:
            split_str = f"Var {var_pct:.0f}% · FI {fi_pct:.0f}%"
        else:
            split_str = (
                f"Var {_compact_brl(var_total)} · FI {_compact_brl(fi_total)}  "
                f"({var_pct:.0f}% / {fi_pct:.0f}%)"
            )
        self.query_one("#sum-total", MetricCard).set_metric(
            "TOTAL PORTFOLIO", total_value_str, split_str, "bold",
        )

        # --- Hero: total unrealised P&L --------------------------------------
        # Reconstruct each position's cost basis from its current value and
        # pnl_pct, the same identity the status bar uses:
        #     cost = value / (1 + pnl_pct / 100)
        # Positions without an avg price (pnl_pct is None) are skipped.
        total_cost_brl = 0.0
        total_value_with_cost = 0.0
        for pv in positions:
            if pv.pnl_pct is None:
                continue
            total_cost_brl += pv.value_brl / (1.0 + pv.pnl_pct / 100.0)
            total_value_with_cost += pv.value_brl

        if total_cost_brl > 0:
            pnl_abs = total_value_with_cost - total_cost_brl
            pnl_pct: float | None = pnl_abs / total_cost_brl * 100.0
            sign = "+" if pnl_abs >= 0 else ""
            pnl_value_str = masked if hide_values else f"{sign}R$ {pnl_abs:,.2f}"
            pnl_subtitle = f"{sign}{pnl_pct:.2f}%"
            pnl_style = _delta_style(pnl_abs)
        else:
            pnl_value_str = "N/A"
            pnl_subtitle = ""
            pnl_pct = None
            pnl_style = "bold"
        self.query_one("#sum-pnl", MetricCard).set_metric(
            "TOTAL P&L", pnl_value_str, pnl_subtitle, pnl_style,
        )

        # --- Hero: 24h day change --------------------------------------------
        # `snapshot.total_value_24h` is the engine-supplied estimate of the
        # portfolio value yesterday. Compare to today's variable total because
        # fixed income has no daily change.
        prev_24h = snapshot.total_value_24h
        if prev_24h is not None and prev_24h > 0:
            day_abs = var_total - prev_24h
            day_pct = day_abs / prev_24h * 100.0
            sign = "+" if day_abs >= 0 else ""
            day_value_str = masked if hide_values else f"{sign}R$ {day_abs:,.2f}"
            day_subtitle = f"{sign}{day_pct:.2f}%"
            day_style = _delta_style(day_abs)
        else:
            day_value_str = "N/A"
            day_subtitle = ""
            day_style = "bold"
        self.query_one("#sum-day", MetricCard).set_metric(
            "DAY CHANGE", day_value_str, day_subtitle, day_style,
        )

        # --- Performance strip: 24h / 1W / 6M / 12M --------------------------
        # Each card shows a percentage change vs. the corresponding past total.
        # The absolute R$ delta is the subtitle (hidden in privacy mode).
        for card_id, label, prev in (
            ("sum-24h", "24H", snapshot.total_value_24h),
            ("sum-1w", "1W", snapshot.total_value_1w),
            ("sum-6m", "6M", snapshot.total_value_6m),
            ("sum-12m", "12M", snapshot.total_value_12m),
        ):
            if prev is not None and prev > 0:
                delta = var_total - prev
                pct = delta / prev * 100.0
                sign = "+" if delta >= 0 else ""
                value_str = f"{sign}{pct:.2f}%"
                subtitle = "" if hide_values else f"{sign}R$ {delta:,.0f}"
                style = _delta_style(delta)
            else:
                value_str = "—"
                subtitle = ""
                style = "bold"
            self.query_one(f"#{card_id}", MetricCard).set_metric(
                label, value_str, subtitle, style,
            )

        # --- Allocation block ------------------------------------------------
        # Aggregate by category, add fixed income as its own bucket.
        cat_totals: dict[str, float] = defaultdict(float)
        for pv in positions:
            cat_totals[pv.category or "Unknown"] += pv.value_brl
        if fi_total > 0:
            cat_totals["Fixed Income"] += fi_total

        # `sorted(..., reverse=True)` puts the biggest slice first. We render
        # one bar per category with right-aligned percentages so the columns
        # line up visually.
        items = sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)
        max_label = max((len(name) for name, _ in items), default=0)
        lines = ["[bold]ALLOCATION[/bold]", ""]
        for name, value in items:
            pct = value / grand_total * 100 if grand_total > 0 else 0.0
            lines.append(f"  {name:<{max_label}}  {_bar(pct)}  {pct:5.1f}%")
        lines.append("")
        # Footer info: position count, category count, closed-exchange tally.
        closed = sum(1 for pv in positions if not pv.market_open)
        n_categories = len({pv.category for pv in positions if pv.category})
        footer = f"[dim]  {len(positions)} positions · {n_categories} categories"
        if closed:
            footer += f" · {closed} exchanges closed"
        footer += "[/dim]"
        lines.append(footer)
        self.query_one("#sum-alloc", Static).update("\n".join(lines))

        # --- Top gainers / losers (24h) --------------------------------------
        # Filter out positions with no 24h data, then sort by change_pct.
        with_change = [pv for pv in positions if pv.change_pct is not None]
        # `sorted(..., reverse=True)` puts the biggest gainers first.
        by_change = lambda pv: pv.change_pct or 0.0  # noqa: E731
        gainers = sorted(with_change, key=by_change, reverse=True)[:3]
        losers = sorted(with_change, key=by_change)[:3]

        def _mover_line(pv: PositionValue) -> str:
            pct = pv.change_pct or 0.0
            sign = "+" if pct >= 0 else ""
            color = "green" if pct >= 0 else "red"
            # Reconstruct the 24h absolute R$ change for this position from its
            # current value and percentage (same identity as the deltas above).
            prev_val = pv.value_brl / (1.0 + pct / 100.0) if pct != -100 else 0.0
            delta = pv.value_brl - prev_val
            delta_str = f"{sign}R${delta:,.0f}" if not hide_values else ""
            return (
                f"  [bold]{pv.ticker:<10}[/bold] "
                f"[{color}]{sign}{pct:5.2f}%[/{color}]  "
                f"[{color}]{delta_str}[/{color}]"
            )

        gainer_lines = ["[bold]TOP GAINERS (24h)[/bold]"]
        gainer_lines.extend(_mover_line(pv) for pv in gainers)
        if not gainers:
            gainer_lines.append("  [dim]No data[/dim]")
        self.query_one("#sum-gainers", Static).update("\n".join(gainer_lines))

        loser_lines = ["[bold]TOP LOSERS (24h)[/bold]"]
        loser_lines.extend(_mover_line(pv) for pv in losers)
        if not losers:
            loser_lines.append("  [dim]No data[/dim]")
        self.query_one("#sum-losers", Static).update("\n".join(loser_lines))

        # --- Largest positions band ------------------------------------------
        # Combine variable and fixed-income positions, sort by BRL value, take
        # the top 4 so they fit on one screen row in two columns of two.
        ranked: list[tuple[str, float]] = [
            (pv.ticker, pv.value_brl) for pv in positions
        ] + [(fi.name, fi.amount_brl) for fi in snapshot.fixed_income]
        ranked.sort(key=lambda item: item[1], reverse=True)
        top4 = ranked[:4]

        def _largest_cell(name: str, value: float) -> str:
            pct = value / grand_total * 100 if grand_total > 0 else 0.0
            value_str = masked if hide_values else f"R$ {value:,.0f}"
            return f"[bold]{name:<10}[/bold] {pct:5.1f}%  {value_str}"

        largest_lines = ["[bold]LARGEST POSITIONS[/bold]"]
        # Layout the 4 items into two side-by-side columns: pairs (0,1) and (2,3).
        # `zip` pairs them; missing items are simply empty strings.
        left = top4[:2]
        right = top4[2:4]
        for i in range(max(len(left), len(right))):
            l_cell = _largest_cell(*left[i]) if i < len(left) else ""
            r_cell = _largest_cell(*right[i]) if i < len(right) else ""
            # Pad the left cell to ~40 chars (minus markup) so the right column
            # lines up. Rich strips markup when measuring width, but ljust does
            # not — we use a manual gap instead. The trailing 8 spaces are
            # roughly the column gap.
            largest_lines.append(f"  {l_cell}        {r_cell}")
        self.query_one("#sum-largest", Static).update("\n".join(largest_lines))

    # ----- Bitcoin strip ------------------------------------------------------

    def update_bitcoin(self, metrics: BitcoinMetrics) -> None:
        """Refresh just the Bitcoin context strip at the bottom of the summary.

        Called from `app.watch_btc_snapshot`. Independent of `update()` because
        the two data sources have different refresh cadences.
        """
        parts: list[str] = []

        if metrics.fear_greed_value is not None:
            label = metrics.fear_greed_label or ""
            style = _fear_greed_style(metrics.fear_greed_value)
            parts.append(f"F&G [{style}]{metrics.fear_greed_value} {label}[/{style}]")

        if metrics.btc_price_usd is not None:
            price = metrics.btc_price_usd
            parts.append(f"BTC [bold yellow]${price:,.0f}[/bold yellow]")

        if metrics.mvrv_ratio is not None:
            ratio = metrics.mvrv_ratio
            style = _mvrv_style(ratio)
            zone = _mvrv_label(ratio)
            parts.append(f"MVRV [{style}]{ratio:.2f} {zone}[/{style}]")

        if metrics.mayers_multiple is not None:
            multiple = metrics.mayers_multiple
            style = _mayers_style(multiple)
            parts.append(f"Mayer [{style}]{multiple:.2f}×[/{style}]")

        if not parts:
            text = "[dim]Bitcoin context: no data[/dim]"
        else:
            # Join with a centered divider — same visual rhythm as the status
            # bar so the strip feels native to the rest of the app.
            text = "[bold]BITCOIN CONTEXT[/bold]   " + "   │   ".join(parts)
        self.query_one("#sum-btc", Static).update(text)

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
from typing import Any

# Rich library types for terminal styling.
from rich.segment import Segment  # a piece of text with an optional style
from rich.style import Style  # foreground/background color, bold, italic, etc.
from rich.text import Text  # a string that carries per-character styles

# `RenderableType` is Rich's union type for anything that can be rendered in a
# terminal: a plain string, a `Text` object, a table, etc.
from textual.app import ComposeResult, RenderableType

# `Horizontal` is a container that lays out its children side by side.
from textual.containers import Horizontal

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
COLUMNS = ("Ticker", "Exchange", "Category", "Quantity", "Price", "Avg Price", "Value (R$)", "P&L %", "24h %", "1W %", "6M %", "12M %")

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

        for pv in sorted_positions:
            self.add_row(
                pv.ticker,
                _fmt_exchange(pv.exchange, pv.market_open),
                pv.category,
                masked if hide_values else _fmt_quantity(pv.quantity, pv.category),
                _fmt_price(pv.value_brl / pv.quantity, "BRL") if pv.category.lower() == "crypto" else _fmt_price(pv.price, pv.native_currency),
                Text(_fmt_price(pv.avg_price_native, "BRL" if pv.category.lower() == "crypto" else pv.native_currency), style="dim") if pv.avg_price_native is not None else Text("----", style="dim"),
                masked if hide_values else f"R${pv.value_brl:,.2f}",
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
                    empty, empty, empty, empty, empty,
                    key=f"{pv.ticker}::day",
                )
                self.add_row(
                    Text("  └ 52W range", style=dim),
                    empty, empty,
                    Text("Low", style=dim), low_52w,
                    Text("High", style=dim), high_52w,
                    empty, empty, empty, empty, empty,
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

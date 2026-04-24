import math
from typing import Any

# `RenderableType` is Rich's union type for anything that can be rendered in a
# terminal: a plain string, a `Text` object, a table, etc.
from textual.app import RenderableType

# `DataTable` is a built-in Textual widget that displays rows and columns with
# keyboard navigation (arrow keys, cursor highlighting, selection).
from textual.widgets import DataTable

# `Widget` is the base class for all Textual widgets. You subclass it to build
# custom widgets with their own rendering logic.
from textual.widget import Widget

# `Strip` is a low-level concept in Textual: a single rendered line of the terminal,
# made up of styled `Segment` objects.
from textual.strip import Strip

# Rich library types for terminal styling.
from rich.segment import Segment   # a piece of text with an optional style
from rich.style import Style       # foreground/background color, bold, italic, etc.
from rich.text import Text         # a string that carries per-character styles

from portfolio.core.models import PortfolioSnapshot

# Tuple of column header strings — used both here and when rebuilding the table.
COLUMNS = ("Ticker", "Exchange", "Category", "Quantity", "Price", "Value (R$)", "24h %", "1W %")

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
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        # `**kwargs` captures any keyword arguments and forwards them to the parent.
        # This allows callers to pass Textual's standard widget options (like `id=`).
        super().__init__(**kwargs)
        self._slices: list[tuple[str, float]] = []  # (label, value) pairs
        self._hide_values: bool = False

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
        # `self.refresh()` tells Textual to re-render this widget on the next frame.
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
                if self._hide_values:
                    text = f"{label:<10} {pct:5.1f}%"        # left-aligned in 10 chars
                else:
                    text = f"{label:<10} {pct:5.1f}%  R${value:>12,.2f}"   # right-aligned value
                text = text[: legend_w - 3].ljust(legend_w - 3)   # truncate + pad to fit
                segments.append(bullet)
                segments.append(Segment(text))
            elif li == len(self._slices) + 1:
                # Total line — two rows below the last item (one blank gap).
                if self._hide_values:
                    text = f"   {'Total':<10}"
                else:
                    text = f"   {'Total':<10} {'':6}  R${total:>12,.2f}"
                text = text[:legend_w].ljust(legend_w)
                segments.append(Segment(text, Style(bold=True)))
            else:
                segments.append(Segment(" " * legend_w))  # blank legend row

        # `adjust_cell_length(w)` pads/trims the strip to exactly `w` cells wide.
        return Strip(segments).adjust_cell_length(w)


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

    def _row_index_for(self, ticker: str) -> int:
        """Return the row index of a ticker after the table has been rebuilt.

        Needed to restore the cursor position after we clear + re-add all rows.
        """
        if self._last_snapshot is None:
            return 0
        sorted_positions = sorted(
            self._last_snapshot.positions,
            # `key=lambda` tells `sorted` what value to compare.
            # `float("-inf")` is negative infinity — positions without a change
            # percentage sort to the bottom.
            key=lambda pv: pv.change_pct if pv.change_pct is not None else float("-inf"),
            reverse=True,
        )
        idx = 0
        for pv in sorted_positions:
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
        self, snapshot: PortfolioSnapshot, hide_values: bool = False
    ) -> None:
        """Rebuild the table from a new snapshot.

        We clear and re-add all rows on every update. This is simpler than
        diffing individual cells, and Textual handles the screen redraw efficiently.
        """
        self._last_snapshot = snapshot
        self._hide_values = hide_values

        # Remember the cursor position so we can restore it after rebuilding.
        saved_row = self.cursor_row if self.row_count > 0 else None
        self.clear()

        # Sort by 24h change descending — best performers appear at the top.
        sorted_positions = sorted(
            snapshot.positions,
            key=lambda pv: pv.change_pct if pv.change_pct is not None else float("-inf"),
            reverse=True,
        )

        masked = "-----"   # shown in place of sensitive numbers when hide_values is True
        dim = "dim"
        empty = Text("", style=dim)

        for pv in sorted_positions:
            self.add_row(
                pv.ticker,
                _fmt_exchange(pv.exchange, pv.market_open),
                pv.category,
                masked if hide_values else _fmt_quantity(pv.quantity, pv.category),
                _fmt_price(pv.price, pv.native_currency),
                masked if hide_values else f"R${pv.value_brl:,.2f}",
                _fmt_change(pv.change_pct),
                _fmt_change(pv.change_pct_1w),
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
                    empty,
                    key=f"{pv.ticker}::day",
                )
                self.add_row(
                    Text("  └ 52W range", style=dim),
                    empty, empty,
                    Text("Low", style=dim), low_52w,
                    Text("High", style=dim), high_52w,
                    empty,
                    key=f"{pv.ticker}::52w",
                )

        if saved_row is not None:
            # `min(saved_row, self.row_count - 1)` prevents the cursor from going
            # past the last row if rows were removed since the last update.
            self.move_cursor(row=min(saved_row, self.row_count - 1))

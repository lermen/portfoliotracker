import math
from typing import Any

from textual.app import RenderableType
from textual.widgets import DataTable
from textual.widget import Widget
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from portfolio.core.models import PortfolioSnapshot

COLUMNS = ("Ticker", "Exchange", "Category", "Quantity", "Price", "Value (R$)", "24h %", "1W %")

_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "BRL": "R$",
    "GBP": "£",
}


def _fmt_exchange(exchange: str, market_open: bool) -> RenderableType:
    if market_open:
        return Text(exchange, style="green")
    return Text(f"{exchange} (c)", style="red")


def _fmt_price(price: float, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    return f"{symbol}{price:,.2f}"


def _fmt_quantity(quantity: float, category: str) -> str:
    if category.lower() == "crypto":
        return f"{quantity:,.8f}" if quantity % 1 else f"{quantity:,.0f}"
    return f"{quantity:,.2f}" if quantity % 1 else f"{quantity:,.0f}"


def _fmt_change(change_pct: float | None) -> RenderableType:
    if change_pct is None:
        return Text("N/A", style="dim")
    sign = "+" if change_pct >= 0 else ""
    color = "green" if change_pct >= 0 else "red"
    return Text(f"{sign}{change_pct:.2f}%", style=color)


# Okabe-Ito palette — colorblind-safe for deuteranopia, protanopia, tritanopia
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
    """Circular pie chart widget rendered with Unicode block characters."""

    DEFAULT_CSS = """
    PieChart {
        height: 1fr;
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._slices: list[tuple[str, float]] = []
        self._hide_values: bool = False

    def set_data(self, slices: list[tuple[str, float]], hide_values: bool = False) -> None:
        """Set (label, value) pairs; groups tail items as 'Other'."""
        sorted_slices = sorted(slices, key=lambda x: x[1], reverse=True)
        if len(sorted_slices) > _MAX_SLICES:
            top = sorted_slices[:_MAX_SLICES]
            other_val = sum(v for _, v in sorted_slices[_MAX_SLICES:])
            top.append(("Other", other_val))
            self._slices = top
        else:
            self._slices = sorted_slices
        self._hide_values = hide_values
        self.refresh()

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        h = self.size.height

        if not self._slices or h == 0 or w == 0:
            return Strip.blank(w)

        total = sum(v for _, v in self._slices)
        if total == 0:
            return Strip.blank(w)

        chart_w = max(1, int(w * 0.55))
        legend_w = w - chart_w

        # Circle geometry — compensate for ~2:1 char height:width aspect ratio
        cx = chart_w / 2.0
        cy = h / 2.0
        ry = min(chart_w / 4.0, h / 2.0) * 0.88
        rx = ry * 2.0

        # Precompute sector boundaries (start from top, go clockwise)
        sectors: list[tuple[float, float, int]] = []
        angle = -math.pi / 2
        for i, (_, value) in enumerate(self._slices):
            start = angle
            angle += 2.0 * math.pi * value / total
            sectors.append((start, angle, i))

        segments: list[Segment] = []

        # --- Pie chart area ---
        for col in range(chart_w):
            dx = (col - cx) / rx
            dy = (y - cy) / ry
            if dx * dx + dy * dy <= 1.0:
                a = math.atan2(dy, dx)
                # Shift into [-π/2, 3π/2] to match sector ranges
                if a < -math.pi / 2:
                    a += 2.0 * math.pi
                color_idx = len(sectors) - 1
                for start, end, idx in sectors:
                    if start <= a < end:
                        color_idx = idx
                        break
                color = _PALETTE[color_idx % len(_PALETTE)]
                segments.append(Segment("█", Style(color=color, bold=True)))
            else:
                segments.append(Segment(" "))

        # --- Legend area ---
        legend_top = max(0, int(cy) - len(self._slices) // 2)
        li = y - legend_top

        if legend_w > 0:
            if 0 <= li < len(self._slices):
                label, value = self._slices[li]
                pct = value / total * 100
                color = _PALETTE[li % len(_PALETTE)]
                bullet = Segment(" ■ ", Style(color=color, bold=True))
                if self._hide_values:
                    text = f"{label:<10} {pct:5.1f}%"
                else:
                    text = f"{label:<10} {pct:5.1f}%  R${value:>12,.2f}"
                text = text[: legend_w - 3].ljust(legend_w - 3)
                segments.append(bullet)
                segments.append(Segment(text))
            elif li == len(self._slices) + 1:
                # Total line — two rows below the last item (one blank gap)
                if self._hide_values:
                    text = f"   {'Total':<10}"
                else:
                    text = f"   {'Total':<10} {'':6}  R${total:>12,.2f}"
                text = text[:legend_w].ljust(legend_w)
                segments.append(Segment(text, Style(bold=True)))
            else:
                segments.append(Segment(" " * legend_w))

        return Strip(segments).adjust_cell_length(w)


def _fmt_range(low: float | None, high: float | None, currency: str) -> tuple[RenderableType, RenderableType]:
    """Return (low_text, high_text) formatted in native currency, dim styled."""
    dim = "dim"
    low_str = _fmt_price(low, currency) if low is not None else "N/A"
    high_str = _fmt_price(high, currency) if high is not None else "N/A"
    return Text(low_str, style=dim), Text(high_str, style=dim)


class PortfolioTable(DataTable):  # type: ignore[type-arg]
    def on_mount(self) -> None:
        self.add_columns(*COLUMNS)
        self.cursor_type = "row"
        self._expanded_ticker: str | None = None
        self._last_snapshot: PortfolioSnapshot | None = None
        self._hide_values: bool = False

    def _row_index_for(self, ticker: str) -> int:
        """Return the row index of a ticker after the table has been rebuilt."""
        if self._last_snapshot is None:
            return 0
        sorted_positions = sorted(
            self._last_snapshot.positions,
            key=lambda pv: pv.change_pct if pv.change_pct is not None else float("-inf"),
            reverse=True,
        )
        idx = 0
        for pv in sorted_positions:
            if pv.ticker == ticker:
                return idx
            idx += 1
            if pv.ticker == self._expanded_ticker:
                idx += 2  # account for the two detail sub-rows
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
        key = str(event.row_key.value) if event.row_key.value is not None else ""
        if "::" in key:
            return  # detail sub-row — ignore
        self._expanded_ticker = None if self._expanded_ticker == key else key
        if self._last_snapshot is not None:
            self.update(self._last_snapshot, hide_values=self._hide_values)
        self.move_cursor(row=self._row_index_for(key))

    def update(  # type: ignore[override]
        self, snapshot: PortfolioSnapshot, hide_values: bool = False
    ) -> None:
        self._last_snapshot = snapshot
        self._hide_values = hide_values
        saved_row = self.cursor_row if self.row_count > 0 else None
        self.clear()
        sorted_positions = sorted(
            snapshot.positions,
            key=lambda pv: pv.change_pct if pv.change_pct is not None else float("-inf"),
            reverse=True,
        )
        masked = "-----"
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
                key=pv.ticker,
            )
            if pv.ticker == self._expanded_ticker:
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
            self.move_cursor(row=min(saved_row, self.row_count - 1))

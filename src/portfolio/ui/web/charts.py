# Builders for Apache ECharts `options` dicts, consumed by `ui.echart()`.
#
# The design handoff hand-rolled these as SVG (`charts.jsx`'s `LineChart`,
# `Donut`, `Treemap`, `PLBars`) because it was a static HTML mock with no
# charting library. NiceGUI ships a native ECharts wrapper, and ECharts has
# built-in line/pie/treemap/bar chart types — so we describe the chart as a
# plain dict instead of drawing it by hand.

from datetime import datetime
from typing import Any

from portfolio.ui.web.format import DOWN_COLOR, UP_COLOR

_AXIS_LINE_COLOR = "#232a38"
_GRID_LINE_COLOR = "#1d2433"
_LABEL_COLOR = "#8b94a8"
_TEXT_COLOR = "#e7eaf0"
_BG_COLOR = "#0b0e14"


def line_chart_option(history: list[tuple[datetime, float]]) -> dict[str, Any]:
    """Portfolio value over time. Green if the value rose, red if it fell."""
    if not history:
        return {
            "xAxis": {"type": "category", "data": []},
            "yAxis": {"type": "value"},
            "series": [],
        }

    xs = [t.strftime("%H:%M:%S") for t, _ in history]
    ys = [round(v, 2) for _, v in history]
    color = UP_COLOR if ys[-1] >= ys[0] else DOWN_COLOR

    return {
        "grid": {"left": 60, "right": 20, "top": 20, "bottom": 30},
        "xAxis": {
            "type": "category",
            "data": xs,
            "boundaryGap": False,
            "axisLine": {"lineStyle": {"color": _AXIS_LINE_COLOR}},
            "axisLabel": {"color": _LABEL_COLOR},
        },
        "yAxis": {
            "type": "value",
            "scale": True,
            "splitLine": {"lineStyle": {"color": _GRID_LINE_COLOR, "type": "dashed"}},
            "axisLabel": {"color": _LABEL_COLOR},
        },
        "series": [
            {
                "type": "line",
                "data": ys,
                "showSymbol": False,
                "lineStyle": {"color": color, "width": 2},
                "areaStyle": {
                    "color": {
                        "type": "linear",
                        "x": 0,
                        "y": 0,
                        "x2": 0,
                        "y2": 1,
                        "colorStops": [
                            {"offset": 0, "color": color + "38"},
                            {"offset": 1, "color": color + "00"},
                        ],
                    },
                },
            },
        ],
        "tooltip": {"trigger": "axis"},
    }


def donut_option(slices: list[tuple[str, float]], colors: list[str]) -> dict[str, Any]:
    """Allocation donut. `slices` and `colors` must be the same length and order."""
    return {
        "color": colors,
        "series": [
            {
                "type": "pie",
                "radius": ["55%", "80%"],
                "avoidLabelOverlap": True,
                "itemStyle": {
                    "borderRadius": 4,
                    "borderColor": _BG_COLOR,
                    "borderWidth": 2,
                },
                "label": {"show": False},
                "data": [
                    {"name": name, "value": round(value, 2)} for name, value in slices
                ],
            },
        ],
        "tooltip": {"trigger": "item"},
    }


def treemap_option(items: list[tuple[str, float, str]]) -> dict[str, Any]:
    """Heat-map of all positions sized by value. `items` = (ticker, value, color)."""
    return {
        "series": [
            {
                "type": "treemap",
                "roam": False,
                "nodeClick": False,
                "breadcrumb": {"show": False},
                "label": {
                    "color": _TEXT_COLOR,
                    "fontFamily": "monospace",
                    "fontWeight": 600,
                },
                "itemStyle": {
                    "borderColor": _BG_COLOR,
                    "borderWidth": 2,
                    "gapWidth": 2,
                },
                "data": [
                    {
                        "name": ticker,
                        "value": round(value, 2),
                        "itemStyle": {"color": color},
                    }
                    for ticker, value, color in items
                ],
            },
        ],
        "tooltip": {"trigger": "item"},
    }


def plbars_option(items: list[tuple[str, float]]) -> dict[str, Any]:
    """Diverging P&L bar chart. `items` = (ticker, pnl_value_brl), any order."""
    ordered = sorted(items, key=lambda item: item[1])
    tickers = [ticker for ticker, _ in ordered]
    values = [round(value, 2) for _, value in ordered]
    return {
        "grid": {"left": 90, "right": 30, "top": 10, "bottom": 10},
        "xAxis": {
            "type": "value",
            "axisLabel": {"color": _LABEL_COLOR},
            "splitLine": {"lineStyle": {"color": _GRID_LINE_COLOR}},
        },
        "yAxis": {
            "type": "category",
            "data": tickers,
            "axisLabel": {"color": _TEXT_COLOR, "fontFamily": "monospace"},
        },
        "series": [
            {
                "type": "bar",
                "data": [
                    {
                        "value": value,
                        "itemStyle": {"color": UP_COLOR if value >= 0 else DOWN_COLOR},
                    }
                    for value in values
                ],
            },
        ],
        "tooltip": {"trigger": "axis"},
    }

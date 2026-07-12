# "Posições" (positions) — the `/positions` route.
#
# A sortable/filterable/searchable table of every position, plus fixed income
# below it. Column sorting is Quasar's native click-to-sort (`sortable: True`
# on each column) rather than the TUI's keyboard-driven sort cycle — see the
# note in components/layout.py for why.

from typing import Any

from nicegui import ui

from portfolio.core.models import FixedIncomePosition, PositionValue
from portfolio.ui.web import format as fmt
from portfolio.ui.web.components.layout import page_shell
from portfolio.ui.web.state import state

_COLUMNS: list[dict[str, Any]] = [
    {
        "name": "ticker",
        "label": "Ticker",
        "field": "ticker",
        "align": "left",
        "sortable": True,
    },
    {
        "name": "exchange",
        "label": "Exchange",
        "field": "exchange",
        "align": "left",
        "sortable": True,
    },
    {
        "name": "category",
        "label": "Categoria",
        "field": "category",
        "align": "left",
        "sortable": True,
    },
    {
        "name": "quantity",
        "label": "Qtd.",
        "field": "quantity",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "price",
        "label": "Cotação",
        "field": "price",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "avg_price",
        "label": "Preço médio",
        "field": "avg_price",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "value",
        "label": "Valor (R$)",
        "field": "value",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "pct_portfolio",
        "label": "% Carteira",
        "field": "pct_portfolio",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "pnl_pct",
        "label": "P&L %",
        "field": "pnl_pct",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "day_pct",
        "label": "Dia %",
        "field": "day_pct",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "week_pct",
        "label": "1S %",
        "field": "week_pct",
        "align": "right",
        "sortable": True,
    },
]

_FI_COLUMNS: list[dict[str, Any]] = [
    {
        "name": "name",
        "label": "Nome",
        "field": "name",
        "align": "left",
        "sortable": True,
    },
    {
        "name": "amount",
        "label": "Valor (R$)",
        "field": "amount",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "pct_portfolio",
        "label": "% Carteira",
        "field": "pct_portfolio",
        "align": "right",
        "sortable": True,
    },
]


def _position_row(
    pv: PositionValue, hide: bool, pct_portfolio: float
) -> dict[str, Any]:
    return {
        "ticker": pv.ticker,
        "exchange": pv.exchange or "—",
        "category": pv.category or "—",
        "quantity": "•••" if hide else fmt.fmt_quantity(pv.quantity, pv.category),
        "price": "•••" if hide else f"{pv.price:,.2f}",
        "avg_price": "—"
        if pv.avg_price_native is None
        else ("•••" if hide else f"{pv.avg_price_native:,.2f}"),
        "value": fmt.fmt_brl(pv.value_brl, hide=hide),
        "pct_portfolio": f"{pct_portfolio:.1f}%",
        "pnl_pct": fmt.fmt_pct(pv.pnl_pct),
        "day_pct": fmt.fmt_pct(pv.change_pct),
        "week_pct": fmt.fmt_pct(pv.change_pct_1w),
    }


def _fi_row(
    fi: FixedIncomePosition, hide: bool, pct_portfolio: float
) -> dict[str, Any]:
    return {
        "name": fi.name,
        "amount": fmt.fmt_brl(fi.amount_brl, hide=hide),
        "pct_portfolio": f"{pct_portfolio:.1f}%",
    }


@ui.page("/positions")
def positions_page() -> None:
    with page_shell("/positions"):
        with ui.row().classes("w-full items-center gap-4"):
            search = (
                ui.input(placeholder="Buscar ticker ou categoria…")
                .classes("w-64")
                .props("dense outlined")
            )
            category_tabs = ui.tabs().classes("flex-1")
            with category_tabs:
                ui.tab("ALL", label="Todos")

        table = ui.table(
            columns=_COLUMNS, rows=[], row_key="ticker", pagination=0
        ).classes("w-full")
        table.bind_filter_from(search, "value")

        ui.label("Renda fixa").classes("text-sm font-semibold mt-4")
        fi_table = ui.table(
            columns=_FI_COLUMNS, rows=[], row_key="name", pagination=0
        ).classes("w-full")

        known_categories: set[str] = set()

        def refresh() -> None:
            snapshot = state.snapshot
            if snapshot is None:
                return
            hide = state.hide_values

            categories = sorted(
                {pv.category for pv in snapshot.positions if pv.category}
            )
            if set(categories) - known_categories:
                known_categories.update(categories)
                category_tabs.clear()
                with category_tabs:
                    ui.tab("ALL", label="Todos")
                    for cat in categories:
                        ui.tab(cat, label=cat)
                category_tabs.set_value(state.filter_category)

            grand_total = snapshot.total_value + snapshot.fixed_income_total
            positions = snapshot.positions
            if state.filter_category != "ALL":
                positions = [
                    pv for pv in positions if pv.category == state.filter_category
                ]

            table.rows = [
                _position_row(
                    pv,
                    hide,
                    pv.value_brl / grand_total * 100 if grand_total > 0 else 0.0,
                )
                for pv in positions
            ]
            table.update()

            fi_table.rows = [
                _fi_row(
                    fi,
                    hide,
                    fi.amount_brl / grand_total * 100 if grand_total > 0 else 0.0,
                )
                for fi in sorted(snapshot.fixed_income, key=lambda fi: fi.name)
            ]
            fi_table.update()

        def on_category_change() -> None:
            state.filter_category = category_tabs.value or "ALL"

        category_tabs.on_value_change(on_category_change)

        ui.timer(1.0, refresh)

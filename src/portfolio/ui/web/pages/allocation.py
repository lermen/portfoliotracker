# "Alocação" (allocation) — the `/allocation` route.
#
# Layout mirrors the handoff's screen 3: a full-width treemap of every
# position sized by market value, then a donut-by-class and a diverging P&L
# bar chart side by side.

from nicegui import ui

from portfolio.core.models import PositionValue
from portfolio.ui.web import format as fmt
from portfolio.ui.web.charts import donut_option, plbars_option, treemap_option
from portfolio.ui.web.components.layout import page_shell
from portfolio.ui.web.state import state


def _position_pnl_brl(pv: PositionValue) -> float | None:
    """Reconstruct the absolute unrealised P&L in BRL from `value_brl` and `pnl_pct`."""
    if pv.pnl_pct is None:
        return None
    denom = 1.0 + pv.pnl_pct / 100.0
    if denom == 0:
        return None
    cost = pv.value_brl / denom
    return pv.value_brl - cost


@ui.page("/allocation")
def allocation_page() -> None:
    with page_shell("/allocation"):
        with ui.card().classes("card-surface w-full"):
            ui.label("Mapa de calor — todas as posições").classes(
                "text-sm font-semibold"
            )
            ui.label("tamanho ∝ valor de mercado").classes("label-mute")
            treemap = (
                ui.echart(treemap_option([])).classes("w-full").style("height:360px")
            )

        with ui.row().classes("w-full gap-4 items-stretch"):
            with ui.card().classes("card-surface flex-1"):
                ui.label("Alocação por classe").classes("text-sm font-semibold")
                donut = (
                    ui.echart(donut_option([], []))
                    .classes("w-full")
                    .style("height:280px")
                )
            with ui.card().classes("card-surface flex-1"):
                ui.label("P&L por posição").classes("text-sm font-semibold")
                plbars = (
                    ui.echart(plbars_option([])).classes("w-full").style("height:280px")
                )

        def refresh() -> None:
            snapshot = state.snapshot
            if snapshot is None:
                return

            treemap_items = [
                (
                    pv.ticker,
                    pv.value_brl,
                    fmt.color_for_position(pv.ticker, pv.category),
                )
                for pv in snapshot.positions
                if pv.value_brl > 0
            ]
            treemap.options.clear()
            treemap.options.update(treemap_option(treemap_items))
            treemap.update()

            totals: dict[str, float] = {}
            for pv in snapshot.positions:
                key = pv.category or "Unknown"
                totals[key] = totals.get(key, 0.0) + pv.value_brl
            if snapshot.fixed_income_total > 0:
                totals["Fixed Income"] = (
                    totals.get("Fixed Income", 0.0) + snapshot.fixed_income_total
                )
            slices = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            colors = [fmt.color_for_position(name, name) for name, _ in slices]
            donut.options.clear()
            donut.options.update(donut_option(slices, colors))
            donut.update()

            pnl_items: list[tuple[str, float]] = []
            for pv in snapshot.positions:
                pnl = _position_pnl_brl(pv)
                if pnl is not None:
                    pnl_items.append((pv.ticker, pnl))
            plbars.options.clear()
            plbars.options.update(plbars_option(pnl_items))
            plbars.update()

        ui.timer(1.0, refresh)

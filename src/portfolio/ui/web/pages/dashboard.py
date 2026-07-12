# "Visão geral" (dashboard) — the `/` route.
#
# Layout mirrors the handoff's screen 1: hero (total value + day/total P&L),
# a line chart of portfolio value, then a 3-column row of allocation donut +
# top gainers + top losers.

from nicegui import ui

from portfolio.core.models import PositionValue
from portfolio.ui.web import format as fmt
from portfolio.ui.web.charts import donut_option, line_chart_option
from portfolio.ui.web.components.layout import page_shell
from portfolio.ui.web.state import state


def _category_totals(
    positions: list[PositionValue], fi_total: float
) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for pv in positions:
        key = pv.category or "Unknown"
        totals[key] = totals.get(key, 0.0) + pv.value_brl
    if fi_total > 0:
        totals["Fixed Income"] = totals.get("Fixed Income", 0.0) + fi_total
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


@ui.page("/")
def dashboard_page() -> None:
    with page_shell("/"):
        ui.label("PATRIMÔNIO TOTAL").classes("label-mute")
        hero_value = ui.label("Carregando…").classes("hero-value tabular-nums")
        with ui.row().classes("gap-4"):
            day_pill = ui.label("").classes("text-sm tabular-nums")
            total_pill = ui.label("").classes("text-sm tabular-nums")

        with ui.card().classes("card-surface w-full"):
            ui.label(
                "Patrimônio ao longo do tempo (desde que este servidor iniciou)"
            ).classes("label-mute")
            chart = (
                ui.echart(line_chart_option([])).classes("w-full").style("height:320px")
            )

        with ui.row().classes("w-full gap-4 items-stretch"):
            with ui.card().classes("card-surface flex-1"):
                ui.label("Alocação por classe").classes("text-sm font-semibold")
                donut = (
                    ui.echart(donut_option([], []))
                    .classes("w-full")
                    .style("height:220px")
                )
            with ui.card().classes("card-surface flex-1"):
                ui.label("Maiores altas (24h)").classes("text-sm font-semibold")
                gainers_col = ui.column().classes("w-full gap-1 mt-2")
            with ui.card().classes("card-surface flex-1"):
                ui.label("Maiores baixas (24h)").classes("text-sm font-semibold")
                losers_col = ui.column().classes("w-full gap-1 mt-2")

        def _mover_row(pv: PositionValue) -> None:
            with ui.row().classes("w-full justify-between text-sm font-mono"):
                ui.label(pv.ticker).classes("font-semibold")
                ui.label(fmt.fmt_pct(pv.change_pct)).style(
                    f"color:{fmt.change_color(pv.change_pct)}"
                )

        def refresh() -> None:
            snapshot = state.snapshot
            if snapshot is None:
                return

            grand_total = snapshot.total_value + snapshot.fixed_income_total
            hero_value.set_text(fmt.fmt_brl(grand_total, hide=state.hide_values))

            prev_24h = snapshot.total_value_24h
            if prev_24h is not None and prev_24h > 0:
                day_delta = snapshot.total_value - prev_24h
                day_pct = day_delta / prev_24h * 100.0
                day_str = fmt.fmt_brl(day_delta, hide=state.hide_values, sign=True)
                day_pill.set_text(f"Hoje: {day_str} ({fmt.fmt_pct(day_pct)})")
                day_pill.style(f"color:{fmt.change_color(day_delta)}")
            else:
                day_pill.set_text("Hoje: N/A")
                day_pill.style("color:#5a6477")

            cost_total = 0.0
            value_with_cost = 0.0
            for pv in snapshot.positions:
                if pv.pnl_pct is None:
                    continue
                denom = 1.0 + pv.pnl_pct / 100.0
                if denom == 0:
                    continue
                cost_total += pv.value_brl / denom
                value_with_cost += pv.value_brl
            if cost_total > 0:
                total_pl = value_with_cost - cost_total
                total_pl_pct = total_pl / cost_total * 100.0
                total_str = fmt.fmt_brl(total_pl, hide=state.hide_values, sign=True)
                total_pill.set_text(f"Total: {total_str} ({fmt.fmt_pct(total_pl_pct)})")
                total_pill.style(f"color:{fmt.change_color(total_pl)}")
            else:
                total_pill.set_text("Total: N/A")
                total_pill.style("color:#5a6477")

            chart.options.clear()
            chart.options.update(line_chart_option(state.history))
            chart.update()

            slices = _category_totals(snapshot.positions, snapshot.fixed_income_total)
            colors = [fmt.color_for_position(name, name) for name, _ in slices]
            donut.options.clear()
            donut.options.update(donut_option(slices, colors))
            donut.update()

            with_change = [pv for pv in snapshot.positions if pv.change_pct is not None]
            gainers = sorted(
                with_change, key=lambda pv: pv.change_pct or 0.0, reverse=True
            )[:4]
            losers = sorted(with_change, key=lambda pv: pv.change_pct or 0.0)[:4]

            gainers_col.clear()
            with gainers_col:
                if not gainers:
                    ui.label("Sem dados").classes("text-sm text-gray-500")
                for pv in gainers:
                    _mover_row(pv)

            losers_col.clear()
            with losers_col:
                if not losers:
                    ui.label("Sem dados").classes("text-sm text-gray-500")
                for pv in losers:
                    _mover_row(pv)

        ui.timer(1.0, refresh)

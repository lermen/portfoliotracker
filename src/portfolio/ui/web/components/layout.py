# Shared page chrome: sidebar nav, header, dark theme, and the global keyboard
# shortcut. Every `@ui.page` function wraps its content in `with page_shell(...):`
# so the three screens (dashboard/positions/allocation) share one look.
#
# Keybinding parity with the TUI (see ui/tui/app.py's BINDINGS):
# only 'h' (toggle hide-balances) is ported here. The TUI's sort keys
# (p/d/w/n/v) are superseded by clicking a sortable column header — Quasar's
# QTable (which `ui.table` wraps) already sorts client-side when a column has
# `sortable: True`, so there is no need to reproduce the keyboard cycling.
# The mutating shortcuts (u/U, P, A, D — edit/add/delete a position) are not
# ported: they need modal dialogs wired to `core/writer.py`, which is a
# separate follow-up, not part of read-only parity.

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import events, ui

from portfolio.ui.web.state import state

_NAV: list[tuple[str, str, str]] = [
    ("/", "Visão geral", "insights"),
    ("/positions", "Posições", "list_alt"),
    ("/allocation", "Alocação", "donut_large"),
]


def _toggle_hide_values() -> None:
    state.hide_values = not state.hide_values


def _handle_key(e: events.KeyEventArguments) -> None:
    if e.action.keydown and e.key == "h":
        _toggle_hide_values()


@contextmanager
def page_shell(active_path: str) -> Iterator[None]:
    """Render the header + sidebar and yield control for page-specific content."""
    ui.add_head_html('<link rel="stylesheet" href="/static/style.css">')
    ui.dark_mode(True)
    ui.keyboard(on_key=_handle_key)

    with (
        ui.header()
        .classes("items-center justify-between px-4")
        .style("background-color:#11151d;border-bottom:1px solid #232a38")
    ):
        ui.label("Portfolio Tracker").classes("text-lg font-semibold")
        ui.button(icon="visibility", on_click=_toggle_hide_values).props(
            "flat round dense"
        ).tooltip("Hide balances (h)")

    with ui.left_drawer(value=True).style(
        "background-color:#11151d;border-right:1px solid #232a38"
    ):
        for path, label, icon in _NAV:
            classes = (
                "text-white font-semibold" if path == active_path else "text-gray-400"
            )
            with ui.row().classes("items-center gap-2 py-1"):
                ui.icon(icon).classes(classes)
                ui.link(label, path).classes(f"no-underline {classes}")

    with ui.column().classes("w-full p-4 gap-4"):
        yield

# Entry point for the NiceGUI web UI (`portfolio web`).
#
# Mirrors `ui/tui/app.py`: starts the same `core.engine`/`core.bitcoin_fetcher`
# background workers and polls the same kind of `asyncio.Queue`. The only
# difference from the TUI is *how* the snapshot is turned into pixels — the
# data flow (`Engine (producer) -> asyncio.Queue -> UI (consumer)`) is
# unchanged, and this module never imports anything from `core.engine`'s
# internals, only its public `run_engine`/`run_bitcoin_engine` functions.

import asyncio
from pathlib import Path

from nicegui import app, background_tasks, ui

from portfolio.core.bitcoin_fetcher import run_bitcoin_engine
from portfolio.core.engine import run_engine
from portfolio.core.settings import settings
from portfolio.ui.web.state import state

_STATIC_DIR = Path(__file__).parent / "static"


async def _poll_loop() -> None:
    """Drain both queues into `state` once a second.

    Pages don't await the queues themselves — each page's own `ui.timer`
    just reads `state.snapshot`/`state.btc_snapshot` — so this single loop is
    the only consumer of the queues, same role `poll_queue`/`poll_btc_queue`
    play in the TUI's `app.py`.
    """
    while True:
        if not state.queue.empty():
            state.snapshot = await state.queue.get()
            state.record_history(state.snapshot)
        if not state.btc_queue.empty():
            state.btc_snapshot = await state.btc_queue.get()
        await asyncio.sleep(1.0)


def _start_background_workers() -> None:
    background_tasks.create(
        run_engine(state.queue, state.refresh_event), name="portfolio-engine"
    )
    background_tasks.create(run_bitcoin_engine(state.btc_queue), name="bitcoin-engine")
    background_tasks.create(_poll_loop(), name="portfolio-poll")


def create_app() -> None:
    """Register routes and background workers. Safe to call once at import time."""
    # Importing these modules is what registers their `@ui.page` routes with
    # NiceGUI — the imports are the side effect, not the names themselves.
    from portfolio.ui.web.pages import allocation, dashboard, positions  # noqa: F401

    app.add_static_files("/static", str(_STATIC_DIR))
    app.on_startup(_start_background_workers)


def run() -> None:
    """Launch the NiceGUI web server. Called by the `portfolio web` CLI command."""
    create_app()
    ui.run(
        host=settings.web_host,
        port=settings.web_port,
        title="Portfolio Tracker",
        dark=True,
        reload=False,
        show=True,
    )

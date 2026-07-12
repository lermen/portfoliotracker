# Shared in-process state for the NiceGUI web UI.
#
# There is exactly one instance of `WebState` (the `state` singleton below),
# because the whole app tracks a single portfolio file — the same design the
# TUI uses. Every browser tab connecting to the server sees the same live
# data; there is no per-user/per-tab portfolio.
#
# Pages read this object from a `ui.timer` poll loop (see `pages/*.py`), the
# same "poll a shared value every second" pattern `ui/tui/app.py` uses with
# Textual's reactive variables — just without a framework-level reactive
# system to hook into.

import asyncio
from datetime import datetime

from portfolio.core.models import BitcoinMetrics, PortfolioSnapshot

# How many (timestamp, total_value) points to keep for the dashboard line
# chart. The engine has no historical time series of its own (see
# `core/engine.py` — it only back-calculates single reference points for
# 24h/1w/6m/12m). This buffer is built up live from snapshots as they arrive,
# so the chart only ever covers "since this web server started", not real
# calendar history like the design mock's 1D/1S/1M/6M/1A/MAX ranges assume.
_MAX_HISTORY_POINTS = 2000


class WebState:
    """Mutable state shared by every page and the background workers."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[PortfolioSnapshot] = asyncio.Queue()
        self.btc_queue: asyncio.Queue[BitcoinMetrics] = asyncio.Queue()
        # Set by write actions to wake the engine early, same as the TUI.
        self.refresh_event: asyncio.Event = asyncio.Event()

        self.snapshot: PortfolioSnapshot | None = None
        self.btc_snapshot: BitcoinMetrics | None = None

        # Rolling (timestamp, grand_total_brl) history, oldest first.
        self.history: list[tuple[datetime, float]] = []

        # UI preferences. Privacy mode defaults on, matching the TUI's default.
        self.hide_values: bool = True
        self.filter_category: str = "ALL"
        self.search_query: str = ""

    def record_history(self, snapshot: PortfolioSnapshot) -> None:
        """Append the snapshot's grand total to the in-memory history buffer."""
        grand_total = snapshot.total_value + snapshot.fixed_income_total
        self.history.append((snapshot.timestamp, grand_total))
        if len(self.history) > _MAX_HISTORY_POINTS:
            del self.history[: len(self.history) - _MAX_HISTORY_POINTS]


# Module-level singleton — every page and background worker imports this same
# object, mirroring the `settings` singleton pattern in `core/settings.py`.
state = WebState()

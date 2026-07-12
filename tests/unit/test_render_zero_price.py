# Regression test for the division-by-zero crash.
#
# When a live price comes back as 0 (a failed fetch, or a genuinely zero-priced
# holding like a subscription right), the engine computes `pnl_pct == -100` and
# `change_pct == -100`. The render path then reconstructs prior values with
# `value / (1 + pct/100)`, whose denominator becomes 0 — crashing the TUI.
#
# This test drives the real app's render path with exactly that data and asserts
# it renders without raising. Before the fix it fails with ZeroDivisionError.

from datetime import UTC, datetime

from portfolio.core.models import PortfolioSnapshot, PositionValue
from portfolio.ui.tui.app import PortfolioApp
from portfolio.ui.tui.widgets import SummaryPanel


def _snapshot_with_zero_price() -> PortfolioSnapshot:
    """A snapshot mixing one normal position with a zero-price one."""
    normal = PositionValue(
        ticker="ITSA4.SA", quantity=100, price=14.17, value_brl=1417.0,
        avg_price_native=7.84, pnl_pct=80.7,
        change_pct=1.2, change_pct_1w=2.0, change_pct_6m=5.0, change_pct_12m=10.0,
        exchange="B3", category="Stock",
    )
    # Price fell to zero → every percentage is exactly -100, the crash trigger.
    zero = PositionValue(
        ticker="RECR12.SA", quantity=47, price=0.0, value_brl=0.0,
        avg_price_native=14.17, pnl_pct=-100.0,
        change_pct=-100.0, change_pct_1w=-100.0,
        change_pct_6m=-100.0, change_pct_12m=-100.0,
        exchange="B3", category="Fund",
    )
    return PortfolioSnapshot(
        positions=[normal, zero],
        total_value=1417.0,
        total_value_24h=1400.0,
        total_value_1w=1390.0,
        total_value_6m=1350.0,
        total_value_12m=1300.0,
        timestamp=datetime.now(UTC),
    )


async def test_app_renders_zero_price_without_crashing() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Assigning the reactive triggers watch_snapshot → _render(), which also
        # drives SummaryPanel.update. Any ZeroDivisionError propagates here.
        app.snapshot = _snapshot_with_zero_price()
        await pilot.pause()
        # Render completed: the status bar shows a total/P&L line, not "—".
        total_label = app.query_one("#total")
        assert "P&L" in str(total_label.render())


async def test_summary_panel_zero_price_pnl() -> None:
    # Exercise the SummaryPanel P&L loop directly with the zero-price position.
    class _Host(PortfolioApp):
        pass

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SummaryPanel)
        # Should not raise; the -100% position is skipped in the cost/value ratio.
        panel.update(_snapshot_with_zero_price(), hide_values=False)
        await pilot.pause()

# Tests for the benchmark index (Ibovespa) card on the Summary tab, and for the
# lightweight index fetcher that feeds it.

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from portfolio.core.fetcher import fetch_index_safe
from portfolio.core.models import IndexQuote, PortfolioSnapshot
from portfolio.ui.tui.app import PortfolioApp
from portfolio.ui.tui.widgets import MetricCard, SummaryPanel


def _snapshot(benchmark: IndexQuote | None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=[], total_value=0.0, benchmark=benchmark,
        timestamp=datetime.now(UTC),
    )


async def _render_index_card(
    snapshot: PortfolioSnapshot, hide_values: bool = False
) -> str:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(snapshot, hide_values=hide_values)
        await pilot.pause()
        return str(app.query_one("#sum-index", MetricCard).render())


async def test_index_card_shows_points_and_change() -> None:
    quote = IndexQuote(ticker="^BVSP", points=128_450.3, change_pct=-1.234)
    card = await _render_index_card(_snapshot(quote))
    assert "IBOVESPA" in card
    assert "128,450 pts" in card
    assert "24h -1.23%" in card


async def test_index_card_stays_visible_in_privacy_mode() -> None:
    quote = IndexQuote(ticker="^BVSP", points=128_450.3, change_pct=0.5)
    card = await _render_index_card(_snapshot(quote), hide_values=True)
    assert "128,450 pts" in card
    assert "24h +0.50%" in card


async def test_index_card_without_data() -> None:
    card = await _render_index_card(_snapshot(None))
    assert "N/A" in card


async def test_fetch_index_computes_change_from_previous_close() -> None:
    info = SimpleNamespace(last_price=110.0, previous_close=100.0)
    with patch("portfolio.core.fetcher.yf.Ticker") as ticker:
        ticker.return_value.fast_info = info
        quote = await fetch_index_safe("^BVSP")
    assert quote is not None
    assert quote.points == 110.0
    assert quote.change_pct is not None
    assert round(quote.change_pct, 6) == 10.0


async def test_fetch_index_returns_none_on_failure() -> None:
    with patch("portfolio.core.fetcher.yf.Ticker", side_effect=RuntimeError("down")):
        assert await fetch_index_safe("^BVSP") is None

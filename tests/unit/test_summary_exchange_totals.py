# Tests for the two per-exchange hero cards on the Summary tab.
#
# `_exchange_total` is the small helper that sums the BRL value of every
# position listed on one exchange. The rendering test then checks that the
# numbers actually reach the cards, including in "hide values" (privacy) mode.

from datetime import UTC, datetime

from portfolio.core.models import (
    FixedIncomePosition,
    PortfolioSnapshot,
    PositionValue,
)
from portfolio.ui.tui.app import PortfolioApp
from portfolio.ui.tui.widgets import (
    MetricCard,
    SummaryPanel,
    _exchange_change,
    _exchange_total,
)


def _pv(
    ticker: str,
    exchange: str,
    value_brl: float,
    change_pct: float | None = None,
    change_pct_1w: float | None = None,
) -> PositionValue:
    """Build a minimal PositionValue — only the fields the totals care about."""
    return PositionValue(
        ticker=ticker, quantity=1, price=value_brl, value_brl=value_brl,
        native_currency="BRL", exchange=exchange, category="Stock",
        change_pct=change_pct, change_pct_1w=change_pct_1w,
    )


def _snapshot() -> PortfolioSnapshot:
    positions = [
        _pv("ITSA4.SA", "B3", 1000.0, 10.0, -20.0),
        _pv("BPAC11.SA", "B3", 500.0, 25.0, 25.0),
        _pv("SAP.DE", "Frankfurt", 2000.0, -20.0, 60.0),
        _pv("BTC-USD", "Crypto", 1500.0, 5.0, 5.0),
        # Exchange column left blank: the ticker suffix decides where it lists.
        _pv("WEGE3.SA", "", 250.0, 0.0, 0.0),
    ]
    return PortfolioSnapshot(
        positions=positions,
        total_value=5250.0,
        fixed_income=[FixedIncomePosition(name="CDB", amount_brl=4750.0)],
        fixed_income_total=4750.0,
        timestamp=datetime.now(UTC),
    )


def test_exchange_total_sums_matching_positions() -> None:
    positions = _snapshot().positions
    # 1000 + 500 from the two explicit B3 rows, + 250 from the blank-exchange
    # row whose ticker ends in ".SA".
    assert _exchange_total(positions, "B3", ".sa") == 1750.0
    assert _exchange_total(positions, "Frankfurt", ".de") == 2000.0


def test_exchange_total_is_case_insensitive() -> None:
    positions = [_pv("ITSA4.SA", "b3", 100.0), _pv("SAP.DE", "FRANKFURT", 200.0)]
    assert _exchange_total(positions, "B3", ".sa") == 100.0
    assert _exchange_total(positions, "Frankfurt", ".de") == 200.0


def test_exchange_total_ignores_unmatched_exchanges() -> None:
    assert _exchange_total(_snapshot().positions, "NYSE", ".us") == 0.0


async def test_summary_cards_show_exchange_totals() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SummaryPanel)
        panel.update(_snapshot(), hide_values=False)
        await pilot.pause()

        b3 = str(app.query_one("#sum-b3", MetricCard).render())
        frankfurt = str(app.query_one("#sum-frankfurt", MetricCard).render())

        assert "TOTAL PORTFOLIO B3" in b3
        assert "1,750.00" in b3
        # 1750 / (5250 variable + 4750 fixed income) = 17.5% of the grand total.
        assert "17.5% of total" in b3

        assert "TOTAL PORTFOLIO FRANKFURT" in frankfurt
        assert "2,000.00" in frankfurt
        assert "20.0% of total" in frankfurt


async def test_summary_cards_mask_values_when_hidden() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SummaryPanel)
        panel.update(_snapshot(), hide_values=True)
        await pilot.pause()

        b3 = str(app.query_one("#sum-b3", MetricCard).render())
        # The amount is hidden, but the percentage stays visible.
        assert "1,750.00" not in b3
        assert "-----" in b3
        assert "17.5% of total" in b3


def test_exchange_change_weights_by_position_size() -> None:
    positions = _snapshot().positions
    # B3 24h: values 1000 (+10%), 500 (+25%), 250 (0%) are worth
    # 909.09 + 400 + 250 = 1559.09 a day ago, so the slice gained 190.91 (12.24%).
    delta, pct = _exchange_change(positions, "B3", ".sa", "change_pct")
    assert delta is not None and pct is not None
    assert round(delta, 2) == 190.91
    assert round(pct, 2) == 12.24

    # Frankfurt is a single -20% position: 2000 today came from 2500.
    delta, pct = _exchange_change(positions, "Frankfurt", ".de", "change_pct")
    assert delta == -500.0
    assert pct == -20.0


def test_exchange_change_1w_uses_the_weekly_field() -> None:
    positions = _snapshot().positions
    # Frankfurt 1W: +60% means 2000 today came from 1250.
    delta, pct = _exchange_change(positions, "Frankfurt", ".de", "change_pct_1w")
    assert delta == 750.0
    assert pct == 60.0


def test_exchange_change_is_none_without_data() -> None:
    # No position on the exchange carries a 24h percentage → nothing to report.
    positions = [_pv("ITSA4.SA", "B3", 1000.0), _pv("BPAC11.SA", "B3", 500.0)]
    assert _exchange_change(positions, "B3", ".sa", "change_pct") == (None, None)


def test_exchange_change_skips_zero_priced_positions() -> None:
    # A price of zero gives change_pct == -100, whose starting value is
    # undefined. It must be skipped, not crash with ZeroDivisionError.
    positions = [
        _pv("ITSA4.SA", "B3", 1000.0, 10.0, 10.0),
        _pv("RECR12.SA", "B3", 0.0, -100.0, -100.0),
    ]
    delta, pct = _exchange_change(positions, "B3", ".sa", "change_pct")
    assert pct is not None and round(pct, 2) == 10.0


async def test_summary_cards_show_24h_and_1w() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(), hide_values=False)
        await pilot.pause()

        b3 = str(app.query_one("#sum-b3", MetricCard).render())
        assert "24h +12.24%" in b3
        # B3 1W: 1000 (-20%) + 500 (+25%) + 250 (0%) came from
        # 1250 + 400 + 250 = 1900 → 1750 today is -7.89%.
        assert "1W -7.89%" in b3

        frankfurt = str(app.query_one("#sum-frankfurt", MetricCard).render())
        assert "24h -20.00%" in frankfurt
        assert "1W +60.00%" in frankfurt


async def test_summary_period_line_survives_privacy_mode() -> None:
    # Percentages don't leak the portfolio size, so they stay visible.
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(), hide_values=True)
        await pilot.pause()

        b3 = str(app.query_one("#sum-b3", MetricCard).render())
        assert "24h +12.24%" in b3
        assert "1W -7.89%" in b3


async def test_summary_period_line_shows_na_without_data() -> None:
    snapshot = _snapshot().model_copy(
        update={"positions": [_pv("SAP.DE", "Frankfurt", 2000.0)]},
    )
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(snapshot, hide_values=False)
        await pilot.pause()

        frankfurt = str(app.query_one("#sum-frankfurt", MetricCard).render())
        assert "24h N/A" in frankfurt
        assert "1W N/A" in frankfurt

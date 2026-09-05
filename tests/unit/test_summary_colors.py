# Tests for the green/red colouring of every change shown on the Summary tab.
#
# Textual renders a widget to a `Content` object: the plain text plus a list of
# `Span`s saying "characters 52-59 are green". So instead of eyeballing the
# terminal, these tests look up the styles covering a piece of text and assert
# the colour is the one the sign calls for.

from datetime import UTC, datetime

from rich.style import Style as RichStyle
from textual.content import Content
from textual.style import Style as TextualStyle

from portfolio.core.models import (
    FixedIncomePosition,
    PortfolioSnapshot,
    PositionValue,
)
from portfolio.ui.tui.app import PortfolioApp
from portfolio.ui.tui.widgets import (
    COLOR_DOWN,
    COLOR_UP,
    MetricCard,
    SummaryPanel,
)


def _styles_over(content: Content, text: str) -> set[str]:
    """Return the styles applied to `text` inside a rendered widget.

    A span covers our text when it starts at or before it and ends at or after
    it. Several spans can overlap (e.g. "bold" plus "green"), hence a set.
    """
    plain = str(content)
    start = plain.index(text)
    end = start + len(text)
    return {
        span.style
        for span in content.spans
        if isinstance(span.style, str) and span.start <= start and span.end >= end
    }


def _card(app: PortfolioApp, card_id: str) -> Content:
    rendered = app.query_one(f"#{card_id}", MetricCard).render()
    assert isinstance(rendered, Content)
    return rendered


def _pv(
    ticker: str,
    exchange: str,
    value_brl: float,
    change_pct: float,
    change_pct_1w: float,
    pnl_pct: float,
) -> PositionValue:
    return PositionValue(
        ticker=ticker, quantity=1, price=value_brl, value_brl=value_brl,
        native_currency="BRL", exchange=exchange, category="Stock",
        change_pct=change_pct, change_pct_1w=change_pct_1w, pnl_pct=pnl_pct,
    )


def _snapshot(*, gaining: bool) -> PortfolioSnapshot:
    """A snapshot whose every change points the same way.

    `gaining=True` makes B3 up on both periods and Frankfurt down, and the
    portfolio totals ahead of every historical value; `gaining=False` mirrors
    it. Testing both directions catches a colour that was hard-coded green.
    """
    sign = 1.0 if gaining else -1.0
    positions = [
        _pv("ITSA4.SA", "B3", 1000.0, sign * 10.0, sign * 20.0, sign * 25.0),
        # Frankfurt moves opposite to B3 over both periods, but both holdings
        # share the same P&L direction so the aggregate P&L is unambiguous.
        _pv("SAP.DE", "Frankfurt", 2000.0, -sign * 10.0, -sign * 20.0, sign * 25.0),
    ]
    # 3000 today vs. 4000 before → every period is down when `gaining` is False.
    past = 2000.0 if gaining else 4000.0
    return PortfolioSnapshot(
        positions=positions,
        total_value=3000.0,
        total_value_24h=past,
        total_value_1w=past,
        total_value_6m=past,
        total_value_12m=past,
        fixed_income=[FixedIncomePosition(name="CDB", amount_brl=1000.0)],
        fixed_income_total=1000.0,
        timestamp=datetime.now(UTC),
    )


async def test_gaining_portfolio_is_green() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(gaining=True), hide_values=False)
        await pilot.pause()

        # Hero: P&L headline and its percentage subtitle.
        pnl = _card(app, "sum-pnl")
        # Cost 2400 (1000 at +25% and 2000 at +25%) against 3000 today.
        assert f"bold {COLOR_UP}" in _styles_over(pnl, "+R$ 600.00")
        assert COLOR_UP in _styles_over(pnl, "+25.00%")

        # Hero: day change headline and subtitle.
        day = _card(app, "sum-day")
        assert f"bold {COLOR_UP}" in _styles_over(day, "+R$ 1,000.00")
        assert COLOR_UP in _styles_over(day, "+50.00%")

        # Performance strip: percentage and the R$ delta beneath it.
        perf = _card(app, "sum-24h")
        assert f"bold {COLOR_UP}" in _styles_over(perf, "+50.00%")
        assert COLOR_UP in _styles_over(perf, "+R$ 1,000")


async def test_losing_portfolio_is_red() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(gaining=False), hide_values=False)
        await pilot.pause()

        pnl = _card(app, "sum-pnl")
        # Cost 4000 (both holdings at -25%) against 3000 today.
        assert f"bold {COLOR_DOWN}" in _styles_over(pnl, "R$ -1,000.00")
        assert COLOR_DOWN in _styles_over(pnl, "-25.00%")

        day = _card(app, "sum-day")
        assert f"bold {COLOR_DOWN}" in _styles_over(day, "R$ -1,000.00")
        assert COLOR_DOWN in _styles_over(day, "-25.00%")

        perf = _card(app, "sum-24h")
        assert f"bold {COLOR_DOWN}" in _styles_over(perf, "-25.00%")
        assert COLOR_DOWN in _styles_over(perf, "R$ -1,000")


async def test_exchange_cards_colour_each_period_separately() -> None:
    # B3 is up and Frankfurt is down in the same snapshot, so one card must
    # come out green and the other red — proof the colour follows the data.
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(gaining=True), hide_values=False)
        await pilot.pause()

        b3 = _card(app, "sum-b3")
        assert COLOR_UP in _styles_over(b3, "+10.00%")
        assert COLOR_UP in _styles_over(b3, "+20.00%")

        frankfurt = _card(app, "sum-frankfurt")
        assert COLOR_DOWN in _styles_over(frankfurt, "-10.00%")
        assert COLOR_DOWN in _styles_over(frankfurt, "-20.00%")


async def test_exchange_period_percentages_are_not_dimmed() -> None:
    # The 24h/1W line is passed through with `description_style=""` so its
    # colours render at full strength; only the "24h"/"1W" labels are dim.
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(gaining=True), hide_values=False)
        await pilot.pause()

        b3 = _card(app, "sum-b3")
        assert "dim" not in _styles_over(b3, "+10.00%")
        assert "dim" in _styles_over(b3, "24h")


async def test_movers_are_coloured_by_direction() -> None:
    app = PortfolioApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(SummaryPanel).update(_snapshot(gaining=True), hide_values=False)
        await pilot.pause()

        gainers = app.query_one("#sum-gainers").render()
        losers = app.query_one("#sum-losers").render()
        assert isinstance(gainers, Content) and isinstance(losers, Content)
        # ITSA4 is +10% (green) and SAP is -10% (red); both appear in each list
        # because there are only two positions, so check the styles by ticker.
        assert COLOR_UP in _styles_over(gainers, "+10.00%")
        assert COLOR_DOWN in _styles_over(losers, "-10.00%")


def test_change_colours_mean_the_same_thing_to_both_renderers() -> None:
    """Guard against a colour one renderer understands and the other ignores.

    This file hands colours to two different engines: Rich `Style` objects for
    the DataTable cells, and Textual markup for the summary cards. They have
    separate colour-name tables — "bright_green" is valid Rich and invalid
    Textual, where it silently rendered as ordinary white text. Asserting on
    span *names* would not catch that, so this asserts the colour each engine
    actually resolves.
    """
    for color, expected in ((COLOR_UP, (0, 230, 118)), (COLOR_DOWN, (255, 0, 0))):
        textual_color = TextualStyle.parse(color).foreground
        assert textual_color is not None
        assert (textual_color.r, textual_color.g, textual_color.b) == expected

        rich_color = RichStyle.parse(color).color
        assert rich_color is not None
        assert rich_color.get_truecolor() == expected


def test_up_colour_is_brighter_than_css_green() -> None:
    # The bug that started this: CSS "green" is #008000, a dark olive that reads
    # as brown-grey beside a full-brightness red. The gain colour must be a
    # green with real luminance behind it.
    up = TextualStyle.parse(COLOR_UP).foreground
    css_green = TextualStyle.parse("green").foreground
    assert up is not None and css_green is not None
    assert up.g > css_green.g

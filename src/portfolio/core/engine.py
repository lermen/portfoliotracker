import asyncio
from datetime import UTC, datetime

import structlog

from portfolio.core.exceptions import PriceFetchError
from portfolio.core.fetcher import fetch_all_prices, fetch_fx_rates
from portfolio.core.models import PortfolioSnapshot, PositionValue
from portfolio.core.reader import read_positions
from portfolio.core.settings import settings

log = structlog.get_logger()


async def run_engine(queue: asyncio.Queue[PortfolioSnapshot]) -> None:
    """Continuously refresh portfolio data and publish snapshots to the queue."""
    log.info("engine_started", interval=settings.refresh_interval_seconds)

    while True:
        try:
            positions = await read_positions(settings.excel_path)
            tickers = [p.ticker for p in positions]
            prices = await fetch_all_prices(tickers)

            unique_currencies = list(
                {data[4] for data in prices.values()}
            )
            fx_rates = await fetch_fx_rates(unique_currencies)

            position_values: list[PositionValue] = []
            for pos in positions:
                price_data = prices.get(pos.ticker)
                if price_data is not None:
                    price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = price_data
                else:
                    price, change_pct, change_pct_1w, market_open, native_currency = 0.0, None, None, True, "USD"
                    day_high = day_low = week_52_high = week_52_low = None
                fx_rate = fx_rates.get(native_currency, 1.0)
                position_values.append(
                    PositionValue(
                        ticker=pos.ticker,
                        quantity=pos.quantity,
                        price=price,
                        native_currency=native_currency,
                        value_brl=pos.quantity * price * fx_rate,
                        change_pct=change_pct,
                        change_pct_1w=change_pct_1w,
                        market_open=market_open,
                        exchange=pos.exchange,
                        category=pos.category,
                        day_high=day_high,
                        day_low=day_low,
                        week_52_high=week_52_high,
                        week_52_low=week_52_low,
                    )
                )

            def _value_before(value_brl: float, change_pct: float | None) -> float:
                if change_pct is None:
                    return value_brl
                return value_brl / (1.0 + change_pct / 100.0)

            snapshot = PortfolioSnapshot(
                positions=position_values,
                total_value=sum(pv.value_brl for pv in position_values),
                total_value_24h=sum(
                    _value_before(pv.value_brl, pv.change_pct)
                    for pv in position_values
                ),
                total_value_1w=sum(
                    _value_before(pv.value_brl, pv.change_pct_1w)
                    for pv in position_values
                ),
                currency="BRL",
                timestamp=datetime.now(UTC),
            )

            await queue.put(snapshot)
            log.info("snapshot_published", total_value=snapshot.total_value)

        except PriceFetchError as exc:
            log.error("price_fetch_failed", error=str(exc))
        except Exception as exc:
            log.error("engine_error", error=str(exc))

        await asyncio.sleep(settings.refresh_interval_seconds)

import asyncio
from datetime import UTC, datetime

import structlog

from portfolio.core.exceptions import PriceFetchError
from portfolio.core.fetcher import fetch_all_prices, fetch_fx_rates
from portfolio.core.models import PortfolioSnapshot, PositionValue
from portfolio.core.reader import read_fixed_income, read_positions
from portfolio.core.settings import settings

log = structlog.get_logger()


async def run_engine(queue: asyncio.Queue[PortfolioSnapshot]) -> None:
    """Continuously refresh portfolio data and publish snapshots to the queue.

    This function is the "producer" in a producer/consumer pattern.
    `asyncio.Queue` is a thread-safe channel:
      - The engine (producer) puts snapshots in with `queue.put(snapshot)`.
      - The UI (consumer) takes them out with `queue.get()`.
    This decouples the two sides — the UI never calls the fetcher directly.

    The function runs in an infinite loop, sleeping between iterations.
    `await asyncio.sleep(...)` yields control back to the event loop so other
    async tasks (like the UI) can run while we wait.
    """
    log.info("engine_started", interval=settings.refresh_interval_seconds)

    while True:
        try:
            # --- Step 1: Read both sheets concurrently from the Excel file ---
            # `asyncio.TaskGroup` is Python 3.11+'s structured concurrency primitive.
            # Both tasks start at the same time; the `async with` block waits for
            # both to finish (or cancels all if one raises).
            async with asyncio.TaskGroup() as tg:
                pos_task = tg.create_task(read_positions(settings.excel_path))
                fi_task = tg.create_task(read_fixed_income(settings.excel_path))
            positions = pos_task.result()
            fixed_income = fi_task.result()

            tickers = [p.ticker for p in positions]

            # --- Step 2: Fetch live prices for all tickers concurrently ---
            prices = await fetch_all_prices(tickers)

            # --- Step 3: Determine which currencies we need FX rates for ---
            # `data[4]` is the native_currency element of each _PriceData tuple.
            # A set comprehension `{...}` automatically deduplicates values.
            unique_currencies = list(
                {data[4] for data in prices.values()}
            )
            fx_rates = await fetch_fx_rates(unique_currencies)

            # --- Step 4: Combine position + price + FX rate into PositionValue ---
            position_values: list[PositionValue] = []
            for pos in positions:
                # `.get(key)` returns None if the key is missing (failed fetch).
                price_data = prices.get(pos.ticker)
                if price_data is not None:
                    # Tuple unpacking: assign each element to its own variable in one line.
                    price, change_pct, change_pct_1w, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = price_data
                else:
                    # Ticker fetch failed — use zero price so it still appears in the UI.
                    price, change_pct, change_pct_1w, market_open, native_currency = 0.0, None, None, True, "USD"
                    day_high = day_low = week_52_high = week_52_low = None

                # Fall back to 1.0 if this currency has no FX rate (i.e. treat as BRL).
                fx_rate = fx_rates.get(native_currency, 1.0)

                position_values.append(
                    PositionValue(
                        ticker=pos.ticker,
                        quantity=pos.quantity,
                        price=price,
                        native_currency=native_currency,
                        # BRL value = shares × price_in_native_currency × rate_to_BRL
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

            # Helper defined inside the loop so it can be used in the generator
            # expressions below. A nested function like this is called a "closure"
            # when it references variables from its enclosing scope.
            def _value_before(value_brl: float, change_pct: float | None) -> float:
                """Back-calculate yesterday's / last-week's value from today's value and the % change.

                If today's value = V and the change = p%, then:
                    V = V_yesterday * (1 + p/100)
                    V_yesterday = V / (1 + p/100)
                """
                if change_pct is None:
                    return value_brl   # no change data → assume same as now
                return value_brl / (1.0 + change_pct / 100.0)

            # --- Step 5: Build and publish the snapshot ---
            fi_total = sum(fi.amount_brl for fi in fixed_income)
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
                fixed_income=fixed_income,
                fixed_income_total=fi_total,
                currency="BRL",
                timestamp=datetime.now(UTC),
            )

            # `await queue.put(snapshot)` hands the snapshot to any waiting consumer.
            # The UI will pick it up the next time it polls the queue.
            await queue.put(snapshot)
            log.info("snapshot_published", total_value=snapshot.total_value)

        except PriceFetchError as exc:
            # A price fetch error is recoverable — log it and try again next cycle.
            log.error("price_fetch_failed", error=str(exc))
        except Exception as exc:
            # Catch-all for any unexpected error so the engine never crashes entirely.
            log.error("engine_error", error=str(exc))

        # Pause before the next refresh. `await` here lets the event loop run other
        # coroutines (like UI updates) while we wait.
        await asyncio.sleep(settings.refresh_interval_seconds)

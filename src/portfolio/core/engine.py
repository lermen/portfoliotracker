# This module is the "engine" — the central coordinator that ties the entire
# backend together. It runs in an infinite loop, orchestrating:
#   1. Reading positions from Excel.
#   2. Fetching live prices for every ticker.
#   3. Fetching the FX rates needed to convert prices to BRL.
#   4. Computing per-position values and P&L.
#   5. Publishing a complete `PortfolioSnapshot` to the queue.
#
# The engine is the "producer" in a producer/consumer pattern:
#   Engine (producer) → asyncio.Queue → UI (consumer)
#
# The UI never calls the fetcher directly — it only reads snapshots from the
# queue. This decoupling means you can add a second UI (e.g. a web dashboard)
# without changing any backend code; it just subscribes to its own queue.
#
# Threading model
# ---------------
# Everything here is async (single-threaded cooperative multitasking). Blocking
# calls (file I/O, yfinance network requests) are offloaded to threads using
# `asyncio.to_thread` inside reader.py and fetcher.py. The engine itself never
# blocks — it only `await`s async functions.

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

    This is the application's main background task. It runs forever (while True)
    inside a Textual `Worker`, meaning Textual starts it in the background and
    it keeps running alongside the UI.

    `asyncio.Queue[PortfolioSnapshot]` is a type-annotated queue — the `[...]`
    tells type checkers (and human readers) exactly what type of object goes in
    and comes out. The queue is created in `app.py` and passed in here.

    The function signature uses `async def` because it uses `await` internally.
    Any function with `async def` must be called with `await` (or started as a
    background task, as Textual does here).
    """
    log.info("engine_started", interval=settings.refresh_interval_seconds)

    while True:
        try:
            # --- Step 1: Read both sheets concurrently from the Excel file ---
            # `asyncio.TaskGroup` is Python 3.11+'s structured concurrency primitive.
            # Both tasks start at the same time — `pos_task` and `fi_task` run in
            # parallel. The `async with` block blocks until both are done (or
            # cancels both if one raises). This is faster than awaiting them one
            # at a time, which would be sequential.
            async with asyncio.TaskGroup() as tg:
                pos_task = tg.create_task(read_positions(settings.excel_path))
                fi_task = tg.create_task(read_fixed_income(settings.excel_path))
            positions = pos_task.result()     # list[Position] from the first sheet
            fixed_income = fi_task.result()   # list[FixedIncomePosition] from FixedIncome sheet

            # List comprehension: extract just the ticker strings we need to fetch prices for.
            tickers = [p.ticker for p in positions]

            # --- Step 2: Fetch live prices for all tickers concurrently ---
            # Returns {ticker: _PriceData} — all N tickers fetched in parallel.
            prices = await fetch_all_prices(tickers)

            # --- Step 3: Determine which currencies we need FX rates for ---
            # `data[6]` is the native_currency element of each _PriceData tuple (index 6).
            # A set comprehension `{...}` automatically deduplicates: if 5 tickers are all
            # in USD, the set still has only one "USD" entry.
            unique_currencies = list(
                {data[6] for data in prices.values()}
            )
            # Returns {currency: brl_rate}, e.g. {"USD": 5.12, "EUR": 5.63, "BRL": 1.0}
            fx_rates = await fetch_fx_rates(unique_currencies)

            # --- Step 4: Combine position + price + FX rate into PositionValue ---
            position_values: list[PositionValue] = []
            for pos in positions:
                # `dict.get(key)` returns None if the key is missing, instead of raising
                # a KeyError. We use this because a ticker's fetch might have failed.
                price_data = prices.get(pos.ticker)
                if price_data is not None:
                    # Tuple unpacking: assign each element of the tuple to its own variable
                    # in a single line. The order matches the `_PriceData` type alias.
                    price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency, day_high, day_low, week_52_high, week_52_low = price_data
                else:
                    # Ticker fetch failed — use zero price so the position still appears in
                    # the UI rather than disappearing silently.
                    price, change_pct, change_pct_1w, change_pct_6m, change_pct_12m, market_open, native_currency = 0.0, None, None, None, None, True, "USD"
                    day_high = day_low = week_52_high = week_52_low = None

                # Fall back to 1.0 if this currency has no FX rate (treat as BRL).
                fx_rate = fx_rates.get(native_currency, 1.0)

                # P&L % = (current price − average purchase price) / avg × 100.
                #
                # Currency note: for most assets, `avg_price_native` is in the asset's
                # own currency (e.g. EUR for a Frankfurt-listed ETF, USD for a US stock),
                # so we compare it directly against `price` which is also in that currency.
                # Exception: crypto avg prices are entered in BRL in the spreadsheet
                # (because the user bought on a BRL exchange), so we first convert the
                # live USD price to BRL using the FX rate before comparing.
                pnl_pct: float | None = None
                if pos.avg_price_native is not None and pos.avg_price_native > 0:
                    if pos.category.lower() == "crypto":
                        price_for_pnl = price * fx_rate   # convert USD → BRL
                    else:
                        price_for_pnl = price             # already in native currency
                    pnl_pct = (price_for_pnl - pos.avg_price_native) / pos.avg_price_native * 100.0

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
                        change_pct_6m=change_pct_6m,
                        change_pct_12m=change_pct_12m,
                        avg_price_native=pos.avg_price_native,
                        pnl_pct=pnl_pct,
                        market_open=market_open,
                        exchange=pos.exchange,
                        category=pos.category,
                        day_high=day_high,
                        day_low=day_low,
                        week_52_high=week_52_high,
                        week_52_low=week_52_low,
                    )
                )

            # `_value_before` is a closure — a helper function defined *inside* another
            # function. It can access variables from the enclosing scope (like `pv`
            # in the generator expressions below). Defining it here keeps it close to
            # its only use and avoids polluting the module namespace.
            def _value_before(value_brl: float, change_pct: float | None) -> float:
                """Back-calculate what the portfolio was worth before the given % change.

                If today's value is V and the percentage change was p%, then:
                    V = V_before × (1 + p/100)
                    V_before = V / (1 + p/100)

                Returns `value_brl` unchanged when `change_pct` is None (no data).
                """
                if change_pct is None:
                    return value_brl
                return value_brl / (1.0 + change_pct / 100.0)

            # --- Step 5: Build and publish the snapshot ---
            fi_total = sum(fi.amount_brl for fi in fixed_income)

            # Generator expressions `(expr for item in iterable)` are like list
            # comprehensions but they don't build the whole list in memory — they
            # produce values one at a time. `sum(...)` consumes them directly.
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
                total_value_6m=sum(
                    _value_before(pv.value_brl, pv.change_pct_6m)
                    for pv in position_values
                ),
                total_value_12m=sum(
                    _value_before(pv.value_brl, pv.change_pct_12m)
                    for pv in position_values
                ),
                fixed_income=fixed_income,
                fixed_income_total=fi_total,
                currency="BRL",
                timestamp=datetime.now(UTC),   # UTC timestamp for timezone-safe comparisons
            )

            # `await queue.put(snapshot)` hands the snapshot to any waiting consumer.
            # The UI will pick it up the next time it polls the queue (every 1 second).
            await queue.put(snapshot)
            log.info("snapshot_published", total_value=snapshot.total_value)

        except PriceFetchError as exc:
            # A price fetch error is recoverable — log it and try again next cycle.
            # We catch this specific exception type before the broad `Exception` below
            # so we can handle it differently (e.g. a different log level or recovery).
            log.error("price_fetch_failed", error=str(exc))
        except Exception as exc:
            # Catch-all for any unexpected error (network outage, API change, etc.)
            # so the engine never crashes entirely. The next iteration will retry.
            log.error("engine_error", error=str(exc))

        # Pause before the next refresh. `await asyncio.sleep(N)` suspends this
        # coroutine for N seconds while letting the event loop run other tasks
        # (like UI rendering) in the meantime. Compare to `time.sleep(N)` which
        # would freeze the entire program.
        await asyncio.sleep(settings.refresh_interval_seconds)

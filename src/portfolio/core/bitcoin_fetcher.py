# This module fetches Bitcoin-specific metrics from several free public APIs and
# publishes them as `BitcoinMetrics` snapshots to an `asyncio.Queue`.
#
# It is a self-contained "mini engine" that runs alongside the main portfolio
# engine. Both are producers; the TUI app is the consumer of both queues.
#
# APIs used (all free, no API key required):
#   - alternative.me/fng/          → Fear & Greed Index
#   - mempool.space/api/           → current block height → halving countdown
#   - fapi.binance.com/fapi/v1/    → perpetual funding rate
#   - community-api.coinmetrics.io → MVRV ratio
#   - api.binance.com/api/v3/      → daily candles → Mayer's Multiple + BTC price
#
# Design principle: every individual fetch has a `_*_safe` wrapper that catches
# all exceptions and returns None (or a tuple of Nones). This means a single API
# outage never aborts the entire metrics update — the panel shows "N/A" for the
# affected card while all other cards still refresh normally.

import asyncio
from datetime import UTC, datetime, timedelta

# `httpx` is an async-native HTTP client. Unlike `requests` (which is synchronous
# and would block the event loop), `httpx.AsyncClient` integrates with asyncio so
# we can `await` each request without freezing other tasks.
import httpx
import structlog

from portfolio.core.models import BitcoinMetrics

log = structlog.get_logger()

# Module-level constants for the API URLs. Storing them here (not inside functions)
# makes them easy to find and update in one place if an API URL changes.
_FEAR_GREED_URL = "https://api.alternative.me/fng/"
_MEMPOOL_HEIGHT_URL = "https://mempool.space/api/blocks/tip/height"
_BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

# Bitcoin halves its block reward every 210,000 blocks (~4 years).
# This is a protocol-level constant — it will never change.
_HALVING_INTERVAL = 210_000   # the underscore is just a visual separator, like 210,000


async def _fetch_fear_greed(client: httpx.AsyncClient) -> tuple[int, str]:
    """Fetch the Fear & Greed Index (0–100) and its text label.

    The index is published once per day by alternative.me. A score near 0
    means "Extreme Fear" (historically a buying signal); near 100 means
    "Extreme Greed" (historically overheated).

    `client: httpx.AsyncClient` — we accept the client as a parameter so all
    fetch functions in this module can share one connection pool, which is more
    efficient than each creating its own client.
    """
    resp = await client.get(_FEAR_GREED_URL, timeout=10.0)
    # `.raise_for_status()` raises `httpx.HTTPStatusError` for 4xx/5xx HTTP errors.
    # Without this, a server returning "404 Not Found" would look like success.
    resp.raise_for_status()
    entry = resp.json()["data"][0]   # the API returns a list; we want the first (latest) entry
    return int(entry["value"]), entry["value_classification"]


async def _fetch_fear_greed_safe(
    client: httpx.AsyncClient,
) -> tuple[int | None, str | None]:
    """Return (value, label) or (None, None) on any failure."""
    try:
        return await _fetch_fear_greed(client)
    except Exception as exc:
        log.warning("fear_greed_fetch_failed", error=str(exc))
        return None, None


async def _fetch_halving(client: httpx.AsyncClient) -> tuple[int, datetime]:
    """Return (blocks_remaining, estimated_datetime) for the next halving.

    Strategy:
    1. Fetch the current chain tip height (the index of the latest block).
    2. Integer-divide by 210,000 to find which halving epoch we're in.
    3. The next halving happens at the start of the next epoch.
    4. Each block averages ~10 minutes, so multiply the remaining blocks by 10
       minutes to get a rough calendar estimate.
    """
    resp = await client.get(_MEMPOOL_HEIGHT_URL, timeout=10.0)
    resp.raise_for_status()
    # The response body is a bare integer, e.g. "897654" — not wrapped in JSON.
    current_height = int(resp.text.strip())

    # Integer division `//` discards the remainder.
    # Example: 897,654 // 210,000 = 4 (we are in epoch 4, i.e. after the 4th halving).
    # Adding 1 gives the next epoch; multiplying by 210,000 gives the block it starts at.
    next_halving = ((current_height // _HALVING_INTERVAL) + 1) * _HALVING_INTERVAL
    blocks_remaining = next_halving - current_height

    # `timedelta(minutes=N)` is a duration. `datetime.now(UTC)` is the current UTC time.
    # Adding them gives an estimated future datetime. This is rough (±weeks) because
    # block times vary around the 10-minute average.
    estimated_date = datetime.now(UTC) + timedelta(minutes=blocks_remaining * 10)
    return blocks_remaining, estimated_date


async def _fetch_halving_safe(
    client: httpx.AsyncClient,
) -> tuple[int | None, datetime | None]:
    """Return halving data or (None, None) on failure."""
    try:
        return await _fetch_halving(client)
    except Exception as exc:
        log.warning("halving_fetch_failed", error=str(exc))
        return None, None


async def _fetch_funding_rate(client: httpx.AsyncClient) -> float:
    """Return the latest BTC perpetual funding rate as a percentage per 8 hours.

    Binance perpetuals exchange a payment every 8 hours between long and short
    position holders. A positive rate means longs pay shorts (the market is
    positioned bullishly). A negative rate means shorts pay longs (bearish).

    The API returns a decimal string like "0.00010000"; multiplying by 100
    converts it to a percentage: 0.00010000 × 100 = 0.01%.
    """
    resp = await client.get(_BINANCE_FUNDING_URL, timeout=10.0)
    resp.raise_for_status()
    # `.json()` parses the response body as JSON and returns a Python dict.
    return float(resp.json()["lastFundingRate"]) * 100.0


async def _fetch_funding_rate_safe(client: httpx.AsyncClient) -> float | None:
    """Return the funding rate percentage or None on failure."""
    try:
        return await _fetch_funding_rate(client)
    except Exception as exc:
        log.warning("funding_rate_fetch_failed", error=str(exc))
        return None


async def _fetch_mvrv(client: httpx.AsyncClient) -> float:
    """Return the current MVRV ratio from the CoinMetrics Community API.

    MVRV = Market Value / Realized Value.
    - Market Value  = current price × circulating supply
      (what the market collectively says all BTC is worth right now)
    - Realized Value = each coin valued at the price it last moved on-chain
      (the aggregate cost basis of all holders — what they collectively paid)

    When MVRV < 1, coins are worth less on the market than holders paid for them
    on average — historically a very strong buy signal. When MVRV > 3.5, past
    cycles have often peaked. The CoinMetrics community tier is free, no API key.
    """
    resp = await client.get(
        _COINMETRICS_URL,
        # `params` is a dict of query parameters appended to the URL.
        # This becomes: ?assets=btc&metrics=CapMVRVCur&page_size=1
        params={"assets": "btc", "metrics": "CapMVRVCur", "page_size": 1},
        timeout=15.0,
    )
    resp.raise_for_status()
    # `data[0]["CapMVRVCur"]` is a string like "2.45"; `float()` converts it to a number.
    return float(resp.json()["data"][0]["CapMVRVCur"])


async def _fetch_mvrv_safe(client: httpx.AsyncClient) -> float | None:
    """Return MVRV ratio or None on failure."""
    try:
        return await _fetch_mvrv(client)
    except Exception as exc:
        log.warning("mvrv_fetch_failed", error=str(exc))
        return None


async def _fetch_mayers_multiple(client: httpx.AsyncClient) -> tuple[float, float, float]:
    """Return (mayers_multiple, ma200_price, current_price) from 200 daily closes.

    Mayer's Multiple = current price / 200-day moving average (MA).
    The 200-day MA is one of the most-watched trend indicators in all markets.
    - Below 0.8: Mayer's suggested accumulation zone
    - Above 2.4: Mayer's suggested caution threshold

    We fetch 200 daily OHLCV candles from Binance. Each candle is a list:
        [open_time, open, high, low, close, volume, ...]
    Index 4 is the closing price for that day, as a string (e.g. "67234.50").

    We also return `current_price` (the most recent close) so the caller can
    populate the BTC Price card without making an extra API call.
    """
    resp = await client.get(
        _BINANCE_KLINES_URL,
        params={"symbol": "BTCUSDT", "interval": "1d", "limit": 200},
        timeout=15.0,
    )
    resp.raise_for_status()
    klines = resp.json()   # list of candles, each candle is a list of values

    # List comprehension: extract the close price (element at index 4) from each candle.
    # `float(k[4])` converts the string "67234.50" to the number 67234.5.
    closes = [float(k[4]) for k in klines]

    # `sum(closes) / len(closes)` is the arithmetic mean — the 200-day moving average.
    ma200 = sum(closes) / len(closes)

    # `closes[-1]` accesses the last element (negative indices count from the end).
    # The last candle is the most recent daily close.
    current_price = closes[-1]

    return current_price / ma200, ma200, current_price


async def _fetch_mayers_safe(
    client: httpx.AsyncClient,
) -> tuple[float | None, float | None, float | None]:
    """Return (mayers_multiple, ma200, btc_price_usd) or (None, None, None) on failure."""
    try:
        multiple, ma200, price = await _fetch_mayers_multiple(client)
        return multiple, ma200, price
    except Exception as exc:
        log.warning("mayers_fetch_failed", error=str(exc))
        return None, None, None


async def fetch_bitcoin_metrics() -> BitcoinMetrics:
    """Fetch all Bitcoin metrics concurrently and return a single model.

    All five API calls launch at the same time inside one `asyncio.TaskGroup`.
    The block exits only after every task finishes (or one fails fatally).

    Using a shared `httpx.AsyncClient` for all requests allows HTTP connection
    reuse (keep-alive) across the five calls, which is more efficient than
    creating a separate client per request. The `async with` block ensures the
    client's connection pool is properly closed when done.
    """
    # `async with A() as a, B() as b:` is shorthand for two nested `async with` blocks.
    # Here we open both the HTTP client and the TaskGroup at the same time.
    async with httpx.AsyncClient() as client, asyncio.TaskGroup() as tg:
        fg_task = tg.create_task(_fetch_fear_greed_safe(client))
        halving_task = tg.create_task(_fetch_halving_safe(client))
        funding_task = tg.create_task(_fetch_funding_rate_safe(client))
        mvrv_task = tg.create_task(_fetch_mvrv_safe(client))
        mayers_task = tg.create_task(_fetch_mayers_safe(client))

    # All tasks are guaranteed done here. `.result()` returns the value or
    # re-raises any exception the task raised (though our "safe" wrappers
    # swallow exceptions and return Nones instead).
    fg_value, fg_label = fg_task.result()
    blocks_remaining, estimated_date = halving_task.result()
    funding_rate = funding_task.result()
    mvrv_ratio = mvrv_task.result()
    mayers_multiple, mayers_ma200, btc_price_usd = mayers_task.result()

    return BitcoinMetrics(
        fear_greed_value=fg_value,
        fear_greed_label=fg_label,
        halving_blocks_remaining=blocks_remaining,
        halving_estimated_date=estimated_date,
        funding_rate=funding_rate,
        mvrv_ratio=mvrv_ratio,
        mayers_multiple=mayers_multiple,
        mayers_ma200=mayers_ma200,
        btc_price_usd=btc_price_usd,
        timestamp=datetime.now(UTC),
    )


async def run_bitcoin_engine(
    queue: asyncio.Queue[BitcoinMetrics],
    interval: int = 300,
) -> None:
    """Continuously fetch Bitcoin metrics and publish them to the queue.

    This mirrors the pattern in `engine.py` — it is the producer side of a
    separate producer/consumer channel dedicated to Bitcoin metrics.

    Bitcoin metrics change slowly (Fear & Greed updates daily, funding rate
    every 8 hours, block height every ~10 minutes), so a 5-minute (300 second)
    refresh interval is more than sufficient and avoids hammering free APIs.

    `interval: int = 300` is a parameter with a default value. The caller can
    override it: `run_bitcoin_engine(queue, interval=60)` would refresh every minute.
    """
    log.info("bitcoin_engine_started", interval=interval)
    while True:
        try:
            metrics = await fetch_bitcoin_metrics()
            await queue.put(metrics)
            log.info("bitcoin_metrics_published", fear_greed=metrics.fear_greed_value)
        except Exception as exc:
            # Log the error but keep the loop running — a temporary API outage
            # should not stop the entire Bitcoin metrics panel permanently.
            log.error("bitcoin_engine_error", error=str(exc))
        await asyncio.sleep(interval)

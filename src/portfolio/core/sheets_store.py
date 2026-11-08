# This module stores the portfolio in a Google Sheet instead of a local file.
# It implements the same `PortfolioStore` contract as `ExcelStore` (see store.py),
# so the engine, importer and TUI work unchanged — they never learn which one
# they're talking to.
#
# The sheet has the same layout as the Excel workbook: positions on the first tab
# (Ticker, Quantity, Exchange, Category, Avg Price), fixed income on a tab named
# "FixedIncome" (Name, Amount). Row parsing is shared with the Excel reader via
# `row_to_position` / `row_to_fixed_income`, so both backends read identically.
#
# Threading model
# ---------------
# `gspread` is a *synchronous* library: each call makes an HTTP request and waits
# for Google to answer. Exactly like openpyxl in reader.py, every public method
# here is `async` and runs the blocking work in a background thread with
# `asyncio.to_thread`, so the TUI never freezes while Google responds.
#
# Authentication
# --------------
# The app logs in as a *service account* (a robot Google account) using the JSON
# key file at `GSHEETS_CREDENTIALS_PATH`. The robot can only see sheets that were
# explicitly shared with its email address.

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import gspread
import structlog
from google.auth.exceptions import GoogleAuthError
from gspread.utils import ValueRenderOption

from portfolio.core.exceptions import (
    DuplicateTickerError,
    PositionNotFoundError,
    StorageError,
)
from portfolio.core.models import FixedIncomePosition, Position
from portfolio.core.reader import row_to_fixed_income, row_to_position

log = structlog.get_logger()

# Column numbers in the positions tab. gspread, like openpyxl, is 1-indexed.
_COL_TICKER = 1     # A
_COL_QUANTITY = 2   # B
_COL_AVG_PRICE = 5  # E

_FIRST_DATA_ROW = 2  # row 1 is the header
_FIXED_INCOME_TAB = "FixedIncome"
_FI_HEADER = ("Name", "Amount")
_COL_FI_NAME = 1    # A
_COL_FI_AMOUNT = 2  # B

# A `TypeVar` is a placeholder for "some type, decided by the caller". In
# `_call` below it says: whatever type `fn` returns, `_call` returns the same.
T = TypeVar("T")


class GoogleSheetsStore:
    """The portfolio in a Google Sheet, accessed through the Sheets API."""

    def __init__(self, spreadsheet_id: str, credentials_path: Path) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.description = f"Google Sheet {spreadsheet_id}"
        # Reads the key file and prepares an authenticated client. This does not
        # contact Google yet — the first request happens on the first read.
        self._client = gspread.service_account(filename=str(credentials_path))

    # --- blocking helpers (run inside asyncio.to_thread) -----------------------

    def _call(self, fn: Callable[[], T]) -> T:
        """Run `fn`, translating Google/network failures into `StorageError`.

        Our own "expected" errors (unknown ticker, duplicate, bad value) pass
        through untouched so the UI can show them as-is. Everything else gets a
        message that says what to do about it.
        """
        try:
            return fn()
        except (PositionNotFoundError, DuplicateTickerError, ValueError):
            raise
        except gspread.exceptions.SpreadsheetNotFound as exc:
            raise StorageError(
                f"Sheet {self.spreadsheet_id} not found — check "
                "GSHEETS_SPREADSHEET_ID and that it's shared with the "
                f"service account ({self._client_email()})"
            ) from exc
        except gspread.exceptions.APIError as exc:
            status = exc.response.status_code
            if status == 403:
                hint = f"share the sheet with {self._client_email()} as Editor"
            elif status == 400 and "Office file" in str(exc):
                hint = "the file is still an .xlsx — use File → Save as Google Sheets"
            elif status == 429:
                hint = "Google API rate limit reached — try again in a minute"
            else:
                hint = str(exc)
            raise StorageError(f"Google Sheets error {status}: {hint}") from exc
        except GoogleAuthError as exc:
            raise StorageError(f"Google login failed: {exc}") from exc
        except Exception as exc:
            # Anything else: network down, malformed cell values, etc.
            raise StorageError(f"Google Sheets request failed: {exc}") from exc

    def _client_email(self) -> str:
        # `getattr(obj, name, default)` reads an attribute that may not exist.
        email = getattr(self._client.http_client.auth, "service_account_email", "")
        return str(email) or "the service account"

    def _positions_tab(self) -> gspread.Worksheet:
        # Index 0 = the first tab, matching the Excel reader's rule.
        return self._client.open_by_key(self.spreadsheet_id).get_worksheet(0)

    def _rows(self, ws: gspread.Worksheet) -> list[list[object]]:
        """All data rows (header skipped), with numbers as real numbers.

        `UNFORMATTED_VALUE` asks Google for the raw value: 9105 rather than the
        display text "9,105", which would not convert to a float.
        """
        rows: list[list[object]] = ws.get_values(
            value_render_option=ValueRenderOption.unformatted
        )
        return rows[_FIRST_DATA_ROW - 1 :]

    def _find_row(self, ws: gspread.Worksheet, key: str) -> int | None:
        """Return the 1-based row number holding `key` in column A, or None.

        Column A is the ticker on the positions tab and the name on the
        FixedIncome tab, so the same lookup serves both.

        Called fresh before every edit — never from a cached copy — so if
        another computer added or removed rows in the meantime, we still edit
        the right one.
        """
        target = key.strip()
        tickers = ws.col_values(_COL_TICKER)
        # `enumerate(seq, start=1)` yields (1, first), (2, second), ... so the
        # counter matches the sheet's 1-based row numbers.
        for row, value in enumerate(tickers, start=1):
            if row >= _FIRST_DATA_ROW and str(value or "").strip() == target:
                return row
        return None

    def _require_row(
        self, ws: gspread.Worksheet, key: str, label: str = "Ticker"
    ) -> int:
        row = self._find_row(ws, key)
        if row is None:
            raise PositionNotFoundError(f"{label} {key!r} not found in the sheet")
        return row

    def _last_row(self, ws: gspread.Worksheet) -> int:
        """Return the last row with a non-empty column A (1 if only the header)."""
        keys = ws.col_values(_COL_TICKER)
        return max(
            (i for i, v in enumerate(keys, start=1) if v and str(v).strip()),
            default=_FIRST_DATA_ROW - 1,
        )

    def _read_positions(self) -> list[Position]:
        rows = self._rows(self._positions_tab())
        positions = [p for row in rows if (p := row_to_position(row)) is not None]
        log.info("gsheets_positions_read", count=len(positions))
        return positions

    def _read_fixed_income(self) -> list[FixedIncomePosition]:
        sheet = self._client.open_by_key(self.spreadsheet_id)
        try:
            ws = sheet.worksheet(_FIXED_INCOME_TAB)
        except gspread.exceptions.WorksheetNotFound:
            log.info("gsheets_fixed_income_tab_missing")
            return []  # same rule as Excel: no tab means no fixed income
        items = [
            i for row in self._rows(ws) if (i := row_to_fixed_income(row)) is not None
        ]
        log.info("gsheets_fixed_income_read", count=len(items))
        return items

    def _set_cell(self, ticker: str, column: int, value: float) -> None:
        ws = self._positions_tab()
        ws.update_cell(self._require_row(ws, ticker), column, value)

    def _add_position(self, position: Position) -> None:
        ws = self._positions_tab()
        if self._find_row(ws, position.ticker) is not None:
            raise DuplicateTickerError(
                f"Ticker {position.ticker!r} already exists in the sheet"
            )
        # Insert right after the last non-empty ticker, mirroring the Excel
        # writer. `insert_row` also grows the sheet if it has no spare rows.
        last = self._last_row(ws)
        values: list[str | float] = [
            position.ticker,
            position.quantity,
            position.exchange,
            position.category,
        ]
        if position.avg_price_native is not None:
            values.append(position.avg_price_native)
        ws.insert_row(values, index=last + 1)

    def _remove_position(self, ticker: str) -> None:
        ws = self._positions_tab()
        ws.delete_rows(self._require_row(ws, ticker))

    def _fixed_income_tab(self) -> gspread.Worksheet:
        """Return the FixedIncome tab, creating it (with a header) if missing.

        Only the write paths use this. Reading still treats a missing tab as
        "no fixed income" rather than creating one as a side effect.
        """
        sheet = self._client.open_by_key(self.spreadsheet_id)
        try:
            return sheet.worksheet(_FIXED_INCOME_TAB)
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet(title=_FIXED_INCOME_TAB, rows=100, cols=2)
            ws.insert_row(list(_FI_HEADER), index=1)
            log.info("gsheets_fixed_income_tab_created")
            return ws

    def _update_fixed_income(self, name: str, item: FixedIncomePosition) -> None:
        ws = self._fixed_income_tab()
        row = self._require_row(ws, name, label="Fixed income")
        # Renaming onto a name some *other* row already uses would make the two
        # rows impossible to tell apart, so refuse it (same rule as Excel).
        if self._find_row(ws, item.name) not in (None, row):
            raise DuplicateTickerError(
                f"Fixed income {item.name!r} already exists in the sheet"
            )
        ws.update_cell(row, _COL_FI_NAME, item.name)
        ws.update_cell(row, _COL_FI_AMOUNT, item.amount_brl)

    def _add_fixed_income(self, item: FixedIncomePosition) -> None:
        ws = self._fixed_income_tab()
        if self._find_row(ws, item.name) is not None:
            raise DuplicateTickerError(
                f"Fixed income {item.name!r} already exists in the sheet"
            )
        ws.insert_row([item.name, item.amount_brl], index=self._last_row(ws) + 1)

    def _remove_fixed_income(self, name: str) -> None:
        ws = self._fixed_income_tab()
        ws.delete_rows(self._require_row(ws, name, label="Fixed income"))

    # --- async public API (the PortfolioStore contract) ------------------------
    #
    # `lambda: ...` creates a tiny unnamed function. `_call` needs a function it
    # can run later (inside the thread), not the result of running it now.

    async def read_positions(self) -> list[Position]:
        return await asyncio.to_thread(self._call, self._read_positions)

    async def read_fixed_income(self) -> list[FixedIncomePosition]:
        return await asyncio.to_thread(self._call, self._read_fixed_income)

    async def set_quantity(self, ticker: str, quantity: float) -> None:
        if quantity <= 0:
            raise ValueError(f"Quantity must be greater than 0, got {quantity}")
        await asyncio.to_thread(
            self._call, lambda: self._set_cell(ticker, _COL_QUANTITY, quantity)
        )
        log.info("quantity_updated", ticker=ticker, quantity=quantity)

    async def set_avg_price(self, ticker: str, avg_price: float) -> None:
        if avg_price <= 0:
            raise ValueError(f"Average price must be greater than 0, got {avg_price}")
        await asyncio.to_thread(
            self._call, lambda: self._set_cell(ticker, _COL_AVG_PRICE, avg_price)
        )
        log.info("avg_price_updated", ticker=ticker, avg_price=avg_price)

    async def add_position(self, position: Position) -> None:
        await asyncio.to_thread(self._call, lambda: self._add_position(position))
        log.info("position_added", ticker=position.ticker, quantity=position.quantity)

    async def remove_position(self, ticker: str) -> None:
        await asyncio.to_thread(self._call, lambda: self._remove_position(ticker))
        log.info("position_removed", ticker=ticker)

    async def update_fixed_income(self, name: str, item: FixedIncomePosition) -> None:
        await asyncio.to_thread(
            self._call, lambda: self._update_fixed_income(name, item)
        )
        log.info("fixed_income_updated", name=name, new_name=item.name)

    async def add_fixed_income(self, item: FixedIncomePosition) -> None:
        await asyncio.to_thread(self._call, lambda: self._add_fixed_income(item))
        log.info("fixed_income_added", name=item.name, amount=item.amount_brl)

    async def remove_fixed_income(self, name: str) -> None:
        await asyncio.to_thread(self._call, lambda: self._remove_fixed_income(name))
        log.info("fixed_income_removed", name=name)

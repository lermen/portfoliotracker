# This module is responsible for reading the Excel spreadsheet and turning its
# rows into typed Python objects (`Position` and `FixedIncomePosition`).
#
# It is the very first step in the data pipeline:
#   [Excel file] → reader.py → [list[Position]] → fetcher.py → engine.py → UI
#
# Design note: the public functions (`read_positions`, `read_fixed_income`) are
# async, but the actual file I/O happens in synchronous helpers (`_parse_excel`,
# `_parse_fixed_income`). This is intentional — openpyxl is a blocking library,
# so we run it in a background thread with `asyncio.to_thread` to avoid freezing
# the event loop. See `read_positions` for the full explanation.

# `asyncio` is Python's built-in library for writing concurrent code using the
# async/await syntax. "Concurrent" means multiple things can be in progress at
# the same time, even though Python typically runs one thread at a time.
import asyncio
from collections.abc import Sequence
from pathlib import Path

# `openpyxl` is a third-party library for reading and writing Excel (.xlsx) files.
# It lets us open a workbook, iterate over sheets, and read individual cell values.
import openpyxl

# `structlog` is a structured logging library. Instead of plain text log lines,
# it emits key=value pairs that are easy to filter and machine-parse in production.
import structlog

from portfolio.core.exceptions import ExcelParseError
from portfolio.core.models import FixedIncomePosition, Position

# Get a logger bound to this module. The `log` variable is used throughout the
# file to emit log events. Structured loggers let you attach arbitrary context:
#   log.info("event_name", key=value, another_key=value)
log = structlog.get_logger()


# --- row helpers shared by every storage backend --------------------------------
#
# Excel (openpyxl) and Google Sheets (gspread) both hand us a sheet as a list of
# rows, where each row is a sequence of cell values. They differ in one detail:
# openpyxl reports an empty cell as `None`, while gspread reports it as `""`.
# These helpers accept either, so both backends turn rows into models the same way.
#
# `Sequence[object]` is the type of "anything indexable with a length" — a tuple
# (what openpyxl gives) or a list (what gspread gives) both qualify. `object`
# means each cell can be any value: str, int, float, None...


def _is_blank(value: object) -> bool:
    """True for an empty cell, whichever backend it came from."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _cell(row: Sequence[object], index: int) -> object:
    """Return the cell at `index`, or None if the row is shorter than that.

    Rows can be short: gspread drops trailing empty cells, so a row with no Avg
    Price arrives with 4 values instead of 5.
    """
    return row[index] if index < len(row) else None


def _to_float(value: object) -> float:
    """Convert a numeric cell to float.

    `float()` accepts numbers and numeric strings; anything else (e.g. a date
    cell) raises `ValueError`, which the backend wraps in its own error type.
    """
    if isinstance(value, (int, float, str)):
        return float(value)
    raise ValueError(f"Expected a number, got {value!r}")


def row_to_position(row: Sequence[object]) -> Position | None:
    """Turn one positions-sheet row into a `Position`, or None to skip the row.

    Columns: A Ticker, B Quantity, C Exchange, D Category, E Avg Price.
    Rows with a blank ticker or quantity are skipped — sheets often contain
    trailing empty rows.
    """
    ticker, quantity = _cell(row, 0), _cell(row, 1)
    if _is_blank(ticker) or _is_blank(quantity):
        return None

    exchange, category, avg = _cell(row, 2), _cell(row, 3), _cell(row, 4)
    # Pydantic validates each field when `Position(...)` is called.
    # If `quantity` is <= 0, Pydantic raises a `ValidationError` automatically
    # because of the `Field(gt=0)` constraint in the model.
    return Position(
        ticker=str(ticker).strip(),
        quantity=_to_float(quantity),
        exchange="" if _is_blank(exchange) else str(exchange).strip(),
        category="" if _is_blank(category) else str(category).strip(),
        avg_price_native=None if _is_blank(avg) else _to_float(avg),
    )


def row_to_fixed_income(row: Sequence[object]) -> FixedIncomePosition | None:
    """Turn one FixedIncome-sheet row (A Name, B Amount) into a model, or None."""
    name, amount = _cell(row, 0), _cell(row, 1)
    if _is_blank(name) or _is_blank(amount):
        return None
    return FixedIncomePosition(name=str(name).strip(), amount_brl=_to_float(amount))


def _parse_excel(path: Path) -> list[Position]:
    """Open the first sheet of the Excel file and parse each row into a `Position`.

    Expected spreadsheet format (row 1 is the header, ignored):
      A: Ticker       — trading symbol, e.g. "AAPL" or "BTC-USD"
      B: Quantity     — number of shares/units (must be positive)
      C: Exchange     — optional display label, e.g. "NYSE", "B3"
      D: Category     — optional category, e.g. "Stocks", "Crypto", "FII"
      E: Avg Price    — optional average purchase price in native currency

    The leading underscore in `_parse_excel` is a Python convention signalling
    "private / internal use". Nothing stops external code from calling it, but
    the underscore communicates that it is an implementation detail not meant
    to be part of the public API.
    """
    try:
        # `read_only=True` streams the file row by row without loading it all into
        # memory at once — important for large files.
        # `data_only=True` returns the cached cell *values* instead of Excel formulas.
        # Without it, a cell containing `=A1*B1` would return the formula string,
        # not the computed number.
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

        # Use the first sheet by position, not `wb.active`. `active` reflects
        # whichever tab was open when the file was last saved in Excel — if the
        # user saved while on the FixedIncome tab, `active` would point there
        # instead of the positions sheet.
        if not wb.worksheets:
            raise ExcelParseError("Workbook has no sheets")
        ws = wb.worksheets[0]

        positions: list[Position] = []

        # `iter_rows(min_row=2)` skips the header row (row 1) and yields each
        # subsequent row as a tuple of cell values.
        # `values_only=True` gives us the raw Python values rather than Cell objects.
        for row in ws.iter_rows(min_row=2, values_only=True):
            # `:=` is the "walrus operator": it assigns and tests in one step.
            # Blank rows come back as None and are skipped.
            if (position := row_to_position(row)) is not None:
                positions.append(position)

        wb.close()
        log.info("excel_parsed", path=str(path), count=len(positions))
        return positions

    except ExcelParseError:
        # Re-raise our own exception type unchanged — we don't want to accidentally
        # wrap an `ExcelParseError` inside another `ExcelParseError` in the clause below.
        raise
    except Exception as exc:
        # Catch any other unexpected error and wrap it in our own type.
        # `raise ... from exc` preserves the original traceback as the "cause",
        # which is invaluable when debugging — it shows both errors in the output.
        raise ExcelParseError(f"Failed to parse {path}: {exc}") from exc


def _parse_fixed_income(path: Path) -> list[FixedIncomePosition]:
    """Parse the 'FixedIncome' sheet from the Excel file.

    The sheet is optional — if it doesn't exist, an empty list is returned so
    the rest of the app can run without modification.

    Expected columns (row 1 is the header):
      A: Name     — a descriptive label for the investment
      B: Amount   — the current BRL value of the position
    """
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

        # `wb.sheetnames` is a list of sheet name strings in the workbook.
        # The `in` operator checks for membership — it reads like plain English:
        # "if 'FixedIncome' is not in the list of sheet names".
        if "FixedIncome" not in wb.sheetnames:
            wb.close()
            log.info("fixed_income_sheet_missing", path=str(path))
            return []   # empty list is always safe to iterate over, unlike None

        ws = wb["FixedIncome"]   # access a sheet by name using dictionary-style syntax
        items: list[FixedIncomePosition] = []

        for row in ws.iter_rows(min_row=2, values_only=True):
            if (item := row_to_fixed_income(row)) is not None:
                items.append(item)

        wb.close()
        log.info("fixed_income_parsed", path=str(path), count=len(items))
        return items

    except Exception as exc:
        raise ExcelParseError(f"Failed to parse FixedIncome sheet in {path}: {exc}") from exc


async def read_positions(path: Path) -> list[Position]:
    """Read variable-asset positions from the first sheet of the Excel file.

    This is an `async` function, meaning it must be called with `await`:
        positions = await read_positions(path)

    Why is it async when openpyxl is a blocking library?
    ----------------------------------------------------
    asyncio runs everything on a single thread. If you call a slow, blocking
    function directly (like reading a large file), the entire event loop — and
    every other async task, including UI updates — freezes until it finishes.

    `asyncio.to_thread(fn, *args)` solves this by running `fn` in a separate
    thread from a background thread pool. The event loop stays free to handle
    other coroutines while the file is being read in the background.
    """
    return await asyncio.to_thread(_parse_excel, path)


async def read_fixed_income(path: Path) -> list[FixedIncomePosition]:
    """Read fixed-income positions from the 'FixedIncome' sheet asynchronously.

    Uses the same `asyncio.to_thread` pattern as `read_positions` so the event
    loop is never blocked while the file is being read.
    """
    return await asyncio.to_thread(_parse_fixed_income, path)

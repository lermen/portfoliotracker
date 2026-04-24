# `asyncio` is Python's built-in library for writing concurrent code using the
# async/await syntax. "Concurrent" means multiple things can be in progress at
# the same time, even though Python typically runs one thread at a time.
import asyncio
from pathlib import Path

# `openpyxl` is a third-party library for reading and writing Excel (.xlsx) files.
import openpyxl

# `structlog` is a structured logging library. Instead of plain text log lines,
# it emits key=value pairs that are easy to filter and machine-parse in production.
import structlog

from portfolio.core.exceptions import ExcelParseError
from portfolio.core.models import Position

# Get a logger bound to this module. The `log` variable is used throughout the
# file to emit log events. Structured loggers let you attach arbitrary context:
#   log.info("event_name", key=value, another_key=value)
log = structlog.get_logger()


def _parse_excel(path: Path) -> list[Position]:
    # The leading underscore in `_parse_excel` is a Python convention signalling
    # "private / internal use". Nothing stops external code from calling it, but
    # the underscore is a clear signal that it is an implementation detail.
    try:
        # `read_only=True` streams the file without loading it all into memory.
        # `data_only=True` returns the cached cell values instead of Excel formulas.
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active   # the "active" sheet is the one that was open when the file was saved
        if ws is None:
            raise ExcelParseError("Workbook has no active sheet")

        positions: list[Position] = []

        # `iter_rows(min_row=2)` skips the header row (row 1) and yields each
        # subsequent row as a tuple of cell values.
        # `values_only=True` gives us the raw Python values rather than Cell objects.
        for row in ws.iter_rows(min_row=2, values_only=True):
            ticker, quantity = row[0], row[1]

            # Skip completely empty rows — openpyxl includes trailing blank rows.
            if ticker is None or quantity is None:
                continue

            # `len(row) > 2` guards against rows that have fewer columns than expected.
            # The `and row[2] is not None` check prevents calling `.strip()` on None.
            exchange = (
                str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
            )
            category = (
                str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
            )

            # Pydantic validates each field when `Position(...)` is called.
            # If `quantity` is <= 0, Pydantic raises a ValidationError automatically.
            positions.append(
                Position(
                    ticker=str(ticker).strip(),
                    quantity=float(quantity),
                    exchange=exchange,
                    category=category,
                )
            )

        wb.close()
        log.info("excel_parsed", path=str(path), count=len(positions))
        return positions

    except ExcelParseError:
        # Re-raise our own exception type unchanged — we don't want to wrap it
        # in another ExcelParseError below.
        raise
    except Exception as exc:
        # Catch any other unexpected error and wrap it in our own type.
        # `raise ... from exc` preserves the original traceback (the "cause"),
        # which is invaluable when debugging.
        raise ExcelParseError(f"Failed to parse {path}: {exc}") from exc


async def read_positions(path: Path) -> list[Position]:
    """Read positions from an Excel file asynchronously.

    `asyncio.to_thread` runs `_parse_excel` in a background OS thread so the
    async event loop is never blocked while the file is being read.

    Why does this matter? asyncio runs everything on a single thread. If you
    call a slow, blocking function directly (like reading a file with openpyxl),
    the entire event loop — and every other async task — freezes until it
    finishes. `to_thread` hands the work off to a thread pool so the loop
    stays free to handle other things.
    """
    return await asyncio.to_thread(_parse_excel, path)

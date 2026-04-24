import asyncio
from pathlib import Path

import openpyxl
import structlog

from portfolio.core.exceptions import ExcelParseError
from portfolio.core.models import Position

log = structlog.get_logger()


def _parse_excel(path: Path) -> list[Position]:
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            raise ExcelParseError("Workbook has no active sheet")

        positions: list[Position] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            ticker, quantity = row[0], row[1]
            if ticker is None or quantity is None:
                continue
            exchange = (
                str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
            )
            category = (
                str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""
            )
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
        raise
    except Exception as exc:
        raise ExcelParseError(f"Failed to parse {path}: {exc}") from exc


async def read_positions(path: Path) -> list[Position]:
    """Read positions from an Excel file asynchronously."""
    return await asyncio.to_thread(_parse_excel, path)

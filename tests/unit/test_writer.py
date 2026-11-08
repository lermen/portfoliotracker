# Unit tests for core/writer.py.
#
# These build a throwaway .xlsx in a temporary directory (pytest's `tmp_path`
# fixture), run the async writer functions against it, then re-open the file to
# assert the right cells changed and everything else was preserved.
#
# `asyncio_mode = "auto"` (set in pyproject.toml) lets us write `async def test_*`
# functions without decorating each one — pytest-asyncio runs them on an event loop.

from pathlib import Path

import openpyxl
import pytest

from portfolio.core.exceptions import DuplicateTickerError, PositionNotFoundError
from portfolio.core.models import FixedIncomePosition, Position
from portfolio.core.writer import (
    add_fixed_income,
    add_position,
    remove_fixed_income,
    remove_position,
    set_avg_price,
    set_quantity,
    update_fixed_income,
)


def _make_workbook(path: Path) -> None:
    """Create a minimal portfolio workbook that matches the reader's layout.

    First sheet: header + two positions. Second sheet: a FixedIncome row we use
    to prove the writer never touches other sheets.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(["Ticker", "Quantity", "Exchange", "Category", "Average Price"])
    ws.append(["ITSA4.SA", 100, "B3", "Stock", 7.84])
    ws.append(["WEGE3.SA", 200, "B3", "Stock", 33.05])

    fi = wb.create_sheet("FixedIncome")
    fi.append(["Name", "Amount"])
    fi.append(["CDB Itaú", 604000])
    wb.save(path)


@pytest.fixture()
def book(tmp_path: Path) -> Path:
    """Return the path to a fresh workbook for each test."""
    path = tmp_path / "portfolio.xlsx"
    _make_workbook(path)
    return path


def _rows(path: Path) -> list[tuple]:
    """Read back the first sheet's data rows (skipping the header)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = [tuple(r) for r in ws.iter_rows(min_row=2, values_only=True)]
    wb.close()
    return rows


async def test_set_quantity_updates_only_that_cell(book: Path) -> None:
    await set_quantity(book, "ITSA4.SA", 9105)
    rows = _rows(book)
    assert rows[0] == ("ITSA4.SA", 9105, "B3", "Stock", 7.84)  # qty changed, avg price intact
    assert rows[1] == ("WEGE3.SA", 200, "B3", "Stock", 33.05)  # other row untouched


async def test_set_quantity_matches_despite_whitespace(book: Path) -> None:
    # The reader strips whitespace; the writer must match the same way.
    await set_quantity(book, "  ITSA4.SA  ", 500)
    assert _rows(book)[0][1] == 500


async def test_set_quantity_missing_ticker_raises(book: Path) -> None:
    with pytest.raises(PositionNotFoundError):
        await set_quantity(book, "NOPE.SA", 10)


async def test_set_quantity_rejects_non_positive(book: Path) -> None:
    with pytest.raises(ValueError):
        await set_quantity(book, "ITSA4.SA", 0)


async def test_set_avg_price_updates_only_that_cell(book: Path) -> None:
    await set_avg_price(book, "ITSA4.SA", 6.50)
    rows = _rows(book)
    assert rows[0] == ("ITSA4.SA", 100, "B3", "Stock", 6.50)   # avg price changed, qty intact
    assert rows[1] == ("WEGE3.SA", 200, "B3", "Stock", 33.05)  # other row untouched


async def test_set_avg_price_missing_ticker_raises(book: Path) -> None:
    with pytest.raises(PositionNotFoundError):
        await set_avg_price(book, "NOPE.SA", 10)


async def test_set_avg_price_rejects_non_positive(book: Path) -> None:
    with pytest.raises(ValueError):
        await set_avg_price(book, "ITSA4.SA", 0)


async def test_add_position_appends_row(book: Path) -> None:
    await add_position(
        book,
        Position(ticker="PETR4.SA", quantity=50, exchange="B3", category="Stock", avg_price_native=32.5),
    )
    rows = _rows(book)
    assert len(rows) == 3
    assert rows[2] == ("PETR4.SA", 50, "B3", "Stock", 32.5)


async def test_add_position_without_avg_price_leaves_blank(book: Path) -> None:
    await add_position(book, Position(ticker="MGLU3.SA", quantity=10))
    # The avg-price cell (index 4) should be empty, matching how the reader
    # treats a missing average.
    assert _rows(book)[2][4] is None


async def test_add_duplicate_ticker_raises(book: Path) -> None:
    with pytest.raises(DuplicateTickerError):
        await add_position(book, Position(ticker="ITSA4.SA", quantity=1))


async def test_remove_position(book: Path) -> None:
    await remove_position(book, "ITSA4.SA")
    rows = _rows(book)
    assert len(rows) == 1
    assert rows[0][0] == "WEGE3.SA"  # remaining row shifted up, no gap


async def test_remove_missing_ticker_raises(book: Path) -> None:
    with pytest.raises(PositionNotFoundError):
        await remove_position(book, "NOPE.SA")


async def test_other_sheet_is_preserved(book: Path) -> None:
    # Every edit must leave the FixedIncome sheet completely intact.
    await set_quantity(book, "ITSA4.SA", 1)
    await add_position(book, Position(ticker="BBAS3.SA", quantity=5))
    await remove_position(book, "WEGE3.SA")

    wb = openpyxl.load_workbook(book, data_only=True)
    assert "FixedIncome" in wb.sheetnames
    fi_rows = [tuple(r) for r in wb["FixedIncome"].iter_rows(min_row=2, values_only=True)]
    wb.close()
    assert fi_rows == [("CDB Itaú", 604000)]


# --- FixedIncome sheet --------------------------------------------------------


def _fi_rows(path: Path) -> list[tuple]:
    """Read back the FixedIncome sheet's data rows (skipping the header)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = [tuple(r) for r in wb["FixedIncome"].iter_rows(min_row=2, values_only=True)]
    wb.close()
    return rows


async def test_update_fixed_income_amount(book: Path) -> None:
    await update_fixed_income(
        book, "CDB Itaú", FixedIncomePosition(name="CDB Itaú", amount_brl=610000)
    )
    assert _fi_rows(book) == [("CDB Itaú", 610000)]


async def test_update_fixed_income_can_rename(book: Path) -> None:
    await update_fixed_income(
        book, "CDB Itaú", FixedIncomePosition(name="CDB Itaú 2027", amount_brl=1)
    )
    assert _fi_rows(book) == [("CDB Itaú 2027", 1)]


async def test_update_fixed_income_rename_onto_existing_raises(book: Path) -> None:
    await add_fixed_income(book, FixedIncomePosition(name="Poupança", amount_brl=10))
    with pytest.raises(DuplicateTickerError):
        await update_fixed_income(
            book, "Poupança", FixedIncomePosition(name="CDB Itaú", amount_brl=10)
        )


async def test_update_fixed_income_missing_raises(book: Path) -> None:
    with pytest.raises(PositionNotFoundError):
        await update_fixed_income(
            book, "Nope", FixedIncomePosition(name="Nope", amount_brl=1)
        )


async def test_add_fixed_income_appends_row(book: Path) -> None:
    await add_fixed_income(
        book, FixedIncomePosition(name="Tesouro IPCA 2035", amount_brl=5000)
    )
    assert _fi_rows(book) == [("CDB Itaú", 604000), ("Tesouro IPCA 2035", 5000)]


async def test_add_fixed_income_duplicate_raises(book: Path) -> None:
    with pytest.raises(DuplicateTickerError):
        await add_fixed_income(
            book, FixedIncomePosition(name="CDB Itaú", amount_brl=1)
        )


async def test_add_fixed_income_creates_missing_sheet(tmp_path: Path) -> None:
    # A workbook with only a positions sheet: the first add creates the
    # FixedIncome sheet (with its header) after it, leaving positions first.
    path = tmp_path / "no_fi.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["Ticker", "Quantity"])
    wb.save(path)

    await add_fixed_income(path, FixedIncomePosition(name="Poupança", amount_brl=100))

    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[1] == "FixedIncome"
    header = next(wb["FixedIncome"].iter_rows(max_row=1, values_only=True))
    wb.close()
    assert header == ("Name", "Amount")
    assert _fi_rows(path) == [("Poupança", 100)]


async def test_remove_fixed_income(book: Path) -> None:
    await remove_fixed_income(book, "CDB Itaú")
    assert _fi_rows(book) == []
    with pytest.raises(PositionNotFoundError):
        await remove_fixed_income(book, "CDB Itaú")


async def test_fixed_income_edits_leave_positions_alone(book: Path) -> None:
    before = _rows(book)
    await add_fixed_income(book, FixedIncomePosition(name="LCI", amount_brl=1))
    await update_fixed_income(
        book, "LCI", FixedIncomePosition(name="LCI", amount_brl=2)
    )
    await remove_fixed_income(book, "LCI")
    assert _rows(book) == before

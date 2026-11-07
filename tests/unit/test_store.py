# Unit tests for the storage backends: core/store.py and core/sheets_store.py.
#
# The Google Sheets store is tested against an in-memory *fake* of the few
# gspread objects it uses (`_FakeWorksheet`, `_FakeSpreadsheet`), so these tests
# never touch the network or need credentials. A fake is a small stand-in class
# with the same method names as the real thing — the store can't tell the
# difference, which is exactly the duck typing that makes Protocols work.

from pathlib import Path
from typing import Any

import gspread
import pytest

from portfolio.core import sheets_store
from portfolio.core.exceptions import (
    ConfigError,
    DuplicateTickerError,
    PositionNotFoundError,
)
from portfolio.core.models import FixedIncomePosition, Position
from portfolio.core.reader import row_to_fixed_income, row_to_position
from portfolio.core.settings import Settings
from portfolio.core.sheets_store import GoogleSheetsStore
from portfolio.core.store import ExcelStore, get_store

# --- shared row helpers ----------------------------------------------------------


def test_row_to_position_full_row() -> None:
    pos = row_to_position(["ITSA4.SA ", 9105, "B3", "Stock", 7.84])
    assert pos == Position(
        ticker="ITSA4.SA",
        quantity=9105,
        exchange="B3",
        category="Stock",
        avg_price_native=7.84,
    )


def test_row_to_position_short_row_from_gspread() -> None:
    # gspread drops trailing empty cells, so there may be no Avg Price column.
    pos = row_to_position(["SAP.DE", 10, "Frankfurt"])
    assert pos is not None
    assert pos.category == ""
    assert pos.avg_price_native is None


@pytest.mark.parametrize("row", [[None, None], ["", ""], ["AAPL", ""], ["  ", 5], []])
def test_row_to_position_skips_blank_rows(row: list[Any]) -> None:
    # Blank cells are None in openpyxl and "" in gspread — both mean "skip".
    assert row_to_position(row) is None


def test_row_to_fixed_income() -> None:
    item = row_to_fixed_income(["CDB Itaú", 624000])
    assert item is not None
    assert item.amount_brl == 624000
    assert row_to_fixed_income(["", ""]) is None


# --- get_store ------------------------------------------------------------------


def test_get_store_excel_is_default(tmp_path: Path) -> None:
    store = get_store(Settings(_env_file=None, excel_path=tmp_path / "p.xlsx"))
    assert isinstance(store, ExcelStore)


def test_get_store_gsheets_requires_spreadsheet_id() -> None:
    settings = Settings(_env_file=None, storage_backend="gsheets")
    with pytest.raises(ConfigError, match="GSHEETS_SPREADSHEET_ID"):
        get_store(settings)


def test_get_store_gsheets_requires_credentials_file(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_backend="gsheets",
        gsheets_spreadsheet_id="abc",
        gsheets_credentials_path=tmp_path / "missing.json",
    )
    with pytest.raises(ConfigError, match="credentials"):
        get_store(settings)


# --- GoogleSheetsStore against a fake spreadsheet ----------------------------------


class _FakeWorksheet:
    """Holds rows in a list of lists; row 1 of the sheet is `rows[0]`."""

    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows

    def get_values(self, **_kwargs: Any) -> list[list[Any]]:
        return [list(r) for r in self.rows]

    def col_values(self, col: int) -> list[Any]:
        return [r[col - 1] if len(r) >= col else "" for r in self.rows]

    def update_cell(self, row: int, col: int, value: Any) -> None:
        self.rows[row - 1][col - 1] = value

    def insert_row(self, values: list[Any], index: int) -> None:
        self.rows.insert(index - 1, list(values))

    def delete_rows(self, start_index: int) -> None:
        del self.rows[start_index - 1]


class _FakeSpreadsheet:
    def __init__(self, tabs: dict[str, _FakeWorksheet]) -> None:
        self.tabs = tabs

    def get_worksheet(self, index: int) -> _FakeWorksheet:
        return list(self.tabs.values())[index]

    def worksheet(self, name: str) -> _FakeWorksheet:
        if name not in self.tabs:
            raise gspread.exceptions.WorksheetNotFound(name)
        return self.tabs[name]

    def add_worksheet(self, title: str, rows: int, cols: int) -> _FakeWorksheet:
        self.tabs[title] = _FakeWorksheet([])
        return self.tabs[title]


class _FakeClient:
    def __init__(self, spreadsheet: _FakeSpreadsheet) -> None:
        self.spreadsheet = spreadsheet

    def open_by_key(self, _key: str) -> _FakeSpreadsheet:
        return self.spreadsheet


def _store(
    monkeypatch: pytest.MonkeyPatch, tabs: dict[str, _FakeWorksheet]
) -> GoogleSheetsStore:
    """Build a store whose gspread client is our fake.

    `monkeypatch.setattr` temporarily replaces `gspread.service_account` (as seen
    from sheets_store) for the duration of one test, then restores it.
    """
    client = _FakeClient(_FakeSpreadsheet(tabs))
    monkeypatch.setattr(
        sheets_store.gspread, "service_account", lambda filename: client
    )
    return GoogleSheetsStore("sheet-id", Path("unused.json"))


@pytest.fixture()
def positions() -> _FakeWorksheet:
    return _FakeWorksheet(
        [
            ["Ticker", "Quantity", "Exchange", "Category", "Average Price"],
            ["ITSA4.SA", 9105, "B3", "Stock", 7.84],
            ["SAP.DE", 10, "Frankfurt", "Stock"],
            ["", "", "", "", ""],  # trailing blank row, as Sheets often has
        ]
    )


async def test_sheets_reads_positions_and_fixed_income(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    fixed = _FakeWorksheet([["Name", "Amount"], ["CDB Itaú", 624000]])
    store = _store(monkeypatch, {"Sheet": positions, "FixedIncome": fixed})

    read = await store.read_positions()
    assert [p.ticker for p in read] == ["ITSA4.SA", "SAP.DE"]
    assert read[1].avg_price_native is None

    fi = await store.read_fixed_income()
    assert [(i.name, i.amount_brl) for i in fi] == [("CDB Itaú", 624000)]


async def test_sheets_missing_fixed_income_tab_is_empty(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    store = _store(monkeypatch, {"Sheet": positions})
    assert await store.read_fixed_income() == []


async def test_sheets_set_quantity_and_avg_price(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    store = _store(monkeypatch, {"Sheet": positions})
    await store.set_quantity("SAP.DE", 12)
    await store.set_avg_price("ITSA4.SA", 8.5)
    assert positions.rows[2][1] == 12
    assert positions.rows[1][4] == 8.5


async def test_sheets_edit_unknown_ticker_raises(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    store = _store(monkeypatch, {"Sheet": positions})
    with pytest.raises(PositionNotFoundError):
        await store.set_quantity("NOPE", 1)
    with pytest.raises(PositionNotFoundError):
        await store.remove_position("NOPE")


async def test_sheets_add_position_after_last_ticker(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    store = _store(monkeypatch, {"Sheet": positions})
    await store.add_position(Position(ticker="WEGE3.SA", quantity=5))
    # Lands right after SAP.DE (row 3), before the blank trailing row.
    assert positions.rows[3][0] == "WEGE3.SA"

    with pytest.raises(DuplicateTickerError):
        await store.add_position(Position(ticker="ITSA4.SA", quantity=1))


async def test_sheets_remove_position(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    store = _store(monkeypatch, {"Sheet": positions})
    await store.remove_position("ITSA4.SA")
    assert [r[0] for r in positions.rows[1:3]] == ["SAP.DE", ""]


async def test_sheets_update_add_remove_fixed_income(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    fixed = _FakeWorksheet([["Name", "Amount"], ["CDB Itaú", 624000], ["", ""]])
    store = _store(monkeypatch, {"Sheet": positions, "FixedIncome": fixed})

    await store.update_fixed_income(
        "CDB Itaú", FixedIncomePosition(name="CDB Itaú", amount_brl=630000)
    )
    assert fixed.rows[1] == ["CDB Itaú", 630000]

    await store.add_fixed_income(FixedIncomePosition(name="LCI", amount_brl=10))
    # Lands right after the last name, before the blank trailing row.
    assert fixed.rows[2] == ["LCI", 10]

    with pytest.raises(DuplicateTickerError):
        await store.add_fixed_income(FixedIncomePosition(name="LCI", amount_brl=1))
    with pytest.raises(DuplicateTickerError):
        await store.update_fixed_income(
            "LCI", FixedIncomePosition(name="CDB Itaú", amount_brl=1)
        )

    await store.remove_fixed_income("CDB Itaú")
    assert [r[0] for r in fixed.rows[1:]] == ["LCI", ""]
    with pytest.raises(PositionNotFoundError):
        await store.remove_fixed_income("CDB Itaú")


async def test_sheets_add_fixed_income_creates_tab(
    monkeypatch: pytest.MonkeyPatch, positions: _FakeWorksheet
) -> None:
    tabs = {"Sheet": positions}
    store = _store(monkeypatch, tabs)
    await store.add_fixed_income(FixedIncomePosition(name="Poupança", amount_brl=50))
    assert tabs["FixedIncome"].rows == [["Name", "Amount"], ["Poupança", 50]]
    assert [(i.name, i.amount_brl) for i in await store.read_fixed_income()] == [
        ("Poupança", 50)
    ]

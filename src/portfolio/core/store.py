# This module defines *where the portfolio lives* as an abstract contract, so the
# rest of the app never needs to know whether that's a local Excel file or a
# shared Google Sheet.
#
#   engine / importer / TUI ──► PortfolioStore ──► ExcelStore        (local .xlsx)
#                                              └─► GoogleSheetsStore (Google Sheet)
#
# Protocols: "duck typing" the type checker understands
# -----------------------------------------------------
# Python has a saying: "if it walks like a duck and quacks like a duck, it's a
# duck". Code that calls `store.read_positions()` doesn't care what *class* the
# store is, only that it *has* that method.
#
# `typing.Protocol` writes that idea down. `PortfolioStore` below lists the
# methods a store must have; any class with matching methods counts as a
# `PortfolioStore` — no inheritance needed. `ExcelStore` never mentions
# `PortfolioStore`, yet mypy accepts it wherever a `PortfolioStore` is expected
# because its methods match. If a method were missing or had the wrong
# signature, mypy would report it.
#
# Dependency injection
# --------------------
# Instead of each module reading `settings.excel_path` itself, the store is built
# once at startup (`get_store`) and *passed in* to whoever needs it. That's
# called dependency injection. It's what makes switching backends a one-line
# config change, and lets tests hand in a store pointing at a temporary file.

from pathlib import Path
from typing import Protocol

from portfolio.core import reader, writer
from portfolio.core.exceptions import ConfigError
from portfolio.core.models import FixedIncomePosition, Position
from portfolio.core.settings import Settings


class PortfolioStore(Protocol):
    """Everything the app needs from wherever the portfolio is stored.

    Rules every implementation follows (the Excel layout defines them):
    - Positions are on the *first* tab; FixedIncome on a tab named "FixedIncome".
    - Row 1 is a header; rows with a blank ticker/quantity are skipped.
    - A missing FixedIncome tab means "no fixed income", not an error.
    - Editing an unknown ticker raises `PositionNotFoundError`; adding an
      existing one raises `DuplicateTickerError`.

    The `...` bodies are not placeholders to fill in later — in a Protocol they
    mean "this is only a signature; implementations provide the body".
    """

    # `description` is a plain attribute in the contract: a short human-readable
    # label for where the data lives, shown by the CLI ("Target: ...").
    description: str

    async def read_positions(self) -> list[Position]: ...
    async def read_fixed_income(self) -> list[FixedIncomePosition]: ...
    async def set_quantity(self, ticker: str, quantity: float) -> None: ...
    async def set_avg_price(self, ticker: str, avg_price: float) -> None: ...
    async def add_position(self, position: Position) -> None: ...
    async def remove_position(self, ticker: str) -> None: ...


class ExcelStore:
    """The portfolio in a local .xlsx file.

    A thin wrapper: every method forwards to the existing `reader` / `writer`
    functions, filling in the file path so callers don't have to carry it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.description = str(path)

    async def read_positions(self) -> list[Position]:
        return await reader.read_positions(self.path)

    async def read_fixed_income(self) -> list[FixedIncomePosition]:
        return await reader.read_fixed_income(self.path)

    async def set_quantity(self, ticker: str, quantity: float) -> None:
        await writer.set_quantity(self.path, ticker, quantity)

    async def set_avg_price(self, ticker: str, avg_price: float) -> None:
        await writer.set_avg_price(self.path, ticker, avg_price)

    async def add_position(self, position: Position) -> None:
        await writer.add_position(self.path, position)

    async def remove_position(self, ticker: str) -> None:
        await writer.remove_position(self.path, ticker)


def get_store(settings: Settings) -> PortfolioStore:
    """Build the store selected by `STORAGE_BACKEND`.

    `match` / `case` (Python 3.10+) compares a value against patterns — here
    simply the two allowed strings. Because `storage_backend` is a `Literal`,
    mypy knows these are the only two cases.
    """
    match settings.storage_backend:
        case "excel":
            return ExcelStore(settings.excel_path)
        case "gsheets":
            if not settings.gsheets_spreadsheet_id:
                raise ConfigError(
                    "STORAGE_BACKEND=gsheets needs GSHEETS_SPREADSHEET_ID in .env"
                )
            if not settings.gsheets_credentials_path.exists():
                raise ConfigError(
                    "Google credentials file not found: "
                    f"{settings.gsheets_credentials_path}"
                )
            # Imported here, not at the top of the file, so people using the
            # Excel backend never load the Google libraries at all.
            from portfolio.core.sheets_store import GoogleSheetsStore

            return GoogleSheetsStore(
                spreadsheet_id=settings.gsheets_spreadsheet_id,
                credentials_path=settings.gsheets_credentials_path,
            )

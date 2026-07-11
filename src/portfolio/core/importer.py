# This module syncs the portfolio spreadsheet from a B3 "posição" export — the
# multi-sheet .xlsx you download from your broker's investor area.
#
# It is pure business logic: it parses the export, compares it against the current
# portfolio, and produces a *plan* of what would change. Nothing here prints to the
# screen or asks the user anything — that is the CLI frontend's job
# (ui/cli/import_command.py). Keeping the decision-making here and the input/output
# there is the same core/UI separation the rest of the project follows, and it
# means the exact same import logic could later be driven from the TUI or a web UI.
#
# The B3 export format
# --------------------
# The workbook has one sheet per asset class. Each sheet is a small table: a header
# row, one row per holding, then a "Total" row. The columns differ per sheet, but
# every tradable sheet has a "Produto" column whose first token is the ticker
# (e.g. "BBAS3 - BCO BRASIL S.A." → "BBAS3") and a "Quantidade" column.
#
# A ticker can appear on more than one sheet — for example shares you own (Acoes)
# plus the same shares you've lent out (Empréstimos) — so we *sum* the quantity
# across sheets to get the total economic holding, exactly as a manual reconcile
# would. Two sheets carry no market ticker (fixed income / treasury) and are
# skipped; those are tracked separately in the app's FixedIncome sheet.

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import structlog

from portfolio.core.exceptions import ExcelParseError
from portfolio.core.models import Position
from portfolio.core.reader import read_positions
from portfolio.core.writer import add_position, set_quantity

log = structlog.get_logger()

# Which export sheets hold tradable, ticker-bearing positions, and the app
# category each maps to. "Renda Fixa" and "Tesouro Direto" are intentionally
# absent — they have no market ticker and live in the FixedIncome sheet instead.
_SHEET_CATEGORY: dict[str, str] = {
    "Acoes": "Stock",
    "Empréstimos": "Stock",   # lent shares are still stocks/FIIs you own
    "ETF": "ETF",
    "Fundo de Investimento": "Fund",
}

# When the same ticker appears on multiple sheets we keep one category for it.
# Lower number = higher priority, so a fund lent out (Empréstimos) is still
# categorised as a Fund rather than a Stock.
_CATEGORY_PRIORITY: dict[str, int] = {"Fund": 0, "ETF": 1, "Stock": 2}

# The Yahoo Finance suffix the app uses for B3-listed tickers. The export lists
# bare tickers ("PETR4"); the portfolio stores them as "PETR4.SA".
_B3_SUFFIX = ".SA"


@dataclass
class ExportEntry:
    """One aggregated holding from the B3 export: total quantity + its category."""

    quantity: float
    category: str


@dataclass
class TickerChange:
    """A single quantity change the import would make to an existing row."""

    ticker: str              # the portfolio ticker (with suffix), e.g. "FLRY3.SA"
    old_quantity: float
    new_quantity: float


@dataclass
class ImportPlan:
    """The full set of changes an import would make, computed before writing.

    Splitting "decide" (this plan) from "do" (apply_plan) lets the CLI show the
    user exactly what will happen and ask for confirmation first — nothing touches
    the spreadsheet until the plan is applied.

    `field(default_factory=list)` gives each instance its own fresh list. Using a
    plain `= []` default would be a classic Python bug: the same list object would
    be shared by every ImportPlan.
    """

    # existing tickers whose quantity changed:
    updates: list[TickerChange] = field(default_factory=list)
    # tickers in the export but not in the portfolio:
    additions: list[Position] = field(default_factory=list)
    # matched tickers whose quantity is already correct:
    unchanged: list[str] = field(default_factory=list)
    # tickers in the portfolio but absent from the export (never removed):
    portfolio_only: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """True if applying the plan would modify the spreadsheet at all."""
        return bool(self.updates or self.additions)


def _base_ticker(ticker: str) -> str:
    """Strip the B3 ".SA" suffix so export and portfolio tickers can be compared.

    Portfolio tickers carry a suffix ("ITSA4.SA"); the export does not ("ITSA4").
    Non-B3 tickers like "SAP.DE" or "BTC-USD" don't end in ".SA", so they pass
    through unchanged and simply never match an export row — which is exactly what
    we want, since the B3 export knows nothing about them.
    """
    if ticker.endswith(_B3_SUFFIX):
        return ticker[: -len(_B3_SUFFIX)]
    return ticker


def _parse_b3_export(path: Path) -> dict[str, ExportEntry]:
    """Read the B3 export and aggregate quantity (and category) per bare ticker.

    Blocking (openpyxl); the async wrapper below runs it off the event loop.
    """
    try:
        # NOTE: we deliberately do *not* use `read_only=True` here. Broker exports
        # often carry unreliable sheet-dimension metadata, and in read-only mode
        # openpyxl trusts that metadata and can truncate rows to a single column.
        # These files are small (dozens of rows), so loading them fully is both
        # safe and correct. `data_only=True` returns cached values, not formulas.
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:
        raise ExcelParseError(f"Failed to open B3 export {path}: {exc}") from exc

    entries: dict[str, ExportEntry] = {}
    try:
        for ws in wb.worksheets:
            category = _SHEET_CATEGORY.get(ws.title)
            if category is None:
                continue  # Renda Fixa / Tesouro Direto (no ticker) — skip

            # Stream the sheet a row at a time. Read-only worksheets don't support
            # random access like `ws[1]`, so we take the header from the first row
            # the iterator yields, then process the rest. `enumerate` gives us the
            # row's position so we can treat row 0 as the header.
            rows = ws.iter_rows(values_only=True)
            try:
                header = list(next(rows))
            except StopIteration:
                continue  # completely empty sheet

            # Find the "Quantidade" column from the header rather than hardcoding
            # its position, because it sits in a different column on each sheet.
            try:
                q_idx = header.index("Quantidade")
            except ValueError:
                # A tradable sheet with no Quantidade column means the file isn't
                # the format we expect — fail loudly rather than silently skip it.
                # `from None` suppresses the low-level ValueError from `.index()`
                # so the traceback shows only our clear domain error.
                raise ExcelParseError(
                    f"Sheet {ws.title!r} in {path} has no 'Quantidade' column"
                ) from None

            for row in rows:
                produto = row[0]
                # Skip blank spacer rows and the trailing "Total" row.
                if not produto or str(produto).strip() in ("", "Total"):
                    continue
                ticker = str(produto).split()[0]   # "BBAS3 - ..." → "BBAS3"

                quantity = row[q_idx]
                # Rights/receipts sometimes have a "-" or 0 quantity — ignore them.
                if quantity in (None, "-", 0):
                    continue

                if ticker in entries:
                    entry = entries[ticker]
                    entry.quantity += float(quantity)
                    # Keep the higher-priority category if this sheet outranks the
                    # one we first saw the ticker on.
                    new_rank = _CATEGORY_PRIORITY[category]
                    if new_rank < _CATEGORY_PRIORITY[entry.category]:
                        entry.category = category
                else:
                    entries[ticker] = ExportEntry(
                        quantity=float(quantity), category=category
                    )
    finally:
        wb.close()

    log.info("b3_export_parsed", path=str(path), tickers=len(entries))
    return entries


async def build_plan(export_path: Path, portfolio_path: Path) -> ImportPlan:
    """Compare a B3 export against the portfolio and return the changes to make.

    Reads nothing destructively — this only *computes* the plan. The heavy file
    reads run off the event loop: `_parse_b3_export` via `asyncio.to_thread`, and
    the portfolio via the existing async `read_positions`.
    """
    export = await asyncio.to_thread(_parse_b3_export, export_path)
    positions = await read_positions(portfolio_path)

    # Index the current portfolio by its bare (suffix-stripped) ticker so we can
    # match "ITSA4.SA" in the file against "ITSA4" in the export.
    portfolio_by_base: dict[str, Position] = {
        _base_ticker(p.ticker): p for p in positions
    }

    plan = ImportPlan()
    for base, entry in export.items():
        existing = portfolio_by_base.get(base)
        if existing is None:
            # Not in the portfolio yet — a candidate addition. Re-attach the B3
            # suffix and tag it with a sensible exchange/category.
            plan.additions.append(
                Position(
                    ticker=base + _B3_SUFFIX,
                    quantity=entry.quantity,
                    exchange="B3",
                    category=entry.category,
                )
            )
        elif existing.quantity != entry.quantity:
            plan.updates.append(
                TickerChange(
                    ticker=existing.ticker,
                    old_quantity=existing.quantity,
                    new_quantity=entry.quantity,
                )
            )
        else:
            plan.unchanged.append(existing.ticker)

    # Tickers held in the portfolio but not in the export. These are never removed
    # automatically: they include legitimately non-B3 assets (SAP.DE, BTC-USD) and
    # anything the export simply didn't cover. We only report them.
    plan.portfolio_only = sorted(
        p.ticker for base, p in portfolio_by_base.items() if base not in export
    )
    return plan


async def apply_plan(
    plan: ImportPlan, portfolio_path: Path, add_new: bool = False
) -> None:
    """Write a plan's changes to the portfolio spreadsheet via the writer.

    Quantity updates are always applied. New tickers are only added when
    `add_new` is True, because adding a position makes structural changes (and
    guesses the category) that the user should opt into explicitly.
    """
    for change in plan.updates:
        await set_quantity(portfolio_path, change.ticker, change.new_quantity)

    if add_new:
        for position in plan.additions:
            await add_position(portfolio_path, position)

    log.info(
        "import_applied",
        updated=len(plan.updates),
        added=len(plan.additions) if add_new else 0,
    )

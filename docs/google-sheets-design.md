# Design: Google Sheets as the Portfolio Store

**Status:** proposed · **Author:** design pass, 2026-10-25 · **Scope:** `core/` + settings + CLI

Move the portfolio from a local `data/portfolio.xlsx` to a Google Sheet, so every
computer running the app — and anyone you share the sheet with — reads and edits
the same data.

---

## 1. Why

Today the source of truth is a file on disk:

```
settings.excel_path ──► data/portfolio.xlsx   (one copy per computer)
```

With two or more computers that means two or more copies. An edit made in the
TUI on the laptop (`u`, `P`, add, remove) or by `portfolio import` never reaches
the desktop, and the two drift apart until someone copies the file by hand —
and copying the wrong direction silently loses edits.

A Google Sheet is a single shared copy with an HTTP API, sharing permissions,
and version history built in. Every client reads from it on each refresh, so
there is nothing to sync.

### Goals

- One shared spreadsheet is the source of truth for positions and fixed income.
- Every existing feature keeps working: TUI edits, B3 import, summary tiles.
- Sheet layout stays identical to the Excel workbook (same tabs, same columns),
  so the migration is "upload the file", not "redesign the data".
- The Excel backend keeps working, selectable by one environment variable, so
  rollback is a config change.
- `core/` keeps zero UI imports; the UI never learns which backend is in use.

### Non-goals (v1)

- Offline mode. The app already needs the network for prices, so a sheet that
  can't be reached is the same failure as Yahoo being unreachable.
- Real-time push of edits between clients. Each client picks up changes on its
  next refresh (`REFRESH_INTERVAL_SECONDS`), or immediately after its own edits.
- Locking or merging concurrent edits to the *same* row (see §8).
- A web UI. This design makes one easier (§11) but does not build it.

---

## 2. Where it fits

Every place that touches the Excel file today goes through a `Path`:

| Caller | What it calls |
|--------|---------------|
| `core/engine.py:74-75` | `read_positions(settings.excel_path)`, `read_fixed_income(...)` |
| `ui/tui/app.py:428-455` | `set_quantity`, `set_avg_price`, `add_position`, `remove_position` with `settings.excel_path` |
| `core/importer.py:200,242` | `read_positions(portfolio_path)`, then `add_position` / `set_quantity` |
| `ui/cli/import_command.py:81` | resolves `portfolio_path` from `settings.excel_path` |

The functions themselves are already the right shape — async, typed, returning
`Position` / `FixedIncomePosition`. What's wrong is that the *location* of the
data (`Path`) leaks into every caller. The core change is to replace that `Path`
with a **store** object that knows where the data lives:

```
                     ┌──────────────────────────┐
  engine.py ────────►│  PortfolioStore (Protocol)│◄──────── ui/tui/app.py
  importer.py ──────►│   read_positions()        │          (edits)
                     │   read_fixed_income()     │
                     │   set_quantity() ...      │
                     └────────────┬─────────────┘
                     ┌────────────┴─────────────┐
                     ▼                          ▼
              ExcelStore                 GoogleSheetsStore
        (wraps reader.py/writer.py)      (new, core/sheets_store.py)
                     │                          │
            data/portfolio.xlsx          Google Sheets API
```

Everything downstream of the reader — fetcher, engine math, `PortfolioSnapshot`,
the queue, every widget — is untouched.

---

## 3. The store contract

A new module `core/store.py` defines what any backend must provide. A
`typing.Protocol` describes the *shape* of an object (which methods it has)
without forcing backends to inherit from a common base class — mypy checks that
`ExcelStore` and `GoogleSheetsStore` both match it.

```python
from typing import Protocol

from portfolio.core.models import FixedIncomePosition, Position


class PortfolioStore(Protocol):
    async def read_positions(self) -> list[Position]: ...
    async def read_fixed_income(self) -> list[FixedIncomePosition]: ...
    async def set_quantity(self, ticker: str, quantity: float) -> None: ...
    async def set_avg_price(self, ticker: str, avg_price: float) -> None: ...
    async def add_position(self, position: Position) -> None: ...
    async def remove_position(self, ticker: str) -> None: ...
```

Behavioural rules every implementation must follow (these become the contract
tests in §10):

| Rule | Excel today | Sheets |
|------|-------------|--------|
| Positions come from the **first** tab, by position not name | `wb.worksheets[0]` | first entry of `spreadsheet.sheets` |
| Row 1 is a header and is skipped | yes | yes |
| Rows with blank ticker or quantity are skipped | yes | yes |
| Columns A–E = Ticker, Quantity, Exchange, Category, Avg Price | yes | yes |
| Missing `FixedIncome` tab → empty list, not an error | yes | yes |
| Tickers compared after `.strip()` | yes | yes |
| Unknown ticker on edit → `PositionNotFoundError` | yes | yes |
| Adding an existing ticker → `DuplicateTickerError` | yes | yes |
| Edits change only the targeted cells; other columns/tabs untouched | yes | yes |

A factory picks the backend from settings:

```python
def get_store(settings: Settings) -> PortfolioStore:
    match settings.storage_backend:
        case "excel":
            return ExcelStore(settings.excel_path)
        case "gsheets":
            return GoogleSheetsStore(
                spreadsheet_id=settings.gsheets_spreadsheet_id,
                credentials_path=settings.gsheets_credentials_path,
            )
```

---

## 4. Configuration

New fields in `core/settings.py`, all from environment variables as CLAUDE.md
requires:

| Env var | Type | Default | Notes |
|---------|------|---------|-------|
| `STORAGE_BACKEND` | `"excel"` \| `"gsheets"` | `"excel"` | `Literal` type, so a typo fails at startup. |
| `GSHEETS_SPREADSHEET_ID` | `str \| None` | `None` | The long ID in the sheet URL: `docs.google.com/spreadsheets/d/<ID>/edit`. Not a secret, but not hardcoded either. |
| `GSHEETS_CREDENTIALS_PATH` | `Path` | `~/.config/portfolio/service-account.json` | Lives **outside** the repo. Never committed. |

A Pydantic `model_validator(mode="after")` raises `ConfigError` when
`STORAGE_BACKEND=gsheets` and `GSHEETS_SPREADSHEET_ID` is missing or the
credentials file doesn't exist — failing at startup with a clear message beats
failing on the first refresh with a stack trace.

`.env.example` gains:

```bash
# Where the portfolio lives: "excel" (local file) or "gsheets" (shared Google Sheet)
STORAGE_BACKEND=excel
EXCEL_PATH=data/portfolio.xlsx
# GSHEETS_SPREADSHEET_ID=1AbC...xyz
# GSHEETS_CREDENTIALS_PATH=~/.config/portfolio/service-account.json
```

The default stays `excel`, so nothing changes for anyone until they opt in.

---

## 5. Authentication

The app needs a Google identity to call the Sheets API. Two options:

| | **Service account** (recommended for v1) | **OAuth user login** |
|---|---|---|
| What it is | A robot Google account with a JSON key file | Each person signs in with their own Google account in a browser |
| Setup | Create once in Google Cloud; share the sheet with the robot's email as Editor | Create an OAuth client + consent screen; each machine runs a one-time browser login |
| Per computer | Copy the JSON key to `~/.config/portfolio/` | Log in once; a refresh token is cached locally |
| Gotcha | The key is a long-lived secret: anyone holding it can edit the sheet. Distribute via a password manager, never via the repo or chat. | A consent screen left in "Testing" mode issues refresh tokens that **expire after 7 days**, forcing frequent re-logins. |
| Audit trail in Sheets | Edits show as the service account | Edits show as the person |

**Recommendation:** service account for v1. It is the least moving parts for one
owner on several computers, and also what a future always-on web UI (§11) would
need. If other *people* start editing, switch to OAuth so Sheets' version history
shows who changed what — the store interface doesn't change, only how
`GoogleSheetsStore` builds its client.

People who only want to *look* at the portfolio don't need the app or any
credentials: share the sheet with them as Viewer in Google Sheets.

### One-time Google Cloud setup

1. Create a Google Cloud project (free).
2. Enable the **Google Sheets API** for it.
3. Create a service account; create a JSON key for it and download it.
4. Save the key as `~/.config/portfolio/service-account.json`, `chmod 600`.
5. Open the Google Sheet → Share → add the service account's email
   (`…@….iam.gserviceaccount.com`) as **Editor**.

These steps go into `README.md` as part of Phase 3.

---

## 6. The Sheets backend

### 6.1 Library choice

| Option | For | Against |
|--------|-----|---------|
| **`gspread`** (+ `google-auth`) | Small, well-known API; handles auth, retries on 429; reads a tab as rows in one call | Synchronous; depends on `requests` |
| `google-api-python-client` | Official, covers every endpoint | Verbose, large, also synchronous |
| Raw REST via `httpx.AsyncClient` | Truly async; matches the "use httpx" rule | We'd hand-roll token refresh, retries and error mapping |

**Recommendation: `gspread`, wrapped in `asyncio.to_thread`** — exactly the
pattern `reader.py` and `writer.py` already use for the blocking `openpyxl`. The
CLAUDE.md rule "do not use `requests`" is about our own code; `requests` already
arrives transitively through `yfinance`, and our code would never import it. If
we later want a fully async backend, it's a second implementation of the same
Protocol, not a rewrite.

```bash
uv add gspread google-auth
```

### 6.2 Reading

One refresh = **one API call**. `values_batch_get` fetches both tabs together:

```python
def _read_all(self) -> tuple[list[list[object]], list[list[object]]]:
    first_tab = self._spreadsheet.sheet1.title          # first tab by position
    result = self._spreadsheet.values_batch_get(
        [f"'{first_tab}'!A2:E", "'FixedIncome'!A2:B"],
        params={"valueRenderOption": "UNFORMATTED_VALUE"},
    )
    ...
```

`read_positions` and `read_fixed_income` stay separate methods on the Protocol,
but `GoogleSheetsStore` can serve both from a single cached batch fetched in the
same refresh (the engine already requests them together in one `TaskGroup`). If
the `FixedIncome` range errors because the tab doesn't exist, return `[]`, same
as Excel.

**Row parsing is shared with Excel.** Factor the row-to-model logic out of
`_parse_excel` into `_row_to_position(row: Sequence[object]) -> Position | None`
in `reader.py`. Both backends feed it lists of cell values, so validation and
the skip-blank-rows rule live in exactly one place.

**`UNFORMATTED_VALUE` is mandatory.** Without it the API returns cells as
*displayed* strings. A sheet whose locale is Brazil shows `7,84` and
`R$ 1.234,56`; `float("7,84")` fails. Unformatted values come back as real JSON
numbers regardless of locale. The Sheets API also omits trailing empty cells,
so a row with no Avg Price arrives with 4 elements, not 5 — the shared row
parser already guards with `len(row) > n`.

### 6.3 Writing

Each edit mirrors `writer.py`: find the row by ticker, then change only the
needed cells.

| Store method | Sheets API call(s) |
|--------------|--------------------|
| `set_quantity` | read column A → find row → `update("B{row}", [[qty]], value_input_option="RAW")` |
| `set_avg_price` | read column A → find row → `update("E{row}", ...)` |
| `add_position` | read column A → reject duplicate → `append_row([...], value_input_option="RAW")` |
| `remove_position` | read column A → find row → `delete_rows(row)` |

`value_input_option="RAW"` stores numbers as numbers. The alternative,
`USER_ENTERED`, parses input as if typed into the UI, so the locale problem from
§6.2 comes back in reverse (`7.84` could be read as text, or as a date).

Every edit is 2–3 API calls (find, verify per §8, write). That's well inside
quota (§7).

---

## 7. Quotas and latency

Sheets API limits are per Google Cloud project: **300 read requests/minute** and
**60 requests/minute per user**.

| Load | Requests/min |
|------|--------------|
| One client refreshing every 30 s (1 batch read) | 2 |
| Five clients at 30 s | 10 |
| A burst of TUI edits (2 calls each) | a few |
| `portfolio import` applying 40 changes | ~80 — **over the per-user limit** |

The import case is the one to design for: `apply_plan` must send all changes in
**one** `batch_update` rather than looping over `set_quantity`/`add_position`.
That adds a sixth Protocol method:

```python
async def apply_changes(self, changes: Sequence[PositionChange]) -> None: ...
```

`ExcelStore` implements it by opening the workbook once and saving once (also
faster than today's one-save-per-change). gspread retries on HTTP 429 with
backoff, so a brief overrun degrades to a short delay, not a crash.

Latency: a read is ~200–600 ms versus ~10 ms for a local file. That's invisible
next to fetching dozens of prices from Yahoo, which the engine already waits on.

---

## 8. Concurrent edits

With one file per computer, conflicts were impossible (and syncing was the
problem). With one shared sheet, two clients can edit at once.

| Scenario | Outcome | Acceptable? |
|----------|---------|-------------|
| A and B change the quantity of *different* tickers | Both edits land | Yes |
| A and B change the quantity of the *same* ticker | Last write wins | Yes — same as two people typing in the sheet |
| A deletes row 7 while B updates the ticker at row 8 | B's "find" ran before A's delete; B writes into what is now row 8, i.e. the **wrong ticker** | **No** |

The third case is the real risk: Sheets has no transactions across separate
calls. Mitigation, cheap enough for v1: **verify right before writing.**
Re-read the single cell `A{row}` immediately before the write and abort with
`PositionNotFoundError` if it no longer holds the expected ticker. This shrinks
the window from "seconds" to "milliseconds"; it doesn't close it.

If this ever matters in practice, the robust fix is a stable row ID column (or
Sheets developer metadata on each row) so writes target the ID, not the row
number. Deferred — see §13.

Edits made directly in the Google Sheets UI are always fine: the app re-reads
the whole tab every refresh and holds no state between reads.

---

## 9. Errors

`ExcelParseError` is Excel-specific, but callers (`engine.py`,
`import_command.py`) catch it to mean "couldn't read the portfolio". Introduce a
backend-neutral parent and keep the old name working:

```python
class StorageError(Exception):
    """The portfolio data could not be read or written, whatever the backend."""

class ExcelParseError(StorageError): ...      # existing, now a subclass
class SheetsAccessError(StorageError): ...    # new: auth, 403/404, network, quota
```

Callers switch to `except StorageError` over time; until they do, Excel errors
still match `except ExcelParseError`. `GoogleSheetsStore` wraps every
`gspread.exceptions.APIError` and `google.auth.exceptions.GoogleAuthError` in
`SheetsAccessError` with a message that names the likely fix:

| Cause | Message hint |
|-------|--------------|
| 403 | "Share the sheet with `<service account email>` as Editor" |
| 404 | "Check `GSHEETS_SPREADSHEET_ID`" |
| Missing/invalid key file | "Check `GSHEETS_CREDENTIALS_PATH`" |
| 429 after retries | "Sheets API quota exceeded; try a longer refresh interval" |

The engine already logs and survives a failed refresh, so a transient network
blip shows stale numbers for one cycle, not a crash.

---

## 10. Testing

- **Contract tests** (`tests/unit/test_store_contract.py`): one test suite,
  parametrized over every store implementation, asserting every rule in the §3
  table. Run against `ExcelStore` (using a temp `.xlsx`, like
  `test_writer.py` does today) and `InMemoryStore`.
- **`InMemoryStore`** (`tests/fakes.py`): a list-backed implementation of the
  Protocol. Engine, importer and TUI tests use it instead of building xlsx
  files, which makes them faster and makes "mock the backend" (CLAUDE.md) a
  one-liner.
- **`GoogleSheetsStore` unit tests**: patch the gspread `Spreadsheet` object
  and assert the exact ranges, `valueRenderOption` and `value_input_option`
  sent, plus error mapping (§9). The locale case — a mocked response that would
  break without `UNFORMATTED_VALUE` — gets its own test.
- **Live integration test** (`tests/integration/test_sheets_live.py`): runs the
  same contract suite against a real scratch spreadsheet. Skipped unless
  `GSHEETS_TEST_SPREADSHEET_ID` is set, so CI and offline runs never need
  credentials.
- **Existing tests**: `test_writer.py` and `test_importer.py` keep passing
  through `ExcelStore`, proving Phase 0 changed no behaviour.

---

## 11. Migration steps

### Phase 0 — Store abstraction, Excel only (no behaviour change)

1. Add `StorageError`; make `ExcelParseError` a subclass.
2. Extract `_row_to_position` in `reader.py`.
3. Add `core/store.py` with the `PortfolioStore` Protocol and `ExcelStore`
   (thin wrapper over existing `reader`/`writer` functions).
4. Add `storage_backend` to settings and `get_store()`.
5. Switch `engine.run_engine`, `importer.build_plan`/`apply_plan` and the TUI
   edit handlers to take a `PortfolioStore` instead of a `Path`. The store is
   built once in `__main__.py` / `app.py` and passed in.
6. Add `apply_changes` and move `apply_plan` onto it.
7. Contract tests + `InMemoryStore`.

Shippable on its own: the app works exactly as before.

### Phase 1 — Read-only Sheets backend

1. `uv add gspread google-auth`.
2. `core/sheets_store.py`: `GoogleSheetsStore` with both reads; write methods
   raise `NotImplementedError` for now.
3. Settings validation for `GSHEETS_*` (§4); error mapping (§9).
4. Do the Google Cloud setup (§5) and upload a **copy** of the workbook.
5. Run the TUI with `STORAGE_BACKEND=gsheets` and compare totals against the
   Excel run.

### Phase 2 — Writes

1. Implement the four edit methods and `apply_changes` (§6.3, §7).
2. Add the verify-before-write guard (§8).
3. Live contract tests pass against the scratch sheet.

### Phase 3 — Move the real data and switch

1. **Upload:** in Google Drive, upload `data/portfolio.xlsx`, then
   *Open with → Google Sheets* (or enable "Convert uploads" in Drive settings).
   This keeps every tab, header and column; nothing is retyped.
2. **Check the conversion:** tab order (positions must be first), `FixedIncome`
   tab name spelled exactly, numeric cells are numbers (right-aligned) not text.
3. **Verify:** add a `portfolio verify-store` CLI command that reads positions
   and fixed income from both backends and prints any differences. Zero
   differences = safe to switch.
4. **Share** the sheet with the service account (Editor) and with any human
   viewers.
5. **Switch:** on each computer, copy the key file and set
   `STORAGE_BACKEND=gsheets` + `GSHEETS_SPREADSHEET_ID` in `.env`.
6. **Freeze the old file:** rename `data/portfolio.xlsx` to
   `data/portfolio.pre-sheets.xlsx` so nothing edits it by accident. It's your
   rollback.
7. Update `README.md` with setup steps; update CLAUDE.md's data-flow diagram
   (`[Excel File]` → `[PortfolioStore]`).

**Rollback** at any point: set `STORAGE_BACKEND=excel`. If edits were made in
Sheets since the switch, first *File → Download → .xlsx* from Google Sheets and
put it at `EXCEL_PATH`.

### Phase 4 — Later

- A `portfolio export` command that writes the sheet to `.xlsx` for offline
  backups (Sheets' own version history covers most of this already).
- A web UI (`ui/web/`) can now run on a server and read the same sheet — it
  needs only the store, not a copy of a file.

---

## 12. Interaction with the Transaction Ledger design

`docs/transactions-design.md` adds a `Transactions` tab and requires "append a
transaction" and "update quantity + avg price" to happen in **one operation**.
That fits this design well:

- The ledger becomes two more Protocol methods (`read_transactions`,
  `record_transaction`) rather than a new `Path`-based function.
- In Sheets, `spreadsheets.batchUpdate` is **atomic**: if any request in the
  batch fails, none are applied. Appending the ledger row and updating the
  position cells in one `batch_update` gives the all-or-nothing behaviour the
  ledger design needs — stronger than the Excel version, where a crash between
  two cell writes is possible but unlikely.
- Dates must be written as serial numbers (or ISO strings with `RAW`) and read
  with `dateTimeRenderOption=FORMATTED_STRING` to avoid locale-dependent
  parsing — same lesson as §6.2.

Whichever design ships first, the other should build on the store interface.

---

## 13. Risks and open questions

| Risk / question | Notes |
|-----------------|-------|
| Key file leaks | Long-lived edit access to your holdings. Keep outside the repo, `chmod 600`, rotate if a laptop is lost. Consider OAuth (§5) if this worries you. |
| Concurrent delete + edit hits the wrong row (§8) | Narrow window in v1. Add a stable row ID column if it ever happens. |
| Someone reorders tabs in the Sheets UI | "Positions = first tab" breaks silently. Option: make the tab name configurable (`GSHEETS_POSITIONS_TAB`) and stop relying on order. **Decide before Phase 1.** |
| Someone types text into a numeric column | Pydantic rejects the row; today the whole read fails. Should one bad row skip-and-log instead of blanking the portfolio? Same question applies to Excel. |
| `requests` arriving via `gspread` vs the "httpx only" rule | Accepted in §6.1 since `yfinance` already brings it in. Revisit if a fully async backend is wanted. |
| Should `.xlsx` support be removed eventually? | Keeping it costs little once the Protocol exists, and it keeps tests fast and offline. Recommend keeping it. |

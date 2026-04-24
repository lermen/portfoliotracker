# CLAUDE.md — Portfolio Tracker

## Project Overview

A near-real-time portfolio valuation tool that reads stock tickers and quantities
from an Excel file and displays aggregated portfolio value. Designed with a strict
backend/frontend separation to support multiple UIs (Textual TUI, web, etc.).

---

## Tech Stack

| Layer        | Tool / Library                             |
|--------------|--------------------------------------------|
| Package mgr  | `uv` (replaces pip + venv)                 |
| Data fetch   | `httpx` (async HTTP client) or `yfinance`  |
| Excel input  | `openpyxl` or `pandas`                     |
| Data models  | `pydantic` v2                              |
| Async core   | `asyncio` + `asyncio.Queue`                |
| TUI frontend | `textual`                                  |
| Testing      | `pytest` + `pytest-asyncio`                |
| Linting/fmt  | `ruff`                                     |
| Type checks  | `mypy` (strict mode)                       |

---

## Project Structure

```
portfolio-tracker/
├── pyproject.toml
├── uv.lock                  # Commit lockfile
├── CLAUDE.md
├── README.md
├── .env                     # Never commit
├── .env.example
├── src/
│   └── portfolio/
│       ├── __init__.py
│       ├── core/            # Business logic (NO UI deps)
│       │   ├── exceptions.py
│       │   ├── models.py
│       │   ├── settings.py
│       │   ├── reader.py
│       │   ├── fetcher.py
│       │   └── engine.py
│       └── ui/
│           ├── tui/
│           │   ├── app.py
│           │   ├── widgets.py
│           │   └── portfolio.tcss
│           └── web/         # Future UI
├── tests/
│   ├── unit/
│   └── integration/
└── data/
    └── portfolio.xlsx       # Gitignore if real data
```

---

## Architecture Rules

### Backend/Frontend Separation (hard rule)

- `src/portfolio/core/` must have **zero imports** from `src/portfolio/ui/`.
- The backend publishes `PortfolioSnapshot` objects to an `asyncio.Queue`.
- UIs only consume snapshots; they never call the fetcher directly.
- This enables multiple UIs (TUI + web) running on the same backend contract.

### Data Flow

1. `reader.py` parses Excel into a list of `Position`.
2. `fetcher.py` fetches prices asynchronously.
3. `engine.py` computes totals and publishes `PortfolioSnapshot` to the queue.
4. Each UI consumes from the queue and renders independently.

```
[Excel File] --> [reader.py] --> [fetcher.py async] --> [engine.py]
                                                              |
                                                       asyncio.Queue
                                                        /           \
                                               [tui/app.py]   [web/app.py]
```

---

## Coding Standards

### Python Version

- Target Python `>=3.12` and declare it in `pyproject.toml`.

### Type Hints (mandatory)

- Fully annotate all public functions and methods.
- Run `mypy --strict src/`.

```python
async def fetch_price(ticker: str) -> float:
    ...
```

### Pydantic Models

- Use Pydantic v2 for parsing and validation at the edges (Excel + API responses).

```python
from datetime import datetime
from pydantic import BaseModel, Field

class Position(BaseModel):
    ticker: str
    quantity: float = Field(gt=0)

class PositionValue(BaseModel):
    ticker: str
    quantity: float
    price: float
    value: float

class PortfolioSnapshot(BaseModel):
    positions: list[PositionValue]
    total_value: float
    currency: str = "USD"
    timestamp: datetime
```

### Async Patterns

- Prefer `asyncio.TaskGroup` (structured concurrency) over ad-hoc `gather` patterns.
- Use `asyncio.Queue` to decouple producer (engine) from consumers (UIs).
- Wrap blocking calls with `asyncio.to_thread()`.

```python
import asyncio

async def fetch_all_prices(tickers: list[str]) -> dict[str, float]:
    async with asyncio.TaskGroup() as tg:
        tasks = {t: tg.create_task(fetch_price(t)) for t in tickers}
    return {t: task.result() for t, task in tasks.items()}
```

### Errors and Logging

- Define explicit exceptions in `core/exceptions.py`: `ExcelParseError`, `PriceFetchError`, `ConfigError`.
- Never use `except: pass`.
- Use structured logging (`structlog`) instead of `print()`.

### Configuration

- All config via environment variables: refresh interval, file path, API keys.
- Use `pydantic-settings` for validated, typed config.
- Keep `.env` local; provide `.env.example`.

```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    excel_path: Path = Path("data/portfolio.xlsx")
    refresh_interval_seconds: int = 30
    model_config = SettingsConfigDict(env_file=".env")
```

---

## Textual TUI Guidelines

- All Textual code lives exclusively in `ui/tui/`.
- Consume from `asyncio.Queue` via periodic polling with `set_interval()`.
- Use `reactive` variables to trigger automatic widget updates.
- Put all styles in `portfolio.tcss`, not inline.
- Use `DataTable` widget for the main portfolio grid.

```python
from textual.app import App
from textual.reactive import reactive

class PortfolioApp(App):
    CSS_PATH = "portfolio.tcss"
    snapshot = reactive(None)

    def on_mount(self) -> None:
        self.set_interval(1.0, self.poll_queue)

    async def poll_queue(self) -> None:
        if not self.queue.empty():
            self.snapshot = await self.queue.get()

    def watch_snapshot(self, snapshot) -> None:
        self.query_one(PortfolioTable).update(snapshot)
```

---

## Dependency Management (uv)

```bash
# Initialize project
uv init portfolio-tracker
cd portfolio-tracker

# Runtime dependencies
uv add pydantic pydantic-settings httpx openpyxl textual structlog

# Development dependencies
uv add --group dev pytest pytest-asyncio mypy ruff

# Run, lint, type-check
uv run python -m portfolio
uv run ruff check src/
uv run ruff format src/
uv run mypy src/
```

Always commit `uv.lock`. Never commit `.venv/`.

---

## Testing

- Unit test `core/` in isolation — mock the price fetcher.
- Use `pytest-asyncio` for all async tests.
- Use Textual's `Pilot` test runner for TUI integration tests.

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## Tooling Config (pyproject.toml)

```toml
[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
strict = true
python_version = "3.12"
```

---

## Git Hygiene

- **Commit:** `pyproject.toml`, `uv.lock`, `CLAUDE.md`, `.env.example`
- **Gitignore:** `.venv/`, `.env`, `__pycache__/`, `*.pyc`, `data/*.xlsx` (if real data)
- Use conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`

---

## What NOT to Do

- Do NOT import `textual` or any UI library from `core/`.
- Do NOT use `time.sleep()` — use `await asyncio.sleep()`.
- Do NOT use `requests` — use `httpx` with an async client.
- Do NOT hardcode file paths, tickers, intervals, or secrets in source code.
- Do NOT use `asyncio.gather(..., return_exceptions=True)` without explicit
  per-exception handling — prefer `asyncio.TaskGroup`.

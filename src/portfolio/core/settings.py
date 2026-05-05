# This module loads and validates application configuration.
#
# Why environment variables?
# --------------------------
# Hardcoding values like file paths, refresh intervals, or API keys directly in
# source code is a bad practice:
#   - You might accidentally commit a secret (API key) to version control.
#   - Changing a setting requires editing and re-deploying code.
#
# The standard alternative is to read configuration from the process *environment*
# — a set of key=value pairs the operating system provides to every running program.
# On macOS/Linux you can set one with `export MY_VAR=value` before running the app.
# A `.env` file is a convenient way to collect these settings locally without
# having to export them manually every time.
#
# `pydantic-settings` bridges Pydantic's validation with environment variables,
# giving us both type safety and easy configuration in one step.

# `pathlib.Path` is the modern Python way to work with file-system paths.
# It is cross-platform (works on Windows, macOS, Linux) and supports operator
# overloading — you can write `Path("data") / "portfolio.xlsx"` instead of
# `"data" + os.sep + "portfolio.xlsx"`.
from pathlib import Path

# `pydantic-settings` extends Pydantic's `BaseModel` to also read field values
# from environment variables and `.env` files. If an environment variable named
# `EXCEL_PATH` is set, it automatically overrides the default defined below.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration, populated from environment variables.

    How it works:
    1. When `Settings()` is instantiated (at the bottom of this file), pydantic-settings
       looks for a `.env` file and reads any key=value pairs from it.
    2. It also checks the process environment for variables whose names match the
       field names (case-insensitive), e.g. `EXCEL_PATH` overrides `excel_path`.
    3. Environment variables take precedence over `.env` file values.
    4. If neither is set, the default value defined here is used.
    5. Pydantic validates the final value (e.g. converts "30" from the environment
       to the integer `30`).

    To override a setting without editing code:
        EXCEL_PATH=/home/user/my-portfolio.xlsx uv run python -m portfolio
    Or add it to your `.env` file:
        EXCEL_PATH=data/my-portfolio.xlsx
        REFRESH_INTERVAL_SECONDS=60
    """

    # Default: look for the portfolio spreadsheet at `data/portfolio.xlsx` relative
    # to the current working directory (wherever you run the app from).
    excel_path: Path = Path("data/portfolio.xlsx")

    # How many seconds to wait between price refreshes. 30 seconds balances
    # freshness with not hammering the Yahoo Finance API.
    refresh_interval_seconds: int = 30

    # `model_config` is a special Pydantic attribute (not a regular field).
    # `SettingsConfigDict` is a typed dictionary that configures pydantic-settings
    # behaviour — it gives IDE autocompletion and prevents typos in config keys.
    # `env_file=".env"` tells pydantic-settings to look for a `.env` file in the
    # current working directory when `Settings()` is created.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


# Module-level singleton: create one `Settings` instance and share it everywhere.
#
# Python caches module imports — the first time `from portfolio.core.settings import
# settings` is executed, Python runs this entire file and stores the result. Every
# subsequent import just returns the cached module without re-running the code. This
# means `settings` is created exactly once per process, and all callers share the
# same object.
settings = Settings()

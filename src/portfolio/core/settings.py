# `pathlib.Path` is the modern Python way to work with file-system paths.
# It is safer and more readable than plain strings: Path("data") / "file.xlsx"
# works on all operating systems (Windows, macOS, Linux) without manual slashes.
from pathlib import Path

# `pydantic-settings` extends Pydantic to read configuration from environment
# variables and .env files. This keeps secrets (API keys, paths) out of source
# code — you set them in the environment instead.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # These are the default values. They are overridden at runtime if an
    # environment variable with the same name (uppercased) is set, e.g.:
    #   EXCEL_PATH=/home/user/myportfolio.xlsx
    #   REFRESH_INTERVAL_SECONDS=60
    excel_path: Path = Path("data/portfolio.xlsx")
    refresh_interval_seconds: int = 30

    # `model_config` tells pydantic-settings where to look for a .env file.
    # `SettingsConfigDict` is a typed dictionary — it provides IDE autocompletion
    # and validation for config keys so you can't accidentally mistype them.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


# Module-level singleton: this object is imported everywhere that needs config.
# Because Python caches module imports, `settings` is only created once per
# process, so all callers share the exact same instance.
settings = Settings()

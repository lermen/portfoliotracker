class ExcelParseError(Exception):
    """Raised when the Excel file cannot be read or parsed."""


class PriceFetchError(Exception):
    """Raised when a price cannot be fetched for a ticker."""


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""

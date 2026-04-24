# In Python, you can define your own exception types by subclassing (inheriting from)
# the built-in `Exception` class. This lets you raise and catch errors that are
# specific to your application, making error handling much clearer.
#
# Instead of raising a generic `Exception("something went wrong")`, you raise
# `ExcelParseError("...")` — callers can then catch exactly the errors they care
# about without accidentally swallowing unrelated problems.


class ExcelParseError(Exception):
    """Raised when the Excel file cannot be read or parsed."""


class PriceFetchError(Exception):
    """Raised when a price cannot be fetched for a ticker."""


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""

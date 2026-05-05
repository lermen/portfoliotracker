# This module defines the custom exception types for the application.
#
# What is an exception?
# ---------------------
# In Python, an "exception" is a signal that something went wrong. When your code
# encounters a problem it can't handle, it "raises" an exception. If nothing
# catches it, the program prints an error message and stops.
#
# Python has many built-in exception types: `ValueError`, `FileNotFoundError`,
# `KeyError`, etc. But raising a generic `Exception("something failed")` makes it
# hard for callers to know *what kind* of problem occurred.
#
# Custom exceptions
# -----------------
# By defining our own exception classes, callers can catch exactly the errors they
# care about:
#
#   try:
#       positions = read_positions(path)
#   except ExcelParseError:
#       print("Could not read the spreadsheet — check the file format.")
#   except PriceFetchError:
#       print("Could not reach the price API — check your internet connection.")
#
# Without custom exceptions, both errors would look the same to the caller and
# there would be no way to handle them differently.
#
# How to define a custom exception
# ----------------------------------
# Simply subclass (inherit from) the built-in `Exception` class. The `pass`
# keyword is used when a class body would otherwise be empty — Python requires
# at least one statement inside a class or function, so `pass` is the standard
# placeholder meaning "nothing extra needed here".


class ExcelParseError(Exception):
    """Raised when the Excel file cannot be opened, read, or parsed.

    Examples of when this is raised:
    - The file does not exist at the configured path.
    - The file is corrupted or not a valid .xlsx file.
    - A required column is missing or contains an unexpected type.
    """


class PriceFetchError(Exception):
    """Raised when a live price cannot be fetched for a ticker.

    Examples of when this is raised:
    - The ticker symbol does not exist on Yahoo Finance.
    - The API returns no price data (e.g. the market is in a data outage).
    - A network error prevents the request from completing.
    """


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid.

    Examples of when this is raised:
    - A required environment variable is not set.
    - The value of a setting is the wrong type or out of range.
    """

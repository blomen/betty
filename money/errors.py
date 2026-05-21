"""Currency-related exception types."""

from __future__ import annotations


class CurrencyError(Exception):
    """Base class for all currency errors."""


class CurrencyMismatch(CurrencyError):
    """Raised when an operation tries to combine two different currencies."""


class RateNotConfigured(CurrencyError):
    """Raised when a conversion is attempted before the SEK/USD rate has been
    configured via `money.configure()`."""

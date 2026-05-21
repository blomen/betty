"""money — currency-safe value type and SEK rate table.

Import the public API from here:

    from money import Money, Currency, configure

Call `configure()` once at process startup with the SEK-per-USD rate before
any cross-currency conversion is attempted.
"""

from __future__ import annotations

from . import currency, money, rates
from .currency import Currency
from .errors import CurrencyError, CurrencyMismatch, RateNotConfigured
from .money import Money
from .rates import configure, convert, is_configured, reset

__all__ = [
    "Money",
    "Currency",
    "CurrencyError",
    "CurrencyMismatch",
    "RateNotConfigured",
    "configure",
    "convert",
    "is_configured",
    "reset",
]

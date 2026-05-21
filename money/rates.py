"""Process-global SEK/USD exchange rate.

Set once at process startup via `configure()`; pure thereafter. SEK is the
pivot currency — every conversion goes amount -> SEK -> target.
"""

from __future__ import annotations

from .currency import Currency
from .errors import RateNotConfigured

# SEK value of one USD. None until configure() runs.
_sek_per_usd: float | None = None


def configure(sek_per_usd: float) -> None:
    """Set the process-global SEK-per-USD rate. Idempotent — last call wins."""
    global _sek_per_usd
    rate = float(sek_per_usd)
    if rate <= 0:
        raise ValueError(f"sek_per_usd must be positive, got {rate}")
    _sek_per_usd = rate


def is_configured() -> bool:
    """True once `configure()` has been called."""
    return _sek_per_usd is not None


def reset() -> None:
    """Clear the configured rate. Test-support only."""
    global _sek_per_usd
    _sek_per_usd = None


def _rate_to_sek(currency: Currency) -> float:
    """SEK value of one unit of `currency`. Raises RateNotConfigured when the
    USD rate is needed but unset."""
    if currency is Currency.SEK:
        return 1.0
    if _sek_per_usd is None:
        raise RateNotConfigured("currency rate not configured — call money.configure() at startup")
    return _sek_per_usd


def convert(amount: float, frm: Currency, to: Currency) -> float:
    """Convert `amount` from one currency to another, pivoting through SEK.

    Identity conversion (frm is to) returns `amount` unchanged and needs no
    configured rate.
    """
    if frm is to:
        return amount
    sek = amount * _rate_to_sek(frm)
    return sek / _rate_to_sek(to)

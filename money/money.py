"""Money — a currency-tagged amount that refuses to mix currencies."""

from __future__ import annotations

from dataclasses import dataclass

from . import rates
from .currency import Currency
from .errors import CurrencyMismatch


@dataclass(frozen=True, repr=False)
class Money:
    """An amount of money in a specific currency.

    Arithmetic between two Money values requires the same currency or raises
    CurrencyMismatch. Multiplication and division are only by a plain scalar.
    """

    amount: float
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.currency, Currency):
            raise TypeError(f"currency must be a Currency, got {self.currency!r}")
        # Coerce int -> float so repr and type stay consistent. A frozen
        # dataclass needs object.__setattr__ to mutate during __post_init__.
        object.__setattr__(self, "amount", float(self.amount))

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        """A zero amount in `currency`."""
        return cls(0.0, currency)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0.0

    def __bool__(self) -> bool:
        return self.amount != 0.0

    def __repr__(self) -> str:
        return f"Money({self.amount:.2f}, {self.currency})"

    def _check_same_currency(self, other: object) -> Money:
        """Validate `other` is Money in the same currency; return it typed."""
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if self.currency is not other.currency:
            raise CurrencyMismatch(f"cannot combine {self.currency} and {other.currency}")
        return other

    def __add__(self, other: object) -> Money:
        other = self._check_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: object) -> Money:
        other = self._check_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, scalar: object) -> Money:
        if isinstance(scalar, Money):
            raise TypeError("cannot multiply Money by Money")
        if not isinstance(scalar, (int, float)):
            return NotImplemented
        return Money(self.amount * scalar, self.currency)

    __rmul__ = __mul__

    def __truediv__(self, scalar: object) -> Money:
        if isinstance(scalar, Money):
            raise TypeError("cannot divide Money by Money")
        if not isinstance(scalar, (int, float)):
            return NotImplemented
        return Money(self.amount / scalar, self.currency)

    def __lt__(self, other: object) -> bool:
        other = self._check_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        other = self._check_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: object) -> bool:
        other = self._check_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: object) -> bool:
        other = self._check_same_currency(other)
        return self.amount >= other.amount

    def convert(self, to: Currency) -> Money:
        """Return this amount expressed in `to`, using the configured rate.

        Identity conversion needs no configured rate; any other conversion
        raises RateNotConfigured if `money.configure()` has not run.
        """
        return Money(rates.convert(self.amount, self.currency, to), to)

    def rounded(self, dp: int = 2) -> Money:
        """Return this amount rounded to `dp` decimal places (currency kept)."""
        return Money(round(self.amount, dp), self.currency)

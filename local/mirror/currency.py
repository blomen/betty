"""Currency awareness for arb sizing.

Providers run in DIFFERENT currencies (see CLAUDE.md). Anything that adds /
subtracts / hedges across providers without converting first is wrong by 5-10×.

This module owns:
- the per-provider currency lookup
- SEK-anchored FX rates (kept in sync with backend/src/config/providers.yaml —
  values diverging from the backend's `exchange_rate_sek` are a bug)
- conversion helpers used by arb_math
- the Money(amount, currency) value type — the typed way to carry an amount
  through code that ALSO knows what currency it's in. Money refuses to
  silently compose across currencies: addition/subtraction enforce matching
  currency, scalar multiplication preserves the unit, conversion is explicit
  via .to(). This was the single missing abstraction behind every cross-
  currency bug fixed in the May 2026 review.

SEK is the base unit because the user is SEK-funded; all conversions route
through SEK.
"""

from __future__ import annotations

from dataclasses import dataclass

# Keep in sync with backend/src/config/providers.yaml — these are rough FX
# anchors, not live rates. Stable values change <2% over months, and arb sizing
# rounds to cents, so a small drift is tolerable. A larger drift is a bug.
_FX_SEK_PER_UNIT: dict[str, float] = {
    "SEK": 1.0,
    "USD": 10.5,
    "USDC": 10.5,
    "GBP": 13.3,
    "EUR": 11.5,
}

# Provider → native currency. The Swedish soft books all play in SEK; the
# unlimited counter pool spans SEK (pinnacle), USDC (polymarket, cloudbet),
# and USD (kalshi). Add new providers here AND in providers.yaml.
_PROVIDER_CURRENCY: dict[str, str] = {
    # Sharp / unlimited
    "pinnacle": "SEK",
    "polymarket": "USDC",
    "cloudbet": "USDC",
    "kalshi": "USD",
    "smarkets": "GBP",
}


def provider_currency(provider_id: str) -> str:
    """Return the native currency code for a provider.

    Defaults to SEK (every Swedish soft book this stack supports is SEK; any
    non-SEK provider must be explicitly listed in _PROVIDER_CURRENCY).
    """
    return _PROVIDER_CURRENCY.get(provider_id, "SEK")


def to_sek(amount: float, currency: str) -> float:
    """Convert `amount` from `currency` into SEK."""
    if currency == "SEK":
        return amount
    rate = _FX_SEK_PER_UNIT.get(currency)
    if rate is None:
        raise ValueError(f"unknown currency: {currency!r}")
    return amount * rate


def from_sek(amount_sek: float, currency: str) -> float:
    """Convert `amount_sek` from SEK into `currency`."""
    if currency == "SEK":
        return amount_sek
    rate = _FX_SEK_PER_UNIT.get(currency)
    if rate is None:
        raise ValueError(f"unknown currency: {currency!r}")
    return amount_sek / rate


def convert(amount: float, src: str, dst: str) -> float:
    """Convert `amount` from `src` to `dst` (routes via SEK)."""
    if src == dst:
        return amount
    return from_sek(to_sek(amount, src), dst)


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in a specific currency. The typed way to carry money.

    Construct directly: Money(100, "SEK"). Arithmetic refuses to silently
    cross currencies — addition/subtraction require matching currency,
    scalar multiplication preserves the unit, conversion is explicit:

        sek = Money(100, "SEK")
        usdc = sek.to("USDC")              # explicit conversion
        bad = sek + Money(10, "USD")       # raises ValueError
        ok = sek + Money(10, "USD").to("SEK")
        scaled = sek * 1.5                 # Money(150, "SEK")

    Designed so `sum([Money, ...])` works when all elements share a currency
    (the 0 starting value coerces via __radd__).
    """

    amount: float
    currency: str

    def to(self, target: str) -> Money:
        """Convert into another currency (routes via SEK)."""
        if target == self.currency:
            return self
        return Money(convert(self.amount, self.currency, target), target)

    def round(self, places: int = 2) -> Money:
        """Round to N decimal places (default: cents)."""
        return Money(round(self.amount, places), self.currency)

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError(
                f"cannot add {self.currency} and {other.currency} — "
                f"convert first via .to({self.currency!r})"
            )
        return Money(self.amount + other.amount, self.currency)

    def __radd__(self, other: object) -> Money:
        # Support sum([Money, ...]) which starts from 0
        if other == 0:
            return self
        return NotImplemented

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError(
                f"cannot subtract {other.currency} from {self.currency} — "
                f"convert first via .to({self.currency!r})"
            )
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, scalar: float | int) -> Money:
        if isinstance(scalar, Money):
            raise TypeError("cannot multiply Money by Money — meaningless dimensions")
        return Money(self.amount * scalar, self.currency)

    def __rmul__(self, scalar: float | int) -> Money:
        return self.__mul__(scalar)

    def __truediv__(self, scalar: float | int) -> Money:
        if isinstance(scalar, Money):
            raise TypeError(
                "Money / Money returns a ratio, not Money — compute amount/amount directly"
            )
        return Money(self.amount / scalar, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money) or other.currency != self.currency:
            return NotImplemented
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money) or other.currency != self.currency:
            return NotImplemented
        return self.amount <= other.amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money) or other.currency != self.currency:
            return NotImplemented
        return self.amount > other.amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money) or other.currency != self.currency:
            return NotImplemented
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency}"


def money_from_provider(amount: float, provider_id: str) -> Money:
    """Construct Money tagged with a provider's native currency."""
    return Money(amount, provider_currency(provider_id))

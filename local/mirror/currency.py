"""Currency awareness for arb sizing.

Providers run in DIFFERENT currencies (see CLAUDE.md). Anything that adds /
subtracts / hedges across providers without converting first is wrong by 5-10×.

This module owns:
- the per-provider currency lookup
- SEK-anchored FX rates (kept in sync with backend/src/config/providers.yaml —
  values diverging from the backend's `exchange_rate_sek` are a bug)
- conversion helpers used by arb_math

SEK is the base unit because the user is SEK-funded; all conversions route
through SEK.
"""

from __future__ import annotations

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

"""Currency enum with provider/DB string normalization."""

from __future__ import annotations

from enum import Enum


class Currency(Enum):
    """A supported currency. Values are ISO-style codes."""

    SEK = "SEK"
    USD = "USD"
    GBP = "GBP"

    @classmethod
    def parse(cls, raw: str) -> Currency:
        """Normalize a provider/DB currency string to a Currency.

        Accepts case-insensitive codes and treats Polymarket's "USDC" as USD.
        Raises ValueError for anything unrecognized — currency is never guessed.
        """
        key = (raw or "").strip().upper()
        if key == "USDC":
            key = "USD"
        try:
            return cls[key]
        except KeyError:
            raise ValueError(f"unknown currency: {raw!r}") from None

    def __str__(self) -> str:
        return self.value

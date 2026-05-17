"""Shared types for API-based recorders."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RecoveredPosition:
    """A single open position fetched from a provider's user-portfolio API."""

    provider_id: str
    provider_bet_id: str  # poly conditionId / kalshi order_id — for dedup
    event_name: str  # full market title from API
    outcome_name: str  # team/player name (poly: "MIBR" / kalshi: "Miomir Kecmanovic")
    odds: float  # decimal odds, fee-adjusted
    stake: float  # native currency (USDC for poly, USD for kalshi)
    currency: str  # "USDC" / "USD"
    raw: dict  # original API payload for debugging


@dataclass
class RecorderResult:
    provider_id: str
    fetched: int = 0
    inserted: int = 0
    skipped_dup: int = 0
    skipped_unmatched: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.provider_id}: fetched={self.fetched} inserted={self.inserted} "
            f"skipped_dup={self.skipped_dup} skipped_unmatched={self.skipped_unmatched} "
            f"errors={len(self.errors)}"
        )

"""
Abstract base classes for bet placement.

Each provider type implements BetPlacer to handle:
1. Session management (cookies/tokens from user's browser)
2. Event ID resolution (canonical -> provider-specific)
3. Odds verification (check current odds before placing)
4. Bet submission + confirmation parsing
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import logging

logger = logging.getLogger(__name__)


class PlacementStatus(str, Enum):
    """Outcome of a bet placement attempt."""
    SUCCESS = "success"
    ODDS_CHANGED = "odds_changed"           # Odds moved but still +EV
    ODDS_DROPPED = "odds_dropped"           # Edge gone, bet skipped
    REJECTED = "rejected"                   # Provider rejected the bet
    INSUFFICIENT_BALANCE = "insufficient_balance"
    EVENT_NOT_FOUND = "event_not_found"     # Can't match to provider event
    MARKET_SUSPENDED = "market_suspended"   # Market closed/suspended
    SESSION_EXPIRED = "session_expired"     # Need to re-login
    RATE_LIMITED = "rate_limited"           # Too many bets, slow down
    ERROR = "error"                         # Unexpected error


@dataclass
class PlacementRequest:
    """What we want to bet on."""
    # Canonical identifiers (from our DB)
    event_id: str               # canonical event ID: "football:man_utd:arsenal:20260218"
    provider_id: str            # e.g., "unibet"
    market: str                 # "1x2", "moneyline", "spread", "total"
    outcome: str                # "home", "away", "draw", "over", "under"
    point: Optional[float] = None  # For spread/total markets

    # Bet parameters
    expected_odds: float = 0.0  # Odds at detection time
    stake: float = 0.0          # Amount in provider currency (kr)
    is_bonus: bool = False
    bonus_type: Optional[str] = None  # "freebet", "deposit_match"

    # Context for event resolution
    home_team: str = ""
    away_team: str = ""
    sport: str = ""
    start_time: str = ""        # ISO format

    # Provider-specific IDs (if already known from extraction cache)
    provider_event_id: Optional[str] = None
    provider_betoffer_id: Optional[str] = None
    provider_outcome_id: Optional[str] = None

    # Tolerance
    min_acceptable_odds: float = 0.0  # 0 = auto-calc (97% of expected)


@dataclass
class PlacementResult:
    """What happened when we tried to place."""
    status: PlacementStatus

    # On success
    confirmation_id: Optional[str] = None   # Provider's bet reference
    actual_odds: Optional[float] = None     # Odds at placement time
    actual_stake: Optional[float] = None    # Actual stake placed

    # On failure
    error_message: Optional[str] = None
    current_odds: Optional[float] = None    # What odds are now

    # Metadata
    provider_event_id: Optional[str] = None
    provider_market_id: Optional[str] = None
    latency_ms: float = 0.0


class BetPlacer(ABC):
    """
    Abstract bet placement interface.

    Each provider type (Kambi, Altenar, Pinnacle, browser-based)
    implements this to handle bet submission.

    Usage:
        placer = SomePlacer(provider_id="unibet")
        await placer.initialize()
        result = await placer.execute(request)
    """

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize the placer (validate session, etc.).
        Returns True if ready to place bets.
        """

    @abstractmethod
    async def check_session(self, provider_id: str) -> bool:
        """Check if the authenticated session for this provider is still valid."""

    @abstractmethod
    async def resolve_event(self, request: PlacementRequest) -> Optional[dict]:
        """
        Resolve canonical event to provider-specific IDs.

        Returns dict with provider-specific IDs needed for placement:
            {"event_id": "...", "betoffer_id": "...", "outcome_id": "...", ...}
        Or None if event can't be found.
        """

    @abstractmethod
    async def get_current_odds(self, request: PlacementRequest, resolved: dict) -> Optional[float]:
        """
        Get current odds for the resolved event/market/outcome.
        Returns None if market is suspended or unavailable.
        """

    @abstractmethod
    async def place_bet(self, request: PlacementRequest, resolved: dict) -> PlacementResult:
        """
        Actually submit the bet to the provider.
        `resolved` contains provider-specific IDs from resolve_event().
        """

    async def execute(self, request: PlacementRequest) -> PlacementResult:
        """
        Full placement pipeline: resolve -> verify odds -> place.

        This is the main entry point. Subclasses typically don't override this.
        """
        start = time.time()

        # 1. Resolve event to provider-specific IDs
        try:
            resolved = await self.resolve_event(request)
        except Exception as e:
            logger.error(f"[{request.provider_id}] Event resolution failed: {e}")
            return PlacementResult(
                status=PlacementStatus.ERROR,
                error_message=f"Event resolution failed: {e}",
                latency_ms=(time.time() - start) * 1000,
            )

        if not resolved:
            return PlacementResult(
                status=PlacementStatus.EVENT_NOT_FOUND,
                error_message=f"Could not find event on {request.provider_id}: {request.home_team} vs {request.away_team}",
                latency_ms=(time.time() - start) * 1000,
            )

        # 2. Check current odds
        try:
            current_odds = await self.get_current_odds(request, resolved)
        except Exception as e:
            logger.error(f"[{request.provider_id}] Odds check failed: {e}")
            return PlacementResult(
                status=PlacementStatus.ERROR,
                error_message=f"Odds check failed: {e}",
                latency_ms=(time.time() - start) * 1000,
            )

        if current_odds is None:
            return PlacementResult(
                status=PlacementStatus.MARKET_SUSPENDED,
                error_message="Market suspended or unavailable",
                latency_ms=(time.time() - start) * 1000,
            )

        # 3. Verify odds haven't dropped too far
        min_odds = request.min_acceptable_odds
        if min_odds <= 0:
            # Default: accept if odds dropped no more than 3%
            min_odds = request.expected_odds * 0.97

        if current_odds < min_odds:
            return PlacementResult(
                status=PlacementStatus.ODDS_DROPPED,
                current_odds=current_odds,
                error_message=f"Odds dropped: {current_odds:.3f} < {min_odds:.3f} min",
                latency_ms=(time.time() - start) * 1000,
            )

        # 4. Log if odds changed (but still acceptable)
        if abs(current_odds - request.expected_odds) > 0.001:
            logger.info(
                f"[{request.provider_id}] Odds moved: {request.expected_odds:.3f} -> {current_odds:.3f} "
                f"(still above min {min_odds:.3f})"
            )

        # 5. Place the bet
        try:
            result = await self.place_bet(request, resolved)
        except Exception as e:
            logger.error(f"[{request.provider_id}] Bet placement failed: {e}", exc_info=True)
            return PlacementResult(
                status=PlacementStatus.ERROR,
                error_message=f"Placement failed: {e}",
                current_odds=current_odds,
                latency_ms=(time.time() - start) * 1000,
            )

        result.latency_ms = (time.time() - start) * 1000
        if result.actual_odds is None:
            result.actual_odds = current_odds

        return result

    @abstractmethod
    async def close(self):
        """Clean up resources."""

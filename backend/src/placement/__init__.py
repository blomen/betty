"""
BankrollBBQ Placement — URL navigation to provider sites.

No Chrome/CDP session management. Users authenticate via BankID.
"""

from .base import (
    PlacementStatus,
    PlacementRequest,
    PlacementResult,
    BetPlacer,
)
from .placement_service import PlacementService

__all__ = [
    "PlacementStatus",
    "PlacementRequest",
    "PlacementResult",
    "BetPlacer",
    "PlacementService",
]

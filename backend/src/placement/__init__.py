"""
BankrollBBQ Placement — URL navigation + CDP bet slip filling.

PlacementService: URL-based navigation to provider sites.
SlipFillerService: CDP-based bet slip auto-fill (navigate + click odds + fill stake).
"""

from .base import (
    PlacementStatus,
    PlacementRequest,
    PlacementResult,
    BetPlacer,
)
from .placement_service import PlacementService
from .slip_filler import SlipFillerService, SlipRequest, SlipResult, SlipStatus

__all__ = [
    "PlacementStatus",
    "PlacementRequest",
    "PlacementResult",
    "BetPlacer",
    "PlacementService",
    "SlipFillerService",
    "SlipRequest",
    "SlipResult",
    "SlipStatus",
]

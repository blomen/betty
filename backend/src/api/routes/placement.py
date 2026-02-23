"""
Placement API — navigate to provider sites.

No Chrome/CDP session management. Users authenticate via BankID in their browser.

Endpoints:
  POST /api/placement/navigate  — Get URL to open a provider's match page
"""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ...placement.placement_service import PlacementService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/placement", tags=["placement"])

# Singleton placement service
_placement_service: Optional[PlacementService] = None


def get_placement_service() -> PlacementService:
    """Get or create the singleton PlacementService."""
    global _placement_service
    if _placement_service is None:
        _placement_service = PlacementService()
    return _placement_service


class NavigateRequest(BaseModel):
    """Navigate browser to a match page."""
    provider_id: str
    provider_meta: Optional[dict] = None
    home_team: str = ""
    away_team: str = ""
    event_id: str = ""


@router.post("/navigate")
async def navigate_to_event(req: NavigateRequest):
    """
    Get URL for a provider's match page.

    Returns the URL for the frontend to open in the user's browser.
    """
    service = get_placement_service()
    result = await service.navigate_to_event(
        provider_id=req.provider_id,
        provider_meta=req.provider_meta,
        home_team=req.home_team,
        away_team=req.away_team,
        event_id=req.event_id,
    )
    return result

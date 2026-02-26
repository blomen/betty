"""
Placement API — navigate to provider sites.

Returns URLs + window names for the frontend to open.
The frontend uses named windows (window.open with target name)
so the same provider tab is reused instead of opening new ones.

Endpoints:
  POST /api/placement/navigate  — Get URL for a provider's event page
  POST /api/placement/deposit   — Get URL for a provider's deposit page
"""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ...placement.placement_service import PlacementService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/placement", tags=["placement"])

_placement_service: Optional[PlacementService] = None


def get_placement_service() -> PlacementService:
    global _placement_service
    if _placement_service is None:
        _placement_service = PlacementService()
    return _placement_service


class NavigateRequest(BaseModel):
    provider_id: str
    provider_meta: Optional[dict] = None
    home_team: str = ""
    away_team: str = ""
    event_id: str = ""


class DepositRequest(BaseModel):
    provider_id: str


@router.post("/navigate")
async def navigate_to_event(req: NavigateRequest):
    """Get URL + window name for a provider's event page."""
    service = get_placement_service()
    return await service.navigate_to_event(
        provider_id=req.provider_id,
        provider_meta=req.provider_meta,
        home_team=req.home_team,
        away_team=req.away_team,
        event_id=req.event_id,
    )


@router.post("/deposit")
async def navigate_to_deposit(req: DepositRequest):
    """Get URL + window name for a provider's deposit/cashier page."""
    service = get_placement_service()
    return await service.navigate_to_deposit(req.provider_id)

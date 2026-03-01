"""
Placement API — navigate to provider sites + CDP-based bet slip filling.

Returns URLs + window names for the frontend to open.
The frontend uses named windows (window.open with target name)
so the same provider tab is reused instead of opening new ones.

Endpoints:
  POST /api/placement/navigate   — Get URL for a provider's event page
  POST /api/placement/deposit    — Get URL for a provider's deposit page
  POST /api/placement/my-bets    — Get URL for a provider's my bets page
  POST /api/placement/results    — Get URL for a provider's results page
  POST /api/placement/fill-slip  — Navigate + auto-fill bet slip via CDP
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


class ProviderRequest(BaseModel):
    provider_id: str


@router.post("/my-bets")
async def navigate_to_my_bets(req: ProviderRequest):
    """Get URL + window name for a provider's my bets / bet history page."""
    service = get_placement_service()
    return await service.navigate_to_my_bets(req.provider_id)


@router.post("/results")
async def navigate_to_results(req: ProviderRequest):
    """Get URL + window name for a provider's results/scores page."""
    service = get_placement_service()
    return await service.navigate_to_results(req.provider_id)


class FillSlipRequest(BaseModel):
    provider_id: str
    event_id: str = ""
    market: str = ""
    outcome: str = ""
    point: Optional[float] = None
    stake: float = 0
    expected_odds: float = 0
    provider_meta: Optional[dict] = None
    home_team: str = ""
    away_team: str = ""


@router.post("/fill-slip")
async def fill_slip(req: FillSlipRequest):
    """Navigate to provider and auto-fill bet slip via CDP.

    Opens the event page in Chrome, clicks the correct odds button,
    and fills the stake amount. User manually confirms on the provider site.

    Returns:
        status: "ready" | "navigated_only" | "error"
        message: Human-readable description
        url: The URL navigated to
        actual_odds: Odds read from the filled slip (if available)
    """
    service = get_placement_service()
    return await service.fill_slip(
        provider_id=req.provider_id,
        event_id=req.event_id,
        market=req.market,
        outcome=req.outcome,
        point=req.point,
        stake=req.stake,
        expected_odds=req.expected_odds,
        provider_meta=req.provider_meta,
        home_team=req.home_team,
        away_team=req.away_team,
    )

"""Opportunities API routes - value betting opportunities."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...services import OpportunityService
from ...services.play_service import PlaySessionService
from ...repositories import ProfileRepo
from ..deps import get_db
from ..schemas import BonusMatchRequest

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


def _get_service(db: Session = Depends(get_db)) -> OpportunityService:
    return OpportunityService(db)


@router.get("")
async def list_opportunities(
    type: Optional[str] = None,
    active_only: bool = True,
    provider1: Optional[str] = None,
    provider2: Optional[str] = None,
    providers: Optional[str] = None,
    market: Optional[str] = None,
    sport: Optional[str] = None,
    min_value: Optional[float] = None,
    limit: int = 500,
    service: OpportunityService = Depends(_get_service),
):
    """Get current value/bonus opportunities with enhanced filtering and stake recommendations."""
    return service.list_opportunities(
        type=type,
        provider1=provider1,
        provider2=provider2,
        providers=providers,
        market=market,
        sport=sport,
        min_value=min_value,
        limit=min(limit, 2000),
    )


@router.get("/clusters")
async def list_clusters(
    service: OpportunityService = Depends(_get_service),
):
    """Get all available provider clusters with balance info."""
    return {"clusters": service.get_clusters()}


@router.get("/cluster-summary")
async def cluster_summary(
    cluster: str,
    service: OpportunityService = Depends(_get_service),
):
    """Get provider status summary for a cluster (balance, wagering, limits)."""
    return service.get_cluster_summary(cluster)


@router.post("/bonus/match")
async def match_bonus_bet(
    data: BonusMatchRequest,
    service: OpportunityService = Depends(_get_service),
):
    """Find the best hedge for a bonus bet."""
    result = service.find_hedge(
        event_id=data.event_id,
        market=data.market,
        anchor_provider=data.anchor_provider,
        anchor_outcome=data.anchor_outcome,
        anchor_odds=data.anchor_odds,
        anchor_stake=data.anchor_stake,
        counterpart_providers=data.counterpart_providers,
        is_free_bet=data.is_free_bet,
    )

    if not result:
        raise HTTPException(
            404,
            "No suitable hedge found (all hedges are same-provider or no valid options)"
        )

    return result


@router.get("/dutch-workflow")
async def dutch_workflow(
    providers: str,
    major_only: bool = False,
    counterpart_providers: Optional[str] = None,
    limit: int = 50,
    service: OpportunityService = Depends(_get_service),
):
    """Live-scan dutch opportunities for specific anchor providers."""
    provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    if not provider_list:
        raise HTTPException(400, "At least one provider required")
    counterpart_list = (
        [p.strip() for p in counterpart_providers.split(",") if p.strip()]
        if counterpart_providers else None
    )
    return service.scan_dutch_workflow(
        anchor_providers=provider_list,
        major_only=major_only,
        counterpart_providers=counterpart_list,
        limit=min(limit, 100),
    )


@router.get("/bonus/scan")
async def scan_bonus_opportunities(
    anchor_provider: str,
    limit: int = 10,
    include_negative: bool = True,
    service: OpportunityService = Depends(_get_service),
):
    """Scan for bonus opportunities at anchor provider vs Pinnacle."""
    return service.scan_bonus(
        anchor_provider=anchor_provider,
        limit=limit,
        include_negative=include_negative,
    )


@router.get("/play/session")
async def get_play_session(db: Session = Depends(get_db)):
    """Get session data for Play panel: clusters, siblings, lifecycle states."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    service = PlaySessionService(db)
    return service.get_session(profile.id)

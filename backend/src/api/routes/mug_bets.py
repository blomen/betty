"""
Mug Bets API Routes

Endpoints for:
- Scanning mug bet opportunities
- Checking provider mug bet status
- Auto-placing mug bets
- Viewing mug bet history
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db
from ...db.models import (
    Bet, Provider, Profile, RiskConfig,
)
from ...analysis.mug_scanner import MugBetScanner, MugBetOpportunity
from ...analysis.mug_automator import MugBetAutomator

router = APIRouter(prefix="/api/mug-bets", tags=["mug-bets"])


# ============ Schemas ============


class MugBetOpportunityResponse(BaseModel):
    """A mug bet opportunity."""

    event_id: str
    market: str
    outcome: str
    provider: str
    provider_odds: float
    fair_odds: float
    edge_pct: float
    implied_prob: float
    recreational_score: float
    home_team: Optional[str]
    away_team: Optional[str]
    sport: Optional[str]
    league: Optional[str]


class MugBetScanResponse(BaseModel):
    """Response from mug bet scan."""

    provider_id: str
    opportunities: list[MugBetOpportunityResponse]
    count: int
    config: dict


class MugBetRequirementResponse(BaseModel):
    """Mug bet requirement for a provider."""

    provider_id: str
    needs_mug_bets: bool
    reason: Optional[str]
    count_needed: int
    account_age_days: Optional[int]
    total_bets: int
    ev_bets: int
    mug_bets: int
    ev_quality_ratio: float
    message: str


class MugBetStatusResponse(BaseModel):
    """Status of mug bet needs across all providers."""

    providers: list[MugBetRequirementResponse]
    total_needing_mug_bets: int
    total_mug_bets_needed: int


class PlacedMugBetResponse(BaseModel):
    """A placed (or would-be-placed) mug bet."""

    provider_id: str
    event_id: str
    outcome: str
    odds: float
    stake: float
    edge_pct: float
    reason: str
    home_team: Optional[str]
    away_team: Optional[str]
    sport: Optional[str]
    bet_id: Optional[int]
    placed: bool


class AutoPlaceRequest(BaseModel):
    """Request to auto-place mug bets."""

    provider_id: Optional[str] = None  # If None, place for all providers
    count: Optional[int] = None  # If None, auto-detect
    dry_run: bool = False


class AutoPlaceResponse(BaseModel):
    """Response from auto-place operation."""

    dry_run: bool
    results: dict[str, list[PlacedMugBetResponse]]
    total_placed: int
    total_stake: float


class MugBetHistoryItem(BaseModel):
    """A historical mug bet."""

    id: int
    provider_id: str
    event_id: Optional[str]
    market: Optional[str]
    outcome: Optional[str]
    odds: float
    stake: float
    result: str
    payout: float
    mug_bet_reason: Optional[str]
    ev_at_placement: Optional[float]
    placed_at: str
    settled_at: Optional[str]


class MugBetHistoryResponse(BaseModel):
    """Response with mug bet history."""

    bets: list[MugBetHistoryItem]
    count: int
    total_staked: float
    total_payout: float
    net_profit: float


class MugBetConfigResponse(BaseModel):
    """Mug bet configuration."""

    mug_bet_max_edge_pct: float
    mug_bet_min_edge_pct: float
    mug_bet_min_implied_prob: float
    mug_bet_stake_pct: float
    mug_bet_warmup_count: int
    mug_bet_ongoing_ratio: int


class MugBetConfigUpdate(BaseModel):
    """Update mug bet configuration."""

    mug_bet_max_edge_pct: Optional[float] = Field(None, le=0)
    mug_bet_min_edge_pct: Optional[float] = Field(None, le=0)
    mug_bet_min_implied_prob: Optional[float] = Field(None, ge=0.5, le=0.95)
    mug_bet_stake_pct: Optional[float] = Field(None, ge=0.5, le=5.0)
    mug_bet_warmup_count: Optional[int] = Field(None, ge=1, le=20)
    mug_bet_ongoing_ratio: Optional[int] = Field(None, ge=1, le=20)


# ============ Endpoints ============


@router.get("/scan", response_model=MugBetScanResponse)
async def scan_mug_bets(
    provider_id: str = Query(..., description="Provider to scan for mug bets"),
    limit: int = Query(50, ge=1, le=100, description="Max opportunities to return"),
    db: Session = Depends(get_db),
):
    """
    Scan for mug bet opportunities at a provider.

    Returns negative-edge favorites sorted by "recreational score"
    (higher = better camouflage for value betting activity).
    """
    # Verify provider exists
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")

    # Get config
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()

    config = None
    if profile:
        config = db.query(RiskConfig).filter(RiskConfig.profile_id == profile.id).first()

    if not config:
        config = RiskConfig()

    # Scan
    scanner = MugBetScanner(db)
    opportunities = scanner.scan_mug_bets(
        provider_id=provider_id,
        max_edge_pct=config.mug_bet_max_edge_pct,
        min_edge_pct=config.mug_bet_min_edge_pct,
        min_implied_prob=config.mug_bet_min_implied_prob,
        limit=limit,
    )

    return MugBetScanResponse(
        provider_id=provider_id,
        opportunities=[
            MugBetOpportunityResponse(
                event_id=opp.event_id,
                market=opp.market,
                outcome=opp.outcome,
                provider=opp.provider,
                provider_odds=opp.provider_odds,
                fair_odds=opp.fair_odds,
                edge_pct=opp.edge_pct,
                implied_prob=opp.implied_prob,
                recreational_score=opp.recreational_score,
                home_team=opp.home_team,
                away_team=opp.away_team,
                sport=opp.sport,
                league=opp.league,
            )
            for opp in opportunities
        ],
        count=len(opportunities),
        config={
            "max_edge_pct": config.mug_bet_max_edge_pct,
            "min_edge_pct": config.mug_bet_min_edge_pct,
            "min_implied_prob": config.mug_bet_min_implied_prob,
        },
    )


@router.get("/status", response_model=MugBetStatusResponse)
async def get_mug_bet_status(
    db: Session = Depends(get_db),
):
    """
    Get account health stats for all providers.

    Mug bets are optional/manual - value betting losses (~45%) provide natural cover.
    Returns informational stats only (needs_mug_bets is always false).
    Use /auto-place with explicit count to place mug bets if desired.
    """
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(name="default", is_active=True)
            db.add(profile)
            db.commit()

    automator = MugBetAutomator(db, profile_id=profile.id)
    statuses = automator.get_all_provider_status()

    total_needing = sum(1 for s in statuses if s.needs_mug_bets)
    total_needed = sum(s.count_needed for s in statuses if s.needs_mug_bets)

    return MugBetStatusResponse(
        providers=[
            MugBetRequirementResponse(
                provider_id=s.provider_id,
                needs_mug_bets=s.needs_mug_bets,
                reason=s.reason,
                count_needed=s.count_needed,
                account_age_days=s.account_age_days,
                total_bets=s.total_bets,
                ev_bets=s.ev_bets,
                mug_bets=s.mug_bets,
                ev_quality_ratio=s.ev_quality_ratio,
                message=s.message,
            )
            for s in statuses
        ],
        total_needing_mug_bets=total_needing,
        total_mug_bets_needed=total_needed,
    )


@router.get("/status/{provider_id}", response_model=MugBetRequirementResponse)
async def get_provider_mug_bet_status(
    provider_id: str,
    db: Session = Depends(get_db),
):
    """Get mug bet status for a specific provider."""
    # Verify provider exists
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")

    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()

    automator = MugBetAutomator(db, profile_id=profile.id if profile else 1)
    status = automator.assess_provider(provider_id)

    return MugBetRequirementResponse(
        provider_id=status.provider_id,
        needs_mug_bets=status.needs_mug_bets,
        reason=status.reason,
        count_needed=status.count_needed,
        account_age_days=status.account_age_days,
        total_bets=status.total_bets,
        ev_bets=status.ev_bets,
        mug_bets=status.mug_bets,
        ev_quality_ratio=status.ev_quality_ratio,
        message=status.message,
    )


@router.post("/auto-place", response_model=AutoPlaceResponse)
async def auto_place_mug_bets(
    request: AutoPlaceRequest,
    db: Session = Depends(get_db),
):
    """
    Manually place mug bets for a provider.

    Mug bets are optional - value betting losses provide natural cover.
    Use this when you decide an account needs extra camouflage.

    Args:
        provider_id: Provider to place mug bets for (required for manual placement)
        count: Number of mug bets to place (required, no auto-detection)
        dry_run: Preview what would be placed without actually placing

    Use dry_run=true to preview what would be placed without actually placing.
    """
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(name="default", is_active=True)
            db.add(profile)
            db.commit()

    automator = MugBetAutomator(db, profile_id=profile.id)

    if request.provider_id:
        # Verify provider exists
        provider = db.query(Provider).filter(Provider.id == request.provider_id).first()
        if not provider:
            raise HTTPException(status_code=404, detail=f"Provider {request.provider_id} not found")

        placed = automator.auto_place(
            provider_id=request.provider_id,
            count=request.count,
            dry_run=request.dry_run,
        )
        results = {request.provider_id: placed} if placed else {}
    else:
        results_dict = automator.auto_place_all(dry_run=request.dry_run)
        results = results_dict

    # Convert to response format
    results_response = {}
    total_stake = 0.0

    for pid, bets in results.items():
        results_response[pid] = [
            PlacedMugBetResponse(
                provider_id=b.provider_id,
                event_id=b.event_id,
                outcome=b.outcome,
                odds=b.odds,
                stake=b.stake,
                edge_pct=b.edge_pct,
                reason=b.reason,
                home_team=b.home_team,
                away_team=b.away_team,
                sport=b.sport,
                bet_id=b.bet_id,
                placed=b.placed,
            )
            for b in bets
        ]
        total_stake += sum(b.stake for b in bets)

    total_placed = sum(len(bets) for bets in results.values())

    return AutoPlaceResponse(
        dry_run=request.dry_run,
        results=results_response,
        total_placed=total_placed,
        total_stake=round(total_stake, 2),
    )


@router.get("/history", response_model=MugBetHistoryResponse)
async def get_mug_bet_history(
    provider_id: Optional[str] = Query(None, description="Filter by provider"),
    limit: int = Query(50, ge=1, le=200, description="Max bets to return"),
    db: Session = Depends(get_db),
):
    """
    Get history of placed mug bets.

    Optionally filter by provider_id.
    """
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()

    automator = MugBetAutomator(db, profile_id=profile.id if profile else 1)
    bets = automator.get_mug_bet_history(provider_id=provider_id, limit=limit)

    total_staked = sum(b.stake for b in bets)
    total_payout = sum(b.payout for b in bets)

    return MugBetHistoryResponse(
        bets=[
            MugBetHistoryItem(
                id=b.id,
                provider_id=b.provider_id,
                event_id=b.event_id,
                market=b.market,
                outcome=b.outcome,
                odds=b.odds,
                stake=b.stake,
                result=b.result,
                payout=b.payout,
                mug_bet_reason=b.mug_bet_reason,
                ev_at_placement=b.ev_at_placement,
                placed_at=b.placed_at.isoformat() if b.placed_at else None,
                settled_at=b.settled_at.isoformat() if b.settled_at else None,
            )
            for b in bets
        ],
        count=len(bets),
        total_staked=round(total_staked, 2),
        total_payout=round(total_payout, 2),
        net_profit=round(total_payout - total_staked, 2),
    )


@router.get("/config", response_model=MugBetConfigResponse)
async def get_mug_bet_config(
    db: Session = Depends(get_db),
):
    """Get current mug bet configuration."""
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(name="default", is_active=True)
            db.add(profile)
            db.commit()

    # Get or create config
    config = db.query(RiskConfig).filter(RiskConfig.profile_id == profile.id).first()
    if not config:
        config = RiskConfig(profile_id=profile.id)
        db.add(config)
        db.commit()

    return MugBetConfigResponse(
        mug_bet_max_edge_pct=config.mug_bet_max_edge_pct,
        mug_bet_min_edge_pct=config.mug_bet_min_edge_pct,
        mug_bet_min_implied_prob=config.mug_bet_min_implied_prob,
        mug_bet_stake_pct=config.mug_bet_stake_pct,
        mug_bet_warmup_count=config.mug_bet_warmup_count,
        mug_bet_ongoing_ratio=config.mug_bet_ongoing_ratio,
    )


@router.put("/config", response_model=MugBetConfigResponse)
async def update_mug_bet_config(
    update: MugBetConfigUpdate,
    db: Session = Depends(get_db),
):
    """Update mug bet configuration."""
    # Get active profile
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No active profile")

    # Get config
    config = db.query(RiskConfig).filter(RiskConfig.profile_id == profile.id).first()
    if not config:
        config = RiskConfig(profile_id=profile.id)
        db.add(config)

    # Update fields
    update_data = update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)

    db.commit()

    return MugBetConfigResponse(
        mug_bet_max_edge_pct=config.mug_bet_max_edge_pct,
        mug_bet_min_edge_pct=config.mug_bet_min_edge_pct,
        mug_bet_min_implied_prob=config.mug_bet_min_implied_prob,
        mug_bet_stake_pct=config.mug_bet_stake_pct,
        mug_bet_warmup_count=config.mug_bet_warmup_count,
        mug_bet_ongoing_ratio=config.mug_bet_ongoing_ratio,
    )

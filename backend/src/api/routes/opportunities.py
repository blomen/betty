"""Opportunities API routes - value betting opportunities."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...services import OpportunityService
from ...services.play_service import PlaySessionService
from ...services.batch_builder import BatchBuilder
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


@router.get("/play/pending-bets")
async def get_pending_bets(db: Session = Depends(get_db)):
    """Get all pending (unsettled) bets grouped by provider."""
    from ...db.models import Bet, Event
    from ...repositories import ProfileRepo

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    pending = (
        db.query(Bet)
        .filter(Bet.profile_id == profile.id, Bet.result == "pending")
        .order_by(Bet.provider_id, Bet.placed_at)
        .all()
    )

    # Build event name lookup
    event_ids = {b.event_id for b in pending if b.event_id}
    events = {}
    if event_ids:
        for ev in db.query(Event).filter(Event.id.in_(event_ids)).all():
            events[ev.id] = f"{ev.home_team} vs {ev.away_team}" if ev.home_team and ev.away_team else ev.id

    # Group by provider
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for bet in pending:
        groups[bet.provider_id].append({
            "id": bet.id,
            "event_name": events.get(bet.event_id, bet.event_id or "Unknown"),
            "market": bet.market,
            "outcome": bet.outcome,
            "odds": bet.odds,
            "stake": bet.stake,
            "currency": bet.currency or "SEK",
            "placed_at": bet.placed_at.isoformat() if bet.placed_at else None,
        })

    providers = [
        {
            "provider_id": pid,
            "pending_count": len(bets),
            "total_stake": sum(b["stake"] for b in bets),
            "bets": bets,
        }
        for pid, bets in sorted(groups.items())
    ]

    return {
        "providers": providers,
        "total_pending": len(pending),
        "total_stake": sum(b.stake for b in pending),
    }


class SettleBetRequest(BaseModel):
    bet_id: int
    result: str  # "won", "lost", "void"


@router.post("/play/settle-bet")
async def settle_bet(body: SettleBetRequest, db: Session = Depends(get_db)):
    """Manually settle a single pending bet."""
    from ...db.models import Bet
    from ...services.bet_service import BetService

    if body.result not in ("won", "lost", "void"):
        raise HTTPException(400, f"Invalid result: {body.result}. Must be won, lost, or void.")

    bet = db.get(Bet, body.bet_id)
    if not bet:
        raise HTTPException(404, f"Bet {body.bet_id} not found")

    # Calculate payout
    if body.result == "won":
        payout = bet.stake * bet.odds
    elif body.result == "void":
        payout = bet.stake
    else:
        payout = 0.0

    # Route through BetService so wagering progress gets recorded
    bet_service = BetService(db)
    bet_service.settle_bet(body.bet_id, body.result, payout)

    return {
        "bet_id": bet.id,
        "result": bet.result,
        "payout": bet.payout,
        "settled_at": bet.settled_at.isoformat() if bet.settled_at else None,
    }


class BuildBatchRequest(BaseModel):
    exclude: list[str] | None = None


class CapitalActionRequest(BaseModel):
    type: str  # "deposit", "withdraw", "transfer"
    provider_id: Optional[str] = None
    from_provider_id: Optional[str] = None
    to_provider_id: Optional[str] = None
    amount: float


class ConfirmCapitalRequest(BaseModel):
    actions: list[CapitalActionRequest]


@router.post("/play/batch")
async def build_batch(
    body: BuildBatchRequest | None = None,
    db: Session = Depends(get_db),
):
    """Build optimal batch of all +EV bets with balance allocation."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    builder = BatchBuilder(db)
    exclude = body.exclude if body else None
    return builder.build(profile.id, exclude=exclude)


@router.post("/play/confirm-capital")
async def confirm_capital(
    body: ConfirmCapitalRequest,
    db: Session = Depends(get_db),
):
    """Apply capital actions (deposit/withdraw/transfer) and rebuild batch."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    for action in body.actions:
        if action.type == "deposit":
            if not action.provider_id:
                raise HTTPException(400, "deposit requires provider_id")
            profile_repo.adjust_balance(profile.id, action.provider_id, action.amount)

        elif action.type == "withdraw":
            if not action.provider_id:
                raise HTTPException(400, "withdraw requires provider_id")
            current = profile_repo.get_balance(profile.id, action.provider_id)
            if current < action.amount:
                raise HTTPException(422, f"Insufficient balance on {action.provider_id}: have {current}, need {action.amount}")
            profile_repo.adjust_balance(profile.id, action.provider_id, -action.amount)

        elif action.type == "transfer":
            if not action.from_provider_id or not action.to_provider_id:
                raise HTTPException(400, "transfer requires from_provider_id and to_provider_id")
            current = profile_repo.get_balance(profile.id, action.from_provider_id)
            if current < action.amount:
                raise HTTPException(422, f"Insufficient balance on {action.from_provider_id}: have {current}, need {action.amount}")
            profile_repo.adjust_balance(profile.id, action.from_provider_id, -action.amount)
            profile_repo.adjust_balance(profile.id, action.to_provider_id, action.amount)

        else:
            raise HTTPException(400, f"Unknown action type: {action.type}")

    db.commit()

    # Rebuild batch with updated balances
    builder = BatchBuilder(db)
    return builder.build(profile.id)

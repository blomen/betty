"""Bets API routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...services import BetService
from ...repositories import BetRepo, ProfileRepo
from ..deps import get_db
from ..schemas import BetCreate, BetUpdate

router = APIRouter(prefix="/api/bets", tags=["bets"])


def _get_service(db: Session = Depends(get_db)) -> BetService:
    return BetService(db)


@router.get("")
async def list_bets(
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get bet history for active profile."""
    profile_repo = ProfileRepo(db)
    bet_repo = BetRepo(db)
    profile = profile_repo.get_active()

    bets = bet_repo.list_for_profile(profile.id, status=status, limit=limit)

    return {
        "profile_id": profile.id,
        "bets": [
            {
                "id": b.id,
                "event_id": b.event_id,
                "provider": b.provider_id,
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "stake": b.stake,
                "is_bonus": b.is_bonus,
                "bonus_type": b.bonus_type,
                "result": b.result,
                "payout": b.payout,
                "profit": b.profit,
                "roi_pct": b.roi_pct,
                "placed_at": b.placed_at.isoformat() if b.placed_at else None,
            }
            for b in bets
        ],
        "count": len(bets),
    }


@router.post("")
async def create_bet(bet: BetCreate, service: BetService = Depends(_get_service)):
    """Record a placed bet for active profile."""
    result = service.create_bet(
        event_id=bet.event_id,
        provider_id=bet.provider_id,
        market=bet.market,
        outcome=bet.outcome,
        odds=bet.odds,
        stake=bet.stake,
        is_bonus=bet.is_bonus,
        bonus_type=bet.bonus_type,
    )

    if "error" in result:
        status_code = 404 if "not found" in result["error"] else 400
        raise HTTPException(status_code, result["error"])

    return result


@router.put("/{bet_id}")
async def settle_bet(bet_id: int, data: BetUpdate, service: BetService = Depends(_get_service)):
    """Settle a bet with result."""
    result = service.settle_bet(bet_id, data.result, data.payout)

    if "error" in result:
        raise HTTPException(404, result["error"])

    return result

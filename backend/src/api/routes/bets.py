"""Bets API routes."""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Provider, Bet
from ..deps import get_db
from ..schemas import BetCreate, BetUpdate

router = APIRouter(prefix="/api/bets", tags=["bets"])


@router.get("")
async def list_bets(
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get bet history."""
    query = db.query(Bet)
    if status:
        query = query.filter(Bet.result == status)

    bets = query.order_by(Bet.placed_at.desc()).limit(limit).all()

    return {
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
async def create_bet(bet: BetCreate, db: Session = Depends(get_db)):
    """Record a placed bet (manual entry)."""
    # Verify provider exists
    provider = db.query(Provider).filter(Provider.id == bet.provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {bet.provider_id} not found")

    # Validate sufficient balance (unless free bet)
    if not bet.is_bonus:
        if provider.balance < bet.stake:
            raise HTTPException(
                400,
                f"Insufficient balance: {provider.balance:.2f} available, {bet.stake:.2f} required"
            )

    b = Bet(
        event_id=bet.event_id,
        provider_id=bet.provider_id,
        market=bet.market,
        outcome=bet.outcome,
        odds=bet.odds,
        stake=bet.stake,
        is_bonus=bet.is_bonus,
        bonus_type=bet.bonus_type,
    )
    db.add(b)

    # Deduct stake from provider balance (unless free bet)
    if not bet.is_bonus:
        provider.balance -= bet.stake

    db.commit()
    return {"success": True, "bet_id": b.id}


@router.put("/{bet_id}")
async def settle_bet(bet_id: int, data: BetUpdate, db: Session = Depends(get_db)):
    """Settle a bet with result."""
    bet = db.query(Bet).filter(Bet.id == bet_id).first()
    if not bet:
        raise HTTPException(404, f"Bet {bet_id} not found")

    bet.result = data.result
    bet.payout = data.payout
    bet.settled_at = datetime.utcnow()

    # Add payout to provider balance
    provider = db.query(Provider).filter(Provider.id == bet.provider_id).first()
    if provider and data.payout > 0:
        provider.balance += data.payout

    db.commit()
    return {"success": True, "profit": bet.profit}

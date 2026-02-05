"""Bets API routes."""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import (
    Provider, Bet,
    get_active_profile, get_profile_balance, adjust_profile_balance
)
from ..deps import get_db
from ..schemas import BetCreate, BetUpdate

router = APIRouter(prefix="/api/bets", tags=["bets"])


@router.get("")
async def list_bets(
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get bet history for active profile."""
    profile = get_active_profile(db)

    query = db.query(Bet).filter(Bet.profile_id == profile.id)
    if status:
        query = query.filter(Bet.result == status)

    bets = query.order_by(Bet.placed_at.desc()).limit(limit).all()

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
async def create_bet(bet: BetCreate, db: Session = Depends(get_db)):
    """Record a placed bet for active profile."""
    profile = get_active_profile(db)

    # Verify provider exists
    provider = db.query(Provider).filter(Provider.id == bet.provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {bet.provider_id} not found")

    # Validate sufficient balance (unless free bet)
    current_balance = get_profile_balance(db, profile.id, bet.provider_id)
    if not bet.is_bonus:
        if current_balance < bet.stake:
            raise HTTPException(
                400,
                f"Insufficient balance: {current_balance:.2f} available, {bet.stake:.2f} required"
            )

    b = Bet(
        profile_id=profile.id,
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

    # Deduct stake from profile's provider balance (unless free bet)
    if not bet.is_bonus:
        adjust_profile_balance(db, profile.id, bet.provider_id, -bet.stake)

    db.commit()
    return {"success": True, "bet_id": b.id, "profile_id": profile.id}


@router.put("/{bet_id}")
async def settle_bet(bet_id: int, data: BetUpdate, db: Session = Depends(get_db)):
    """Settle a bet with result."""
    bet = db.query(Bet).filter(Bet.id == bet_id).first()
    if not bet:
        raise HTTPException(404, f"Bet {bet_id} not found")

    bet.result = data.result
    bet.payout = data.payout
    bet.settled_at = datetime.utcnow()

    # Add payout to profile's provider balance
    if bet.profile_id and data.payout > 0:
        adjust_profile_balance(db, bet.profile_id, bet.provider_id, data.payout)

    db.commit()
    return {"success": True, "profit": bet.profit, "profile_id": bet.profile_id}

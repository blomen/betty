"""
OddOpp FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, WebSocket, WebSocketDisconnect

# Load .env from backend directory
load_dotenv(Path(__file__).parent.parent / ".env")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .db.models import (
    init_db, get_session, 
    Event, Odds, Provider, Bet, Profile, Opportunity
)
from .analysis import find_arbitrage, find_best_value, find_best_hedge
from .bankroll import BankrollManager, kelly_stake

app = FastAPI(
    title="OddOpp API",
    description="Polymarket arbitrage & value betting backend",
    version="0.1.0",
)

# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Extraction state
extraction_state = {"running": False, "last_run": None, "events": 0, "odds": 0}

# Global pipeline instance for accessing metrics/circuit breaker/cache
_pipeline_instance = None

def get_pipeline():
    """Get or create pipeline singleton."""
    global _pipeline_instance
    if _pipeline_instance is None:
        from .pipeline import ExtractionPipeline
        _pipeline_instance = ExtractionPipeline()
    return _pipeline_instance


# WebSocket connection manager for real-time progress
class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept and store new connection."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove disconnected client."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.disconnect(conn)

ws_manager = ConnectionManager()

# Initialize database on startup
@app.on_event("startup")
async def startup():
    init_db()


# ============ Pydantic Schemas ============

class ProviderCreate(BaseModel):
    id: str
    name: str
    url: Optional[str] = None
    balance: float = 0.0

class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    is_enabled: Optional[bool] = None
    balance: Optional[float] = None

class BulkBalanceUpdate(BaseModel):
    balance: float
    provider_ids: Optional[list[str]] = None  # If None, updates all enabled providers

class BalanceAdjustment(BaseModel):
    amount: float  # Can be positive (add) or negative (subtract)

class BetCreate(BaseModel):
    event_id: Optional[str] = None
    provider_id: str
    market: Optional[str] = None
    outcome: Optional[str] = None
    odds: float
    stake: float
    is_bonus: bool = False
    bonus_type: Optional[str] = None

class BetUpdate(BaseModel):
    result: str  # "won", "lost", "void"
    payout: float = 0.0

class ProfileCreate(BaseModel):
    name: str
    bankroll: Optional[float] = 1000.0
    currency: Optional[str] = "USD"
    kelly_fraction: Optional[float] = 0.25
    min_edge_pct: Optional[float] = 2.0
    min_arb_pct: Optional[float] = 0.5
    max_stake_pct: Optional[float] = 5.0
    min_retention_pct: Optional[float] = 80.0
    preferred_counterparts: Optional[list[str]] = None
    bonus_enabled: Optional[bool] = True

class ProfileUpdate(BaseModel):
    kelly_fraction: Optional[float] = None
    min_edge_pct: Optional[float] = None
    min_arb_pct: Optional[float] = None
    max_stake_pct: Optional[float] = None
    min_retention_pct: Optional[float] = None
    preferred_counterparts: Optional[list[str]] = None
    bonus_enabled: Optional[bool] = None

class BonusMatchRequest(BaseModel):
    event_id: str
    market: str
    anchor_provider: str
    anchor_outcome: str
    anchor_odds: float
    anchor_stake: float
    is_free_bet: bool = False
    counterpart_providers: Optional[list[str]] = None


# ============ Dependency ============

def get_db():
    db = get_session()
    try:
        yield db
    finally:
        db.close()


# ============ Health ============

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# ============ Providers ============

@app.get("/api/providers")
async def list_providers(db: Session = Depends(get_db)):
    """Get all providers with status and balance."""
    providers = db.query(Provider).all()
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "url": p.url,
                "is_enabled": p.is_enabled,
                "balance": p.balance,
            }
            for p in providers
        ],
        "total_balance": sum(p.balance for p in providers if p.is_enabled),
    }


@app.post("/api/providers")
async def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    """Create a new provider."""
    existing = db.query(Provider).filter(Provider.id == provider.id).first()
    if existing:
        raise HTTPException(400, f"Provider {provider.id} already exists")
    
    p = Provider(
        id=provider.id,
        name=provider.name,
        url=provider.url,
        balance=provider.balance,
    )
    db.add(p)
    db.commit()
    return {"success": True, "provider_id": p.id}


@app.put("/api/providers/{provider_id}")
async def update_provider(
    provider_id: str,
    data: ProviderUpdate,
    db: Session = Depends(get_db)
):
    """Update provider (balance, enabled, etc.)."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = provider.balance

    if data.name is not None:
        provider.name = data.name
    if data.url is not None:
        provider.url = data.url
    if data.is_enabled is not None:
        provider.is_enabled = data.is_enabled
    if data.balance is not None:
        provider.balance = data.balance

    provider.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "new_balance": provider.balance,
    }


# ============ Bankroll ============

@app.get("/api/bankroll")
async def get_bankroll(db: Session = Depends(get_db)):
    """Get provider balances and total bankroll."""
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()
    
    return {
        "total": sum(p.balance for p in providers),
        "providers": [
            {"id": p.id, "name": p.name, "balance": p.balance}
            for p in providers
        ],
    }


@app.get("/api/bankroll/stats")
async def get_bankroll_stats(db: Session = Depends(get_db)):
    """Get bankroll statistics including bet history."""
    # Get all settled bets
    bets = db.query(Bet).filter(Bet.result != "pending").all()

    total_staked = sum(b.stake for b in bets)
    total_profit = sum(b.profit for b in bets)
    win_count = len([b for b in bets if b.result == "won"])
    loss_count = len([b for b in bets if b.result == "lost"])
    void_count = len([b for b in bets if b.result == "void"])

    return {
        "total_bets": len(bets),
        "wins": win_count,
        "losses": loss_count,
        "voids": void_count,
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(total_profit / total_staked * 100, 2) if total_staked > 0 else 0,
        "win_rate": round(win_count / len(bets) * 100, 2) if len(bets) > 0 else 0,
    }


@app.post("/api/bankroll/set-all")
async def set_all_balances(data: BulkBalanceUpdate, db: Session = Depends(get_db)):
    """Set balance for multiple providers at once."""
    if data.provider_ids:
        providers = db.query(Provider).filter(Provider.id.in_(data.provider_ids)).all()
    else:
        providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    if not providers:
        raise HTTPException(404, "No providers found")

    updated_count = 0
    for provider in providers:
        provider.balance = data.balance
        provider.updated_at = datetime.utcnow()
        updated_count += 1

    db.commit()

    total_balance = sum(p.balance for p in providers)

    return {
        "success": True,
        "updated_count": updated_count,
        "balance_per_provider": data.balance,
        "total_balance": total_balance,
    }


@app.post("/api/bankroll/adjust/{provider_id}")
async def adjust_balance(
    provider_id: str,
    data: BalanceAdjustment,
    db: Session = Depends(get_db)
):
    """Add or subtract from provider balance."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = provider.balance
    provider.balance += data.amount
    provider.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "adjustment": data.amount,
        "new_balance": provider.balance,
    }


@app.post("/api/bankroll/reset-all")
async def reset_all_balances(db: Session = Depends(get_db)):
    """Reset all provider balances to 0."""
    providers = db.query(Provider).all()

    for provider in providers:
        provider.balance = 0.0
        provider.updated_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "reset_count": len(providers),
        "message": "All balances reset to 0",
    }


@app.get("/api/bankroll/exposure")
async def get_bankroll_exposure(db: Session = Depends(get_db)):
    """Get bankroll with exposure breakdown per provider."""
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    exposure_data = []
    for provider in providers:
        # Calculate pending bets for this provider
        pending_bets = db.query(Bet).filter(
            Bet.provider_id == provider.id,
            Bet.result == "pending"
        ).all()

        pending_exposure = sum(b.stake for b in pending_bets if not b.is_bonus)
        pending_count = len(pending_bets)

        exposure_data.append({
            "provider_id": provider.id,
            "provider_name": provider.name,
            "total_balance": provider.balance,
            "pending_exposure": pending_exposure,
            "pending_bets_count": pending_count,
            "available": provider.balance,  # Already deducted when bet placed
        })

    total_balance = sum(p.balance for p in providers)
    total_pending = sum(e["pending_exposure"] for e in exposure_data)

    return {
        "total_balance": total_balance,
        "total_pending": total_pending,
        "total_available": total_balance,
        "providers": exposure_data,
    }


# ============ Events ============

@app.get("/api/events")
async def list_events(
    sport: Optional[str] = None, 
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get extracted events with odds."""
    query = db.query(Event)
    if sport:
        query = query.filter(Event.sport == sport)
    
    events = query.order_by(Event.start_time).limit(limit).all()
    
    return {
        "events": [
            {
                "id": e.id,
                "sport": e.sport,
                "league": e.league,
                "home_team": e.home_team,
                "away_team": e.away_team,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "odds_count": len(e.odds),
            }
            for e in events
        ],
        "count": len(events),
    }


@app.get("/api/events/{event_id}")
async def get_event(event_id: str, db: Session = Depends(get_db)):
    """Get event details with all odds."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, f"Event {event_id} not found")
    
    # Group odds by market
    odds_by_market = {}
    for o in event.odds:
        if o.market not in odds_by_market:
            odds_by_market[o.market] = []
        odds_by_market[o.market].append({
            "provider": o.provider_id,
            "outcome": o.outcome,
            "odds": o.odds,
        })
    
    return {
        "id": event.id,
        "sport": event.sport,
        "league": event.league,
        "home_team": event.home_team,
        "away_team": event.away_team,
        "start_time": event.start_time.isoformat() if event.start_time else None,
        "odds": odds_by_market,
    }


# ============ Opportunities ============

@app.get("/api/opportunities")
async def list_opportunities(
    type: Optional[str] = None,
    active_only: bool = True,
    provider1: Optional[str] = None,
    provider2: Optional[str] = None,
    providers: Optional[str] = None,
    market: Optional[str] = None,
    sport: Optional[str] = None,
    min_value: Optional[float] = None,
    db: Session = Depends(get_db)
):
    """Get current arb/value/bonus opportunities with enhanced filtering."""
    query = db.query(Opportunity)

    if type:
        query = query.filter(Opportunity.type == type)
    if active_only:
        query = query.filter(Opportunity.is_active == True)
    if provider1:
        query = query.filter(Opportunity.provider1_id == provider1)
    if provider2:
        query = query.filter(Opportunity.provider2_id == provider2)
    if providers:
        provider_list = [p.strip() for p in providers.split(',')]
        query = query.filter(
            (Opportunity.provider1_id.in_(provider_list)) |
            (Opportunity.provider2_id.in_(provider_list))
        )
    if market:
        query = query.filter(Opportunity.market == market)
    if sport:
        # Join with Event table to filter by sport
        query = query.join(Event, Event.id == Opportunity.event_id).filter(Event.sport == sport)
    if min_value is not None:
        # Filter by profit_pct for arb or edge_pct for value
        query = query.filter(
            (Opportunity.profit_pct >= min_value) |
            (Opportunity.edge_pct >= min_value)
        )

    opps = query.order_by(Opportunity.detected_at.desc()).limit(50).all()

    return {
        "opportunities": [
            {
                "id": o.id,
                "type": o.type,
                "event_id": o.event_id,
                "market": o.market,
                "provider1": o.provider1_id,
                "provider2": o.provider2_id,
                "odds1": o.odds1,
                "odds2": o.odds2,
                "outcome1": o.outcome1,
                "outcome2": o.outcome2,
                "profit_pct": o.profit_pct,
                "edge_pct": o.edge_pct,
                "detected_at": o.detected_at.isoformat() if o.detected_at else None,
            }
            for o in opps
        ],
        "count": len(opps),
    }


@app.post("/api/opportunities/bonus/match")
async def match_bonus_bet(
    data: BonusMatchRequest,
    db: Session = Depends(get_db)
):
    """Find the best hedge for a bonus bet."""
    # Query all odds for the event/market
    query = db.query(Odds).filter(
        Odds.event_id == data.event_id,
        Odds.market == data.market,
        Odds.outcome != data.anchor_outcome,
        Odds.provider_id != data.anchor_provider
    )

    # Filter by counterpart providers if specified
    if data.counterpart_providers:
        query = query.filter(Odds.provider_id.in_(data.counterpart_providers))

    opposing_odds = query.all()

    if not opposing_odds:
        raise HTTPException(
            404,
            "No opposing odds found for the specified event/market/outcome combination"
        )

    # Format for find_best_hedge
    opposing_list = [
        {
            "provider": o.provider_id,
            "outcome": o.outcome,
            "odds": o.odds
        }
        for o in opposing_odds
    ]

    # Find best hedge
    result = find_best_hedge(
        event_id=data.event_id,
        market=data.market,
        anchor_provider=data.anchor_provider,
        anchor_outcome=data.anchor_outcome,
        anchor_odds=data.anchor_odds,
        anchor_stake=data.anchor_stake,
        opposing_odds_list=opposing_list,
        is_free_bet=data.is_free_bet
    )

    if not result:
        raise HTTPException(
            404,
            "No suitable hedge found (all hedges are same-provider or no valid options)"
        )

    return {
        "event_id": result.event_id,
        "market": result.market,
        "anchor_provider": result.anchor_provider,
        "anchor_outcome": result.anchor_outcome,
        "anchor_odds": result.anchor_odds,
        "anchor_stake": result.anchor_stake,
        "hedge_provider": result.hedge_provider,
        "hedge_outcome": result.hedge_outcome,
        "hedge_odds": result.hedge_odds,
        "hedge_stake": result.hedge_stake,
        "qualifying_loss": result.qualifying_loss,
        "retention_pct": result.retention_pct,
    }


# ============ Bets ============

@app.get("/api/bets")
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


@app.post("/api/bets")
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


@app.put("/api/bets/{bet_id}")
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


# ============ Profiles ============

def profile_to_dict(profile: Profile) -> dict:
    """Convert profile to dict response."""
    import json

    # Parse preferred_counterparts JSON if exists
    preferred_counterparts = []
    if profile.preferred_counterparts:
        try:
            preferred_counterparts = json.loads(profile.preferred_counterparts)
        except:
            pass

    return {
        "id": profile.id,
        "name": profile.name,
        "bankroll": profile.bankroll,
        "currency": profile.currency,
        "kelly_fraction": profile.kelly_fraction,
        "min_edge_pct": profile.min_edge_pct,
        "min_arb_pct": profile.min_arb_pct,
        "max_stake_pct": profile.max_stake_pct,
        "min_retention_pct": profile.min_retention_pct,
        "preferred_counterparts": preferred_counterparts,
        "bonus_enabled": profile.bonus_enabled,
        "is_active": profile.is_active,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


@app.get("/api/profiles")
async def list_profiles(db: Session = Depends(get_db)):
    """List all profiles."""
    profiles = db.query(Profile).order_by(Profile.created_at).all()

    # Ensure at least one default profile exists
    if not profiles:
        default = Profile(name="default", is_active=True)
        db.add(default)
        db.commit()
        profiles = [default]

    return {
        "profiles": [profile_to_dict(p) for p in profiles],
        "active": next((profile_to_dict(p) for p in profiles if p.is_active), None),
    }


@app.get("/api/profiles/active")
async def get_active_profile(db: Session = Depends(get_db)):
    """Get currently active profile."""
    profile = db.query(Profile).filter(Profile.is_active == True).first()

    if not profile:
        # Create and activate default profile
        profile = Profile(name="default", is_active=True)
        db.add(profile)
        db.commit()

    return profile_to_dict(profile)


@app.post("/api/profiles")
async def create_profile(data: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new profile."""
    # Check name uniqueness
    existing = db.query(Profile).filter(Profile.name == data.name).first()
    if existing:
        raise HTTPException(400, f"Profile '{data.name}' already exists")

    profile = Profile(
        name=data.name,
        bankroll=data.bankroll,
        currency=data.currency,
        kelly_fraction=data.kelly_fraction,
        min_edge_pct=data.min_edge_pct,
        min_arb_pct=data.min_arb_pct,
        max_stake_pct=data.max_stake_pct,
        is_active=False,
    )
    db.add(profile)
    db.commit()

    return {"success": True, "profile": profile_to_dict(profile)}


@app.get("/api/profiles/{profile_id}")
async def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get profile by ID."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    return profile_to_dict(profile)


@app.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: int, data: ProfileUpdate, db: Session = Depends(get_db)):
    """Update profile settings."""
    import json

    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    if data.name is not None:
        # Check name uniqueness
        existing = db.query(Profile).filter(Profile.name == data.name, Profile.id != profile_id).first()
        if existing:
            raise HTTPException(400, f"Profile name '{data.name}' already exists")
        profile.name = data.name
    if data.bankroll is not None:
        profile.bankroll = data.bankroll
    if data.currency is not None:
        profile.currency = data.currency
    if data.kelly_fraction is not None:
        profile.kelly_fraction = data.kelly_fraction
    if data.min_edge_pct is not None:
        profile.min_edge_pct = data.min_edge_pct
    if data.min_arb_pct is not None:
        profile.min_arb_pct = data.min_arb_pct
    if data.max_stake_pct is not None:
        profile.max_stake_pct = data.max_stake_pct
    if data.min_retention_pct is not None:
        profile.min_retention_pct = data.min_retention_pct
    if data.preferred_counterparts is not None:
        profile.preferred_counterparts = json.dumps(data.preferred_counterparts)
    if data.bonus_enabled is not None:
        profile.bonus_enabled = data.bonus_enabled

    db.commit()
    return {"success": True, "profile": profile_to_dict(profile)}


@app.post("/api/profiles/{profile_id}/activate")
async def activate_profile(profile_id: int, db: Session = Depends(get_db)):
    """Set profile as active (deactivates others)."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    # Deactivate all profiles
    db.query(Profile).update({Profile.is_active: False})

    # Activate selected
    profile.is_active = True
    db.commit()

    return {"success": True, "profile": profile_to_dict(profile)}


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a profile."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    if profile.is_active:
        raise HTTPException(400, "Cannot delete active profile. Activate another profile first.")

    db.delete(profile)
    db.commit()

    return {"success": True}


# ============ Stake Calculator ============

@app.post("/api/calculate/stake")
async def calculate_stake(
    odds: float,
    fair_odds: float,
    db: Session = Depends(get_db)
):
    """Calculate recommended stake using Kelly criterion."""
    # Get profile and bankroll
    profile = db.query(Profile).filter(Profile.name == "default").first()
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()
    
    bankroll = sum(p.balance for p in providers)
    kelly_frac = profile.kelly_fraction if profile else 0.25
    max_stake_pct = profile.max_stake_pct if profile else 5.0
    
    win_prob = 1 / fair_odds
    rec = kelly_stake(
        odds=odds,
        win_probability=win_prob,
        bankroll=bankroll,
        kelly_fraction=kelly_frac,
        max_stake_pct=max_stake_pct,
    )
    
    return {
        "recommended_stake": rec.stake,
        "kelly_stake": rec.kelly_stake,
        "max_stake": rec.max_stake,
        "bankroll": bankroll,
        "reason": rec.reason,
    }


# ============ Extraction ============

async def run_extraction_task(providers: list[str], sport: str, max_groups: int):
    """Background task to run extraction."""
    global extraction_state

    from .pipeline.orchestrator import ExtractionPipeline

    extraction_state["running"] = True
    extraction_state["events"] = 0
    extraction_state["odds"] = 0

    try:
        # Run extraction pipeline
        pipeline = ExtractionPipeline()
        results = await pipeline.run(
            polymarket=True,
            providers=providers if providers else None
        )

        # Update extraction state from results
        if results:
            extraction_state["events"] = results.get("total_events", 0)
            extraction_state["odds"] = results.get("total_odds", 0)

        extraction_state["last_run"] = datetime.utcnow().isoformat()

    except Exception as e:
        print(f"Extraction failed: {e}")
        import traceback
        traceback.print_exc()

    finally:
        extraction_state["running"] = False


@app.get("/api/extraction/status")
async def get_extraction_status():
    """Get extraction status."""
    return extraction_state


@app.post("/api/extraction/run")
async def run_extraction(
    background_tasks: BackgroundTasks,
    providers: str = "unibet,leovegas,casumo",
    sport: str = "football",
    max_groups: int = 5,
):
    """Trigger extraction from Kambi providers."""
    if extraction_state["running"]:
        raise HTTPException(400, "Extraction already running")

    provider_list = [p.strip() for p in providers.split(",")]
    background_tasks.add_task(run_extraction_task, provider_list, sport, max_groups)

    return {"status": "started", "providers": provider_list, "sport": sport}


# ============ Metrics ============

@app.get("/api/metrics/history")
async def get_metrics_history(limit: int = 10):
    """Get historical metrics from pipeline runs."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        return {"error": "Metrics not enabled", "history": []}

    history = pipeline.metrics.get_history(limit=limit)

    return {
        "history": [run.to_dict() for run in history],
        "count": len(history)
    }


@app.get("/api/metrics/provider/{provider_id}")
async def get_provider_metrics(provider_id: str, limit: int = 10):
    """Get aggregate metrics for a specific provider."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        return {"error": "Metrics not enabled"}

    agg = pipeline.metrics.get_provider_aggregate(provider_id, limit=limit)

    return agg


@app.get("/api/metrics/current")
async def get_current_metrics():
    """Get metrics for current/latest run."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        return {"error": "Metrics not enabled"}

    current = pipeline.metrics.get_current_run()
    if current:
        return current.to_dict()

    # Get latest from history
    history = pipeline.metrics.get_history(limit=1)
    if history:
        return history[0].to_dict()

    return {"error": "No metrics available"}


# ============ Circuit Breaker ============

@app.get("/api/circuit-breaker/status")
async def get_circuit_breaker_status():
    """Get circuit breaker status for all providers."""
    pipeline = get_pipeline()

    if not pipeline.circuit_breaker:
        return {"error": "Circuit breaker not enabled", "statuses": {}}

    statuses = pipeline.circuit_breaker.get_all_statuses()

    return {
        "statuses": {
            pid: {
                "state": status.state.value,
                "failure_count": status.failure_count,
                "success_count": status.success_count,
                "last_failure_time": status.last_failure_time,
                "last_success_time": status.last_success_time,
                "opened_at": status.opened_at,
            }
            for pid, status in statuses.items()
        }
    }


@app.get("/api/circuit-breaker/status/{provider_id}")
async def get_provider_circuit_breaker_status(provider_id: str):
    """Get circuit breaker status for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.circuit_breaker:
        return {"error": "Circuit breaker not enabled"}

    status = pipeline.circuit_breaker.get_status(provider_id)

    return {
        "provider_id": provider_id,
        "state": status.state.value,
        "failure_count": status.failure_count,
        "success_count": status.success_count,
        "last_failure_time": status.last_failure_time,
        "last_success_time": status.last_success_time,
        "opened_at": status.opened_at,
    }


@app.post("/api/circuit-breaker/reset/{provider_id}")
async def reset_circuit_breaker(provider_id: str):
    """Manually reset circuit breaker for provider."""
    pipeline = get_pipeline()

    if not pipeline.circuit_breaker:
        raise HTTPException(400, "Circuit breaker not enabled")

    pipeline.circuit_breaker.reset(provider_id)

    return {
        "success": True,
        "provider_id": provider_id,
        "message": "Circuit breaker reset to CLOSED"
    }


# ============ Cache ============

@app.get("/api/cache/stats")
async def get_cache_stats():
    """Get cache statistics."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        return {"error": "Cache not enabled"}

    stats = pipeline.cache.get_stats()

    return stats


@app.get("/api/cache/stats/{provider_id}")
async def get_provider_cache_stats(provider_id: str):
    """Get cache statistics for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        return {"error": "Cache not enabled"}

    stats = pipeline.cache.get_provider_stats(provider_id)

    return {
        "provider_id": provider_id,
        **stats
    }


@app.post("/api/cache/clear")
async def clear_cache(provider_id: Optional[str] = None):
    """Clear cache (all or specific provider)."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        raise HTTPException(400, "Cache not enabled")

    pipeline.cache.clear(provider_id=provider_id)

    return {
        "success": True,
        "message": f"Cache cleared{' for ' + provider_id if provider_id else ' (all providers)'}"
    }


@app.post("/api/cache/evict-expired")
async def evict_expired_cache():
    """Manually evict expired cache entries."""
    pipeline = get_pipeline()

    if not pipeline.cache:
        raise HTTPException(400, "Cache not enabled")

    pipeline.cache.evict_expired()

    return {
        "success": True,
        "message": "Expired cache entries evicted"
    }


# ============ Health Checks ============

@app.get("/api/health-check/status")
async def get_health_check_status():
    """Get cached health check status for all providers."""
    pipeline = get_pipeline()

    if not pipeline.health_checker:
        return {"error": "Health checker not enabled", "statuses": {}}

    statuses = pipeline.health_checker.get_all_statuses()

    return {
        "statuses": {
            pid: {
                "healthy": status.healthy,
                "response_time_ms": status.response_time_ms,
                "error": status.error,
                "checked_at": status.checked_at,
            }
            for pid, status in statuses.items()
        }
    }


@app.post("/api/health-check/run/{provider_id}")
async def run_health_check(provider_id: str, force: bool = False):
    """Run health check for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.health_checker:
        raise HTTPException(400, "Health checker not enabled")

    # Get extractor
    extractor = pipeline.engine.get_extractor(provider_id)
    if not extractor:
        raise HTTPException(404, f"Provider {provider_id} not found")

    # Run check
    status = await pipeline.health_checker.check_provider(
        provider_id, extractor, force=force
    )

    return {
        "provider_id": provider_id,
        "healthy": status.healthy,
        "response_time_ms": status.response_time_ms,
        "error": status.error,
        "checked_at": status.checked_at,
    }


@app.post("/api/health-check/clear-cache")
async def clear_health_check_cache(provider_id: Optional[str] = None):
    """Clear health check cache."""
    pipeline = get_pipeline()

    if not pipeline.health_checker:
        raise HTTPException(400, "Health checker not enabled")

    pipeline.health_checker.clear_cache(provider_id=provider_id)

    return {
        "success": True,
        "message": f"Health check cache cleared{' for ' + provider_id if provider_id else ' (all)'}"
    }


# ============ Provider Monitoring ============

@app.get("/api/monitor/providers")
async def monitor_all_providers(limit: int = 20):
    """Get health assessment for all providers."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    # Get metrics history
    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        return {"error": "No metrics history available", "providers": {}}

    # Get circuit breaker and health check statuses
    cb_statuses = {}
    if pipeline.circuit_breaker:
        statuses = pipeline.circuit_breaker.get_all_statuses()
        cb_statuses = {
            pid: {
                "state": status.state.value,
                "failure_count": status.failure_count,
                "success_count": status.success_count,
            }
            for pid, status in statuses.items()
        }

    hc_statuses = {}
    if pipeline.health_checker:
        statuses = pipeline.health_checker.get_all_statuses()
        hc_statuses = {
            pid: {
                "healthy": status.healthy,
                "error": status.error,
            }
            for pid, status in statuses.items()
        }

    # Assess providers
    from .pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()
    assessments = monitor.assess_all_providers(history, cb_statuses, hc_statuses)

    return {
        "providers": {
            pid: {
                "health_score": health.health_score.value,
                "score_value": health.score_value,
                "is_healthy": health.is_healthy,
                "has_critical_issues": health.has_critical_issues,
                "avg_events_per_run": health.avg_events_per_run,
                "avg_response_time_ms": health.avg_response_time_ms,
                "success_rate": health.success_rate,
                "trend_direction": health.trend_direction,
                "issues": [
                    {
                        "type": issue.issue_type.value,
                        "severity": issue.severity,
                        "message": issue.message,
                        "metric_value": issue.metric_value,
                    }
                    for issue in health.issues
                ],
            }
            for pid, health in assessments.items()
        },
        "summary": {
            "total_providers": len(assessments),
            "healthy": sum(1 for h in assessments.values() if h.is_healthy),
            "unhealthy": sum(1 for h in assessments.values() if not h.is_healthy),
            "critical": sum(1 for h in assessments.values() if h.has_critical_issues),
        }
    }


@app.get("/api/monitor/providers/{provider_id}")
async def monitor_provider(provider_id: str, limit: int = 20):
    """Get detailed health assessment for specific provider."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        raise HTTPException(404, "No metrics history available")

    # Get statuses
    cb_status = None
    if pipeline.circuit_breaker:
        status = pipeline.circuit_breaker.get_status(provider_id)
        cb_status = {
            "state": status.state.value,
            "failure_count": status.failure_count,
            "success_count": status.success_count,
        }

    hc_status = None
    if pipeline.health_checker:
        status = pipeline.health_checker.get_cached_status(provider_id)
        if status:
            hc_status = {
                "healthy": status.healthy,
                "error": status.error,
                "response_time_ms": status.response_time_ms,
            }

    # Assess provider
    from .pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()
    health = monitor.assess_provider(provider_id, history, cb_status, hc_status)

    return {
        "provider_id": provider_id,
        "health_score": health.health_score.value,
        "score_value": health.score_value,
        "is_healthy": health.is_healthy,
        "has_critical_issues": health.has_critical_issues,
        "metrics": {
            "avg_events_per_run": health.avg_events_per_run,
            "avg_response_time_ms": health.avg_response_time_ms,
            "success_rate": health.success_rate,
            "uptime_pct": health.uptime_pct,
            "avg_odds_per_event": health.avg_odds_per_event,
        },
        "trend": {
            "direction": health.trend_direction,
            "is_degrading": health.is_degrading,
        },
        "issues": [
            {
                "type": issue.issue_type.value,
                "severity": issue.severity,
                "message": issue.message,
                "metric_value": issue.metric_value,
                "threshold_value": issue.threshold_value,
                "detected_at": issue.detected_at,
            }
            for issue in health.issues
        ],
        "assessed_at": health.assessed_at,
    }


@app.get("/api/monitor/unhealthy")
async def get_unhealthy_providers(limit: int = 20):
    """Get list of unhealthy providers."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        return {"unhealthy_providers": [], "count": 0}

    # Assess all providers
    from .pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()

    cb_statuses = {}
    if pipeline.circuit_breaker:
        statuses = pipeline.circuit_breaker.get_all_statuses()
        cb_statuses = {
            pid: {"state": s.state.value, "failure_count": s.failure_count}
            for pid, s in statuses.items()
        }

    assessments = monitor.assess_all_providers(history, cb_statuses)
    unhealthy = monitor.get_unhealthy_providers(assessments)

    return {
        "unhealthy_providers": [
            {
                "provider_id": pid,
                "health_score": assessments[pid].health_score.value,
                "score_value": assessments[pid].score_value,
                "issue_count": len(assessments[pid].issues),
                "critical_issues": sum(1 for i in assessments[pid].issues if i.severity == "critical"),
            }
            for pid in unhealthy
        ],
        "count": len(unhealthy)
    }


@app.get("/api/monitor/critical")
async def get_critical_providers(limit: int = 20):
    """Get list of providers with critical issues."""
    pipeline = get_pipeline()

    if not pipeline.metrics:
        raise HTTPException(400, "Metrics not enabled")

    history = pipeline.metrics.get_history(limit=limit)

    if not history:
        return {"critical_providers": [], "count": 0}

    # Assess all providers
    from .pipeline.provider_monitor import ProviderMonitor
    monitor = ProviderMonitor()

    cb_statuses = {}
    if pipeline.circuit_breaker:
        statuses = pipeline.circuit_breaker.get_all_statuses()
        cb_statuses = {
            pid: {"state": s.state.value, "failure_count": s.failure_count}
            for pid, s in statuses.items()
        }

    assessments = monitor.assess_all_providers(history, cb_statuses)
    critical = monitor.get_critical_providers(assessments)

    return {
        "critical_providers": [
            {
                "provider_id": pid,
                "health_score": assessments[pid].health_score.value,
                "score_value": assessments[pid].score_value,
                "critical_issues": [
                    {
                        "type": i.issue_type.value,
                        "message": i.message,
                    }
                    for i in assessments[pid].issues
                    if i.severity == "critical"
                ],
            }
            for pid in critical
        ],
        "count": len(critical)
    }


# ============ WebSocket Progress ============

@app.websocket("/ws/extraction")
async def websocket_extraction_progress(websocket: WebSocket):
    """WebSocket endpoint for real-time extraction progress."""
    await ws_manager.connect(websocket)

    try:
        # Keep connection alive
        while True:
            # Wait for client message (ping)
            data = await websocket.receive_text()

            # Echo back to confirm connection
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ============ Chat with Claude ============

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    system: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = True


async def stream_anthropic_response(system: str, messages: list[dict]):
    """Stream responses from Anthropic API."""
    import httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield 'data: {"content": "Error: ANTHROPIC_API_KEY not set"}\n\n'
        yield 'data: [DONE]\n\n'
        return

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4096,
                    "system": system,
                    "messages": messages,
                    "stream": True,
                },
                timeout=60.0,
            )

            if response.status_code != 200:
                error_text = response.text
                yield f'data: {{"content": "API error: {response.status_code}"}}\n\n'
                yield 'data: [DONE]\n\n'
                return

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        import json
                        event = json.loads(data)
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                yield f'data: {{"content": {json.dumps(text)}}}\n\n'
                    except:
                        pass

            yield 'data: [DONE]\n\n'

        except Exception as e:
            yield f'data: {{"content": "Error: {str(e)}"}}\n\n'
            yield 'data: [DONE]\n\n'


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Chat endpoint with Claude API streaming."""
    system = request.system or "You are a helpful betting analytics assistant."
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    if request.stream:
        return StreamingResponse(
            stream_anthropic_response(system, messages),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    else:
        # Non-streaming response (simplified)
        return {"content": "Streaming is recommended for chat."}


# Entry point for development
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)


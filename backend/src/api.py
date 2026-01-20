"""
OddOpp FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
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

class ProfileUpdate(BaseModel):
    kelly_fraction: Optional[float] = None
    min_edge_pct: Optional[float] = None
    min_arb_pct: Optional[float] = None
    max_stake_pct: Optional[float] = None


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
    return {"success": True, "provider_id": provider_id}


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
    
    return {
        "total_bets": len(bets),
        "wins": win_count,
        "losses": loss_count,
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(total_profit / total_staked * 100, 2) if total_staked > 0 else 0,
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
    db: Session = Depends(get_db)
):
    """Get current arb/value/bonus opportunities."""
    query = db.query(Opportunity)
    
    if type:
        query = query.filter(Opportunity.type == type)
    if active_only:
        query = query.filter(Opportunity.is_active == True)
    
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


# ============ Profile ============

@app.get("/api/profile")
async def get_profile(db: Session = Depends(get_db)):
    """Get user profile settings."""
    profile = db.query(Profile).filter(Profile.name == "default").first()
    
    if not profile:
        # Create default profile
        profile = Profile(name="default")
        db.add(profile)
        db.commit()
    
    return {
        "name": profile.name,
        "kelly_fraction": profile.kelly_fraction,
        "min_edge_pct": profile.min_edge_pct,
        "min_arb_pct": profile.min_arb_pct,
        "max_stake_pct": profile.max_stake_pct,
    }


@app.put("/api/profile")
async def update_profile(data: ProfileUpdate, db: Session = Depends(get_db)):
    """Update user profile settings."""
    profile = db.query(Profile).filter(Profile.name == "default").first()
    
    if not profile:
        profile = Profile(name="default")
        db.add(profile)
    
    if data.kelly_fraction is not None:
        profile.kelly_fraction = data.kelly_fraction
    if data.min_edge_pct is not None:
        profile.min_edge_pct = data.min_edge_pct
    if data.min_arb_pct is not None:
        profile.min_arb_pct = data.min_arb_pct
    if data.max_stake_pct is not None:
        profile.max_stake_pct = data.max_stake_pct
    
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
    
    from .extractors.kambi import get_extractor, KAMBI_PROVIDERS
    import re
    
    extraction_state["running"] = True
    extraction_state["events"] = 0
    extraction_state["odds"] = 0
    
    db = get_session()
    
    try:
        for provider_id in providers:
            if provider_id not in KAMBI_PROVIDERS:
                continue
            
            # Ensure provider exists
            if not db.query(Provider).filter(Provider.id == provider_id).first():
                config = KAMBI_PROVIDERS[provider_id]
                db.add(Provider(id=provider_id, name=provider_id.title(), url=config["domain"], balance=0))
                db.commit()
            
            # Extract
            extractor = get_extractor(provider_id)
            events = await extractor.extract(sport, max_groups=max_groups)
            
            # Store events
            for kambi_event in events:
                # Generate canonical ID
                home = re.sub(r'[^\w\s]', '', kambi_event.home_team.lower().strip())
                away = re.sub(r'[^\w\s]', '', kambi_event.away_team.lower().strip())
                try:
                    date_str = datetime.fromisoformat(kambi_event.start_time.replace('Z', '+00:00')).strftime('%Y%m%d')
                except:
                    date_str = 'unknown'
                canonical_id = f"{kambi_event.sport}:{home}:{away}:{date_str}"
                
                # Upsert event
                event = db.query(Event).filter(Event.id == canonical_id).first()
                if not event:
                    try:
                        start_dt = datetime.fromisoformat(kambi_event.start_time.replace('Z', '+00:00'))
                    except:
                        start_dt = None
                    event = Event(
                        id=canonical_id,
                        sport=kambi_event.sport,
                        league=kambi_event.league,
                        home_team=kambi_event.home_team,
                        away_team=kambi_event.away_team,
                        start_time=start_dt,
                    )
                    db.add(event)
                    extraction_state["events"] += 1
                
                # Store odds
                for market in kambi_event.markets:
                    market_type = market.get('type', '')[:30].lower().replace(' ', '_')
                    for outcome in market.get('outcomes', []):
                        outcome_name = outcome.get('name', '')[:20].lower()
                        odds_value = outcome.get('odds', 0)
                        if odds_value <= 1:
                            continue
                        
                        existing = db.query(Odds).filter(
                            Odds.event_id == canonical_id,
                            Odds.provider_id == provider_id,
                            Odds.market == market_type,
                            Odds.outcome == outcome_name,
                        ).first()
                        
                        if existing:
                            existing.odds = odds_value
                            existing.updated_at = datetime.utcnow()
                        else:
                            db.add(Odds(
                                event_id=canonical_id,
                                provider_id=provider_id,
                                market=market_type,
                                outcome=outcome_name,
                                odds=odds_value,
                            ))
                            extraction_state["odds"] += 1
            
            db.commit()
        
        extraction_state["last_run"] = datetime.utcnow().isoformat()
        
    finally:
        extraction_state["running"] = False
        db.close()


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


# Entry point for development
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)


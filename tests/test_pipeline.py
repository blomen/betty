"""
Integration Test: Kambi Extraction → DB Pipeline

Tests the full flow:
1. Extract events from Kambi (Unibet)
2. Generate canonical event IDs
3. Store events and odds in SQLite
4. Verify data integrity
"""

import asyncio
import logging
import re
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Imports
from src.extractors.kambi import get_extractor, KambiEvent
from src.db.models import init_db, get_session, Event, Odds, Provider


def normalize_team_name(name: str) -> str:
    """
    Normalize team name for canonical ID generation.
    
    - Lowercase
    - Remove common suffixes (FC, IF, AIK, etc.)
    - Remove punctuation
    - Collapse spaces
    """
    if not name:
        return ""
    
    name = name.lower().strip()
    
    # Remove common suffixes
    suffixes = [' fc', ' if', ' aik', ' fk', ' bk', ' sk', ' ff', ' ik']
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    
    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)
    
    # Collapse whitespace
    name = ' '.join(name.split())
    
    return name


def generate_canonical_id(sport: str, home: str, away: str, start_time: str) -> str:
    """
    Generate a canonical event ID for cross-provider matching.
    
    Format: {sport}:{home_normalized}:{away_normalized}:{date}
    """
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)
    
    # Extract date from ISO timestamp
    try:
        dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        date_str = dt.strftime('%Y%m%d')
    except:
        date_str = 'unknown'
    
    return f"{sport}:{home_norm}:{away_norm}:{date_str}"


def kambi_event_to_db(kambi_event: KambiEvent, provider_id: str, session) -> tuple[Event, list[Odds]]:
    """
    Convert a KambiEvent to database models.
    
    Returns (Event, [Odds])
    """
    # Generate canonical ID
    canonical_id = generate_canonical_id(
        sport=kambi_event.sport,
        home=kambi_event.home_team,
        away=kambi_event.away_team,
        start_time=kambi_event.start_time,
    )
    
    # Parse start time
    try:
        start_dt = datetime.fromisoformat(kambi_event.start_time.replace('Z', '+00:00'))
    except:
        start_dt = None
    
    # Create or get Event
    event = session.query(Event).filter(Event.id == canonical_id).first()
    if not event:
        event = Event(
            id=canonical_id,
            sport=kambi_event.sport,
            league=kambi_event.league,
            home_team=kambi_event.home_team,
            away_team=kambi_event.away_team,
            start_time=start_dt,
        )
        session.add(event)
    
    # Create Odds entries
    odds_list = []
    for market in kambi_event.markets:
        market_type = normalize_market_type(market.get('type', ''))
        
        for outcome in market.get('outcomes', []):
            outcome_name = normalize_outcome(outcome.get('name', ''))
            odds_value = outcome.get('odds', 0)
            
            if odds_value <= 1:
                continue
            
            # Check for existing odds (upsert)
            existing = session.query(Odds).filter(
                Odds.event_id == canonical_id,
                Odds.provider_id == provider_id,
                Odds.market == market_type,
                Odds.outcome == outcome_name,
            ).first()
            
            if existing:
                existing.odds = odds_value
                existing.updated_at = datetime.utcnow()
            else:
                odds = Odds(
                    event_id=canonical_id,
                    provider_id=provider_id,
                    market=market_type,
                    outcome=outcome_name,
                    odds=odds_value,
                )
                session.add(odds)
                odds_list.append(odds)
    
    return event, odds_list


def normalize_market_type(market: str) -> str:
    """Normalize market type to standard format."""
    market = market.lower().strip()
    
    # Map common market names
    if 'full time' in market or '1x2' in market or 'match' in market:
        return '1x2'
    if 'over' in market and 'under' in market:
        return 'over_under'
    if 'both teams' in market:
        return 'btts'
    if 'handicap' in market:
        return 'handicap'
    
    return market.replace(' ', '_')[:30]


def normalize_outcome(outcome: str) -> str:
    """Normalize outcome name."""
    outcome = outcome.lower().strip()
    
    # Map common outcomes
    if outcome in ['1', 'home', 'hemma', 'home team']:
        return 'home'
    if outcome in ['x', 'draw', 'oavgjort']:
        return 'draw'
    if outcome in ['2', 'away', 'borta', 'away team']:
        return 'away'
    if 'over' in outcome:
        return 'over'
    if 'under' in outcome:
        return 'under'
    if outcome in ['yes', 'ja']:
        return 'yes'
    if outcome in ['no', 'nej']:
        return 'no'
    
    return outcome[:20]


async def run_extraction_test():
    """Run the full extraction pipeline test."""
    logger.info("=" * 60)
    logger.info("KAMBI EXTRACTION PIPELINE TEST")
    logger.info("=" * 60)
    
    # Initialize database
    logger.info("\n[1/5] Initializing database...")
    init_db()
    session = get_session()
    
    # Ensure provider exists
    provider_id = "unibet"
    provider = session.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        provider = Provider(id=provider_id, name="Unibet", url="unibet.se", balance=0)
        session.add(provider)
        session.commit()
        logger.info(f"    Created provider: {provider_id}")
    else:
        logger.info(f"    Provider exists: {provider_id}")
    
    # Extract from Kambi
    logger.info("\n[2/5] Extracting from Kambi (Unibet football)...")
    extractor = get_extractor("unibet")
    events = await extractor.extract("football", max_groups=5)  # Limit for test
    logger.info(f"    Extracted {len(events)} events")
    
    if not events:
        logger.error("    No events extracted - check network/API")
        return
    
    # Show sample event
    sample = events[0]
    logger.info(f"\n    Sample event: {sample.home_team} vs {sample.away_team}")
    logger.info(f"    League: {sample.league}")
    logger.info(f"    Markets: {len(sample.markets)}")
    
    # Store in database
    logger.info("\n[3/5] Storing in database...")
    events_stored = 0
    odds_stored = 0
    
    for kambi_event in events:
        event, odds_list = kambi_event_to_db(kambi_event, provider_id, session)
        events_stored += 1
        odds_stored += len(odds_list)
    
    session.commit()
    logger.info(f"    Stored {events_stored} events")
    logger.info(f"    Stored {odds_stored} new odds entries")
    
    # Verify data
    logger.info("\n[4/5] Verifying database...")
    total_events = session.query(Event).count()
    total_odds = session.query(Odds).filter(Odds.provider_id == provider_id).count()
    
    logger.info(f"    Total events in DB: {total_events}")
    logger.info(f"    Total odds from {provider_id}: {total_odds}")
    
    # Show sample from DB
    logger.info("\n[5/5] Sample from database:")
    db_event = session.query(Event).first()
    if db_event:
        logger.info(f"    Event ID: {db_event.id}")
        logger.info(f"    {db_event.home_team} vs {db_event.away_team}")
        logger.info(f"    Sport: {db_event.sport}, League: {db_event.league}")
        
        # Get odds for this event
        event_odds = session.query(Odds).filter(Odds.event_id == db_event.id).all()
        for odds in event_odds[:5]:
            logger.info(f"      {odds.market} -> {odds.outcome}: {odds.odds}")
    
    session.close()
    
    logger.info("\n" + "=" * 60)
    logger.info("TEST COMPLETE")
    logger.info("=" * 60)
    
    return {
        "events_extracted": len(events),
        "events_stored": events_stored,
        "odds_stored": odds_stored,
        "total_events_db": total_events,
        "total_odds_db": total_odds,
    }


if __name__ == "__main__":
    results = asyncio.run(run_extraction_test())
    
    if results:
        print(f"\n✅ Pipeline working!")
        print(f"   Events: {results['total_events_db']}")
        print(f"   Odds: {results['total_odds_db']}")

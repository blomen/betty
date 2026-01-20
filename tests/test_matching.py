"""
Test: Polymarket ↔ Kambi Event Matching

Extracts events from both sources, stores in DB with canonical IDs,
and finds matched events to compare odds.
"""

import asyncio
import logging
import re
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Imports
from src.sources.polymarket import PolymarketSource, PolymarketEvent
from src.extractors.kambi import get_extractor, KambiEvent
from src.db.models import init_db, get_session, Event, Odds, Provider
from src.config.sports import SPORTS_CONFIG


# ============ Team Name Parsing ============

def parse_teams_from_title(title: str) -> tuple[str, str] | None:
    """
    Parse home and away teams from Polymarket event title.
    
    Examples:
    - "Heat vs. Bulls" → ("Heat", "Bulls")
    - "Liverpool FC vs. Burnley FC" → ("Liverpool FC", "Burnley FC")
    - "FK Bodø/Glimt vs. Manchester City FC" → ("FK Bodø/Glimt", "Manchester City FC")
    """
    # Handle " - More Markets" suffix
    title = re.sub(r'\s*-\s*More Markets$', '', title)
    
    # Try different separators
    for sep in [' vs. ', ' vs ', ' @ ']:
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts) == 2:
                home = parts[0].strip()
                away = parts[1].strip()
                return (home, away)
    
    return None


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
    suffixes = [' fc', ' if', ' aik', ' fk', ' bk', ' sk', ' ff', ' ik', ' cf', ' ud', ' afc', ' sc']
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    
    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)
    
    # Collapse whitespace
    name = ' '.join(name.split())
    
    return name


def generate_canonical_id(sport: str, home: str, away: str, start_time: datetime | str) -> str:
    """
    Generate a canonical event ID for cross-provider matching.
    
    Format: {sport}:{home_normalized}:{away_normalized}:{date}
    """
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)
    
    # Extract date
    if isinstance(start_time, datetime):
        date_str = start_time.strftime('%Y%m%d')
    elif isinstance(start_time, str):
        try:
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            date_str = dt.strftime('%Y%m%d')
        except:
            date_str = 'unknown'
    else:
        date_str = 'unknown'
    
    return f"{sport}:{home_norm}:{away_norm}:{date_str}"


# ============ Market Normalization ============

def normalize_market_type(market: str) -> str:
    """Normalize market type to standard format."""
    market = market.lower().strip()
    
    if 'full time' in market or '1x2' in market or 'match' in market:
        return '1x2'
    if 'over' in market and 'under' in market:
        return 'over_under'
    if 'will' in market and 'win' in market:
        return '1x2'
    if 'spread' in market or 'handicap' in market:
        return 'spread'
    if 'o/u' in market:
        return 'over_under'
    if 'draw' in market:
        return '1x2'
    
    return market.replace(' ', '_')[:30]


def normalize_outcome(outcome: str, home_team: str = "", away_team: str = "") -> str:
    """Normalize outcome name."""
    outcome = outcome.lower().strip()
    home_norm = normalize_team_name(home_team)
    away_norm = normalize_team_name(away_team)
    
    # Check if outcome matches team name
    outcome_norm = normalize_team_name(outcome)
    if home_norm and outcome_norm == home_norm:
        return 'home'
    if away_norm and outcome_norm == away_norm:
        return 'away'
    
    # Map common outcomes
    if outcome in ['1', 'home', 'hemma', 'home team', 'yes']:
        return 'home'
    if outcome in ['x', 'draw', 'oavgjort']:
        return 'draw'
    if outcome in ['2', 'away', 'borta', 'away team', 'no']:
        return 'away'
    if 'over' in outcome:
        return 'over'
    if 'under' in outcome:
        return 'under'
    
    return outcome[:20]


# ============ Store Functions ============

def store_polymarket_event(event: PolymarketEvent, session) -> tuple[Event | None, int]:
    """
    Store a Polymarket event in the database.
    
    Returns (Event, num_odds_stored)
    """
    # Parse teams from title
    teams = parse_teams_from_title(event.title)
    if not teams:
        return None, 0
    
    home_team, away_team = teams
    
    # Generate canonical ID
    canonical_id = generate_canonical_id(
        sport=event.sport,
        home=home_team,
        away=away_team,
        start_time=event.start_time,
    )
    
    # Create or get Event
    db_event = session.query(Event).filter(Event.id == canonical_id).first()
    if not db_event:
        db_event = Event(
            id=canonical_id,
            sport=event.sport,
            league=event.sport,  # Use sport as league for now
            home_team=home_team,
            away_team=away_team,
            start_time=event.start_time,
        )
        session.add(db_event)
    
    # Store odds
    odds_count = 0
    provider_id = "polymarket"
    
    for market in event.markets:
        if not market.get("is_active"):
            continue
        
        market_type = normalize_market_type(market.get("question", ""))
        outcomes = market.get("outcomes", [])
        odds_values = market.get("decimal_odds", [])
        
        for outcome_name, odds_value in zip(outcomes, odds_values):
            if odds_value <= 1 or odds_value > 100:
                continue
            
            outcome_norm = normalize_outcome(outcome_name, home_team, away_team)
            
            # Upsert odds
            existing = session.query(Odds).filter(
                Odds.event_id == canonical_id,
                Odds.provider_id == provider_id,
                Odds.market == market_type,
                Odds.outcome == outcome_norm,
            ).first()
            
            if existing:
                existing.odds = odds_value
                existing.updated_at = datetime.utcnow()
            else:
                odds = Odds(
                    event_id=canonical_id,
                    provider_id=provider_id,
                    market=market_type,
                    outcome=outcome_norm,
                    odds=odds_value,
                )
                session.add(odds)
                odds_count += 1
    
    return db_event, odds_count


def store_kambi_event(event: KambiEvent, provider_id: str, session) -> tuple[Event, int]:
    """
    Store a Kambi event in the database.
    
    Returns (Event, num_odds_stored)
    """
    # Generate canonical ID
    canonical_id = generate_canonical_id(
        sport=event.sport,
        home=event.home_team,
        away=event.away_team,
        start_time=event.start_time,
    )
    
    # Parse start time
    try:
        start_dt = datetime.fromisoformat(event.start_time.replace('Z', '+00:00'))
    except:
        start_dt = None
    
    # Create or get Event
    db_event = session.query(Event).filter(Event.id == canonical_id).first()
    if not db_event:
        db_event = Event(
            id=canonical_id,
            sport=event.sport,
            league=event.league,
            home_team=event.home_team,
            away_team=event.away_team,
            start_time=start_dt,
        )
        session.add(db_event)
    
    # Store odds
    odds_count = 0
    
    for market in event.markets:
        market_type = normalize_market_type(market.get('type', ''))
        
        for outcome in market.get('outcomes', []):
            outcome_name = normalize_outcome(
                outcome.get('name', ''), 
                event.home_team, 
                event.away_team
            )
            odds_value = outcome.get('odds', 0)
            
            if odds_value <= 1:
                continue
            
            # Upsert odds
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
                odds_count += 1
    
    return db_event, odds_count


# ============ Main Test ============

async def run_matching_test():
    """Run extraction from both sources and find matches."""
    logger.info("=" * 70)
    logger.info("POLYMARKET ↔ KAMBI EVENT MATCHING TEST")
    logger.info("=" * 70)
    
    # Initialize database
    logger.info("\n[1/6] Initializing database...")
    init_db()
    session = get_session()
    
    # Ensure providers exist
    for pid, name in [("polymarket", "Polymarket"), ("unibet", "Unibet")]:
        provider = session.query(Provider).filter(Provider.id == pid).first()
        if not provider:
            provider = Provider(id=pid, name=name, balance=0)
            session.add(provider)
    session.commit()
    
    # Extract from Polymarket
    logger.info("\n[2/6] Extracting from Polymarket...")
    poly_events = []
    async with PolymarketSource() as source:
        # Focus on sports that Kambi also has
        test_sports = [
            s for s in SPORTS_CONFIG 
            if s.kambi_sport in ["football", "basketball", "ice_hockey", "tennis"]
        ][:10]  # Limit for test
        
        for sport in test_sports:
            events = await source.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                limit=20,  # Limit per sport
            )
            poly_events.extend(events)
    
    logger.info(f"    Extracted {len(poly_events)} Polymarket events")
    
    # Store Polymarket events
    logger.info("\n[3/6] Storing Polymarket events...")
    poly_stored = 0
    poly_odds = 0
    for event in poly_events:
        db_event, odds_count = store_polymarket_event(event, session)
        if db_event:
            poly_stored += 1
            poly_odds += odds_count
    session.commit()
    logger.info(f"    Stored {poly_stored} events, {poly_odds} odds")
    
    # Extract from Kambi (Unibet)
    logger.info("\n[4/6] Extracting from Kambi (Unibet)...")
    kambi_events = []
    extractor = get_extractor("unibet")
    
    for kambi_sport in ["football", "basketball", "ice_hockey", "tennis"]:
        try:
            events = await extractor.extract(kambi_sport, max_groups=5)
            kambi_events.extend(events)
        except Exception as e:
            logger.warning(f"    Failed to extract {kambi_sport}: {e}")
    
    logger.info(f"    Extracted {len(kambi_events)} Kambi events")
    
    # Store Kambi events
    logger.info("\n[5/6] Storing Kambi events...")
    kambi_stored = 0
    kambi_odds = 0
    for event in kambi_events:
        db_event, odds_count = store_kambi_event(event, "unibet", session)
        kambi_stored += 1
        kambi_odds += odds_count
    session.commit()
    logger.info(f"    Stored {kambi_stored} events, {kambi_odds} odds")
    
    # Find matched events
    logger.info("\n[6/6] Finding matched events...")
    
    # Events that have odds from BOTH providers
    from sqlalchemy import func
    
    matched_events = session.query(Event).join(Odds).filter(
        Odds.provider_id.in_(["polymarket", "unibet"])
    ).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    ).all()
    
    logger.info(f"    Found {len(matched_events)} events with odds from both providers!")
    
    # Show matched events with odds comparison
    if matched_events:
        logger.info("\n" + "=" * 70)
        logger.info("MATCHED EVENTS WITH ODDS COMPARISON")
        logger.info("=" * 70)
        
        for event in matched_events[:10]:
            logger.info(f"\n  {event.home_team} vs {event.away_team}")
            logger.info(f"  Sport: {event.sport}, Date: {event.start_time}")
            
            # Get odds from each provider
            poly_odds_list = session.query(Odds).filter(
                Odds.event_id == event.id,
                Odds.provider_id == "polymarket"
            ).all()
            
            unibet_odds_list = session.query(Odds).filter(
                Odds.event_id == event.id,
                Odds.provider_id == "unibet"
            ).all()
            
            # Group by market
            poly_by_market = {}
            for o in poly_odds_list:
                poly_by_market.setdefault(o.market, {})[o.outcome] = o.odds
            
            unibet_by_market = {}
            for o in unibet_odds_list:
                unibet_by_market.setdefault(o.market, {})[o.outcome] = o.odds
            
            # Compare
            for market in set(poly_by_market.keys()) & set(unibet_by_market.keys()):
                logger.info(f"\n    Market: {market}")
                for outcome in set(poly_by_market[market].keys()) & set(unibet_by_market[market].keys()):
                    p_odds = poly_by_market[market][outcome]
                    u_odds = unibet_by_market[market][outcome]
                    diff = ((p_odds - u_odds) / u_odds * 100) if u_odds else 0
                    direction = "↑" if diff > 0 else "↓" if diff < 0 else "="
                    logger.info(f"      {outcome}: Poly {p_odds:.2f} vs Unibet {u_odds:.2f} ({direction}{abs(diff):.1f}%)")
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    
    total_events = session.query(Event).count()
    total_poly_odds = session.query(Odds).filter(Odds.provider_id == "polymarket").count()
    total_unibet_odds = session.query(Odds).filter(Odds.provider_id == "unibet").count()
    
    logger.info(f"  Total events in DB: {total_events}")
    logger.info(f"  Polymarket odds: {total_poly_odds}")
    logger.info(f"  Unibet odds: {total_unibet_odds}")
    logger.info(f"  Matched events: {len(matched_events)}")
    
    session.close()
    
    return {
        "poly_events": len(poly_events),
        "kambi_events": len(kambi_events),
        "matched_events": len(matched_events),
    }


if __name__ == "__main__":
    results = asyncio.run(run_matching_test())
    
    if results:
        print(f"\n✅ Matching test complete!")
        print(f"   Polymarket: {results['poly_events']} events")
        print(f"   Kambi: {results['kambi_events']} events")
        print(f"   Matched: {results['matched_events']} events")

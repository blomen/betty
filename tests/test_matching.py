"""
Test: Polymarket ↔ Kambi Event Matching

Extracts events from both sources, stores in DB with canonical IDs,
and finds matched events to compare odds.
"""

import asyncio
import logging
import re
import sys
import os
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.append(os.path.join(str(Path(__file__).parent.parent), "backend"))

# Force UTF-8 output
try:
    sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Imports
from src.factory import get_extractor
from src.extractors.base import StandardEvent
from src.db.models import init_db, get_session, Event, Odds, Provider
from src.factory import ExtractorFactory  # To access sports config if needed, or just iterate generic

# ============ Utils ============

# Build league-to-sport mapping from factory config
_LEAGUE_TO_SPORT = None
def get_kambi_sport(league_or_sport: str) -> str:
    """Map league name (e.g., 'La Liga') to kambi_sport (e.g., 'football')."""
    global _LEAGUE_TO_SPORT
    if _LEAGUE_TO_SPORT is None:
        _LEAGUE_TO_SPORT = {}
        try:
            factory = ExtractorFactory.get_instance()
            for s in factory.sports:
                _LEAGUE_TO_SPORT[s.name.lower()] = s.kambi_sport
        except Exception:
            pass
    return _LEAGUE_TO_SPORT.get(league_or_sport.lower(), league_or_sport.lower())

def normalize_team_name(name: str) -> str:
    from src.utils.matching import normalize_team_name as norm
    return norm(name)

def generate_canonical_id(sport: str, home: str, away: str, start_time: datetime | str) -> str:
    from src.utils.matching import generate_canonical_id as gen_id
    # Wrapper to handle datetime/str logic if needed, but util should handle it
    # The util expects string YYYYMMDD usually if sticking to strict format, or we adapt.
    # Let's import the one from pipeline or reimplement similarity?
    # Actually, let's use the one from pipeline or matching utils directly.
    from src.pipeline import generate_canonical_id as pipe_gen_id
    return pipe_gen_id(sport, home, away, start_time)

def normalize_market_type(market: str) -> str:
    from src.rules.normalization import normalize_market
    return normalize_market(market)

def normalize_outcome(outcome: str, home: str = "", away: str = "") -> str:
    from src.rules.normalization import normalize_outcome as norm_out
    return norm_out(outcome, home, away)


# ============ Store Functions ============

def store_event(event: StandardEvent, provider_id: str, session) -> tuple[Event | None, int]:
    """Store a StandardEvent in default DB schema."""
    # Use pipeline logic simplified
    
    # Parse team names from title if not provided (Polymarket case)
    home = event.home_team
    away = event.away_team
    
    if not home or not away:
        # Try to parse from event.name (title) e.g. "Team A vs Team B"
        import re
        name = getattr(event, 'name', '') or ''
        # Strip "- More Markets" suffix
        name = re.sub(r'\s*-\s*More Markets$', '', name, flags=re.IGNORECASE)
        for sep in [' vs. ', ' vs ', ' @ ']:
            if sep in name:
                parts = name.split(sep, 1)
                if len(parts) == 2:
                    home = parts[0].strip()
                    away = parts[1].strip()
                    break
    
    # If still no teams, skip this event
    if not home or not away:
        return None, 0
    
    # Normalize sport using factory config (La Liga -> football, NBA -> basketball)
    normalized_sport = get_kambi_sport(event.sport)
    
    # 1. Generate Canonical ID
    canon_id = generate_canonical_id(normalized_sport, home, away, event.start_time)
    
    # 2. Check DB
    db_event = session.query(Event).filter(Event.id == canon_id).first()
    is_new = False
    
    if not db_event:
        # Check start time format
        if isinstance(event.start_time, str):
            try:
                start_dt = datetime.fromisoformat(event.start_time.replace('Z', '+00:00'))
            except:
                start_dt = None
        else:
            start_dt = event.start_time
            
        db_event = Event(
            id=canon_id,
            sport=event.sport,
            league=event.league,
            home_team=home,  # Use parsed home, not event.home_team
            away_team=away,  # Use parsed away, not event.away_team
            start_time=start_dt,
        )
        session.add(db_event)
        is_new = True
        
    # 3. Store Odds
    odds_count = 0
    
    for market in event.markets:
        # Polymarket market passed as dict, StandardEvent.markets is list of dicts
        m_type = normalize_market_type(market.get('type') or market.get('question', ''))
        
        for outcome in market.get('outcomes', []):
            o_name = normalize_outcome(outcome.get('name', ''), home, away)  # Use parsed names
            o_odds = outcome.get('odds', 0)
            
            if o_odds <= 1 or o_odds > 100: continue
            
            # Upsert
            existing = session.query(Odds).filter(
                Odds.event_id == canon_id,
                Odds.provider_id == provider_id,
                Odds.market == m_type,
                Odds.outcome == o_name
            ).first()
            
            if existing:
                existing.odds = o_odds
                existing.updated_at = datetime.utcnow()
            else:
                session.add(Odds(
                    event_id=canon_id,
                    provider_id=provider_id,
                    market=m_type,
                    outcome=o_name,
                    odds=o_odds
                ))
                odds_count += 1
                
    return db_event, odds_count

# ============ Main Test ============

async def run_matching_test():
    logger.info("=" * 70)
    logger.info("POLYMARKET ↔ KAMBI EVENT MATCHING TEST")
    logger.info("=" * 70)
    
    logger.info("\n[1/5] Initializing database...")
    init_db()
    session = get_session()
    
    # Ensure providers
    for pid, name in [("polymarket", "Polymarket"), ("unibet", "Unibet")]:
        if not session.query(Provider).filter(Provider.id == pid).first():
            session.add(Provider(id=pid, name=name))
    session.commit()
    
    # Extract Poly
    logger.info("\n[2/5] Extracting & Storing Polymarket...")
    poly_extractor = get_extractor("polymarket")
    
    # Use factory sports to know what to fetch
    factory = ExtractorFactory.get_instance()
    # Filter for main sports
    # Specific prioritized list for testing
    priority_leagues = ["La Liga", "Premier League", "NBA", "NHL"]
    target_sports = [s.name for s in factory.sports if s.name in priority_leagues]
    
    poly_count = 0
    poly_odds = 0
    
    async with poly_extractor:
        for sport_name in target_sports: # Iterate all prioritized
            try:
                events = await poly_extractor.extract(sport_name, limit=20)
                for ev in events:
                    _, o_cnt = store_event(ev, "polymarket", session)
                    poly_count += 1
                    poly_odds += o_cnt
                session.commit() # Commit per sport
            except Exception as e:
                logger.warning(f"Poly extract {sport_name} failed: {e}")

    logger.info(f"    Saved {poly_count} events, {poly_odds} odds")
    
    # Extract Kambi
    logger.info("\n[3/5] Extracting & Storing Kambi (Unibet)...")
    kambi_extractor = get_extractor("unibet")
    
    kambi_count = 0
    kambi_odds = 0
    
    # We use kambi_sports
    kambi_targets = list(set([s.kambi_sport for s in factory.sports if s.name in target_sports]))
    
    for k_sport in kambi_targets:
        try:
            # Kambi extractor usually takes sport name (e.g. "football")
            # Note: The APIExtractor.extract_kambi uses `_kambi_match_sport` logic
            events = await kambi_extractor.extract(k_sport, limit=50) 
            for ev in events:
                 _, o_cnt = store_event(ev, "unibet", session)
                 kambi_count += 1
                 kambi_odds += o_cnt
            session.commit()
        except Exception as e:
            logger.warning(f"Kambi extract {k_sport} failed: {e}")
            
    logger.info(f"    Saved {kambi_count} events, {kambi_odds} odds")
    
    # Match Analysis
    from sqlalchemy import func
    
    matched_events = session.query(Event).join(Odds).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    ).all()
    
    logger.info("\n" + "=" * 70)
    logger.info(f"MATCH RESULTS: {len(matched_events)} matched events")
    logger.info("=" * 70)
    
    # Check specifically for Athletic Club match if possible
    athletic_matches = [e for e in matched_events if "athletic" in e.home_team or "athletic" in e.away_team]
    if athletic_matches:
        logger.info(f"Found {len(athletic_matches)} 'Athletic' matches:")
        for e in athletic_matches:
            logger.info(f"  {e.home_team} vs {e.away_team} ({e.id})")
            
    # List a few others
    for e in matched_events[:5]:
         logger.info(f"  [MATCH] {e.sport}: {e.home_team} vs {e.away_team}")

    session.close()
    return len(matched_events)

if __name__ == "__main__":
    asyncio.run(run_matching_test())


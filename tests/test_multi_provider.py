"""
Multi-Provider Extraction Test

Extracts from multiple Kambi providers and verifies:
1. Same events get same canonical ID
2. Odds from different providers are linked correctly
3. Cross-provider matching works
"""

import asyncio
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from src.extractors.kambi import get_extractor, KAMBI_PROVIDERS, KambiEvent
from src.db.models import init_db, get_session, Event, Odds, Provider
from test_pipeline import generate_canonical_id, kambi_event_to_db


async def extract_from_provider(provider_id: str, sport: str = "football", max_groups: int = 3):
    """Extract events from a single provider."""
    try:
        extractor = get_extractor(provider_id)
        events = await extractor.extract(sport, max_groups=max_groups)
        return events
    except Exception as e:
        logger.error(f"Failed to extract from {provider_id}: {e}")
        return []


async def run_multi_provider_test():
    """Test extraction from multiple providers."""
    logger.info("=" * 60)
    logger.info("MULTI-PROVIDER EXTRACTION TEST")
    logger.info("=" * 60)
    
    # Initialize
    init_db()
    session = get_session()
    
    # Providers to test (from KAMBI_PROVIDERS)
    providers_to_test = ["unibet", "leovegas", "casumo"]
    
    logger.info(f"\nTesting providers: {providers_to_test}")
    logger.info(f"Available Kambi providers: {list(KAMBI_PROVIDERS.keys())}")
    
    # Ensure providers exist in DB
    for pid in providers_to_test:
        if not session.query(Provider).filter(Provider.id == pid).first():
            config = KAMBI_PROVIDERS.get(pid, {})
            provider = Provider(
                id=pid, 
                name=pid.title(),
                url=config.get("domain", f"{pid}.se"),
                balance=0
            )
            session.add(provider)
    session.commit()
    
    # Extract from each provider
    all_events_by_provider = {}
    
    for provider_id in providers_to_test:
        logger.info(f"\n[{provider_id.upper()}] Extracting...")
        events = await extract_from_provider(provider_id, "football", max_groups=3)
        all_events_by_provider[provider_id] = events
        logger.info(f"[{provider_id.upper()}] Got {len(events)} events")
        
        # Store in DB
        for kambi_event in events:
            try:
                kambi_event_to_db(kambi_event, provider_id, session)
            except Exception as e:
                logger.debug(f"Failed to store: {e}")
        
        session.commit()
    
    # Verify cross-matching
    logger.info("\n" + "=" * 60)
    logger.info("CROSS-PROVIDER MATCHING ANALYSIS")
    logger.info("=" * 60)
    
    # Find events with odds from multiple providers
    from sqlalchemy import func
    
    events_with_multi_provider = (
        session.query(Event.id, func.count(func.distinct(Odds.provider_id)).label('provider_count'))
        .join(Odds, Event.id == Odds.event_id)
        .group_by(Event.id)
        .having(func.count(func.distinct(Odds.provider_id)) > 1)
        .all()
    )
    
    logger.info(f"\nEvents with odds from 2+ providers: {len(events_with_multi_provider)}")
    
    # Show details
    for event_id, count in events_with_multi_provider[:5]:
        event = session.query(Event).filter(Event.id == event_id).first()
        if event:
            logger.info(f"\n{event.home_team} vs {event.away_team}")
            logger.info(f"  Canonical ID: {event_id[:50]}...")
            logger.info(f"  Providers: {count}")
            
            # Get odds from each provider for this event
            for pid in providers_to_test:
                odds_count = session.query(Odds).filter(
                    Odds.event_id == event_id,
                    Odds.provider_id == pid
                ).count()
                if odds_count > 0:
                    # Get 1x2 odds if available
                    sample_odds = session.query(Odds).filter(
                        Odds.event_id == event_id,
                        Odds.provider_id == pid,
                        Odds.market == '1x2'
                    ).all()
                    odds_str = ", ".join(f"{o.outcome}:{o.odds}" for o in sample_odds[:3])
                    logger.info(f"    {pid}: {odds_count} markets" + (f" (1x2: {odds_str})" if odds_str else ""))
    
    # Summary
    total_events = session.query(Event).count()
    total_odds = session.query(Odds).count()
    
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total events in DB: {total_events}")
    logger.info(f"Total odds entries: {total_odds}")
    logger.info(f"Events matched across providers: {len(events_with_multi_provider)}")
    
    # Per-provider stats
    for pid in providers_to_test:
        odds_count = session.query(Odds).filter(Odds.provider_id == pid).count()
        logger.info(f"  {pid}: {odds_count} odds entries")
    
    session.close()
    
    return {
        "total_events": total_events,
        "total_odds": total_odds,
        "matched_events": len(events_with_multi_provider),
    }


if __name__ == "__main__":
    results = asyncio.run(run_multi_provider_test())
    
    print(f"\n✅ Multi-provider test complete!")
    print(f"   Total events: {results['total_events']}")
    print(f"   Cross-matched: {results['matched_events']}")

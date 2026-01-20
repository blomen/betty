import asyncio
import sys
import os
import logging
from sqlalchemy import func

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db, get_session, Event, Odds

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_polymarket")

async def main():
    logger.info("Initializing DB...")
    init_db()
    session = get_session()
    
    # Clear existing data for clean test? 
    # Maybe better to just check if we get *new* data or just existing data is fine.
    # Let's verify we can extract something.
    
    logger.info("Initializing Pipeline...")
    pipeline = ExtractionPipeline(session)
    
    # FILTER SPORTS FOR SPEED
    all_sports = pipeline.engine.sports
    pipeline.engine.sports = [s for s in all_sports if s.name in ["NBA", "Premier League"]]
    logger.info(f"Filtered sports to: {[s.name for s in pipeline.engine.sports]}")
    
    logger.info("Running Polymarket Extraction (Limit 5)...")
    results = await pipeline.run(
        polymarket=True,
        providers=[], # No other providers
        max_events_per_sport=5
    )
    
    logger.info(f"Extraction complete. Results: {results}")
    
    # Verify DB contents
    events_count = session.query(Event).filter(Event.id.like("%polymarket%")).count() # IDs might not have 'polymarket' if canonical
    # Actually canonical IDs are sport:home:away:date.
    
    # Let's check for events with odds from polymarket
    poly_odds_count = session.query(Odds).filter(Odds.provider_id == 'polymarket').count()
    
    logger.info(f"Polymarket Odds in DB: {poly_odds_count}")
    
    if poly_odds_count > 0:
        # Check normalization
        sample_odds = session.query(Odds).filter(Odds.provider_id == 'polymarket').limit(5).all()
        logger.info("Sample Normalized Odds:")
        for odd in sample_odds:
            logger.info(f"  Event: {odd.event_id} | Market: {odd.market} | Outcome: {odd.outcome} | Price: {odd.odds}")
            
            # Basic validation
            if odd.market not in ['1x2', 'spread', 'over_under', 'moneyline']:
                # It might be other things, but let's see what we get
                pass
                
            if odd.outcome in ['home', 'away', 'draw']:
                logger.info("    -> Transformation looks correct (home/away/draw)")
    else:
        logger.error("No odds found! Extraction might have failed.")

if __name__ == "__main__":
    asyncio.run(main())

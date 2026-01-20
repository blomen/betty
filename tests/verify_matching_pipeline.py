import asyncio
import sys
import os
import logging
import pandas as pd
from sqlalchemy import create_engine

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db, get_session, Event, Odds

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_matching")

# Silence noisy loggers
logging.getLogger("backend.src.providers.spectate").setLevel(logging.WARNING)

async def main():
    logger.info("Initializing Pipeline...")
    pipeline = ExtractionPipeline()
    
    # FILTER FOR SPEED: Just specific sports
    target_sports = ["Premier League", "NBA"]
    all_sports = pipeline.engine.sports
    pipeline.engine.sports = [s for s in all_sports if s.name in target_sports]
    logger.info(f"Targeting Sports: {[s.name for s in pipeline.engine.sports]}")
    
    # Run Pipeline: Polymarket + Unibet
    logger.info("Running Extraction (Polymarket + Unibet)...")
    results = await pipeline.run(
        polymarket=True,
        providers=["unibet"], # Explicitly request Unibet
        max_events_per_sport=10
    )
    
    logger.info(f"Pipeline finished. Results: {results}")
    
    # CHECK MATCHES
    matches = pipeline.get_matched_events(limit=20)
    logger.info(f"Found {len(matches)} matched events.")
    
    if len(matches) > 0:
        print("\n=== SAMPLE MATCHES ===")
        for m in matches:
            print(f"\n[MATCH] {m['sport'].upper()} | {m['home_team']} vs {m['away_team']} ({m['start_time']})")
            for prov, odds_list in m['providers'].items():
                # Show first 2 odds per provider
                print(f"  {prov}: {len(odds_list)} outcomes")
                for o in odds_list[:2]:
                    print(f"    - {o['market']} {o['outcome']} @ {o['odds']}")
    else:
        logger.warning("No matches found! Checking potential issues...")
        
        # Debug: Dump Unibet events to see why they didn't match
        engine = create_engine(f"sqlite:///{pipeline.session.bind.url.database}")
        unibet_events = pd.read_sql("SELECT * FROM odds WHERE provider_id='unibet'", engine)
        if unibet_events.empty:
            logger.error("Unibet returned NO odds. Extraction failed or no data found.")
        else:
            logger.info(f"Unibet has {len(unibet_events)} odds rows. Issue is likely matching.")
            # Show some normalized names
            events = pd.read_sql("SELECT id, home_team, away_team FROM events WHERE id LIKE ':unibet:%'", engine)
            print(events.head())

if __name__ == "__main__":
    asyncio.run(main())
